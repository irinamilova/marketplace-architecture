import json
import time
from datetime import timezone
from uuid import UUID

from cassandra import ConsistencyLevel, DriverException
from cassandra.cluster import NoHostAvailable
from cassandra.query import BatchStatement, BatchType

from app.common import json_dump
from app.monitoring import WRITE_ERRORS


class DomainError(ValueError):
    pass


class State:
    def __init__(self, session):
        self.session = session
        self.prepared = {}

    def execute(self, sql, values=()):
        if sql not in self.prepared:
            self.prepared[sql] = self.session.prepare(sql)
        try:
            return self.session.execute(self.prepared[sql], values)
        except (DriverException, NoHostAvailable):
            if sql.startswith(("INSERT", "UPDATE", "DELETE")):
                WRITE_ERRORS.inc()
            raise

    def inventory(self, warehouse, product):
        return list(self.execute(
            "SELECT * FROM warehouse.inventory_by_product_zone WHERE warehouse_id=? AND product_id=?",
            (warehouse, product),
        ))

    def total(self, warehouse, product):
        return self.execute(
            "SELECT * FROM warehouse.inventory_by_product WHERE warehouse_id=? AND product_id=?",
            (warehouse, product),
        ).one()

    def order(self, warehouse, order):
        return self.execute("SELECT * FROM warehouse.orders WHERE warehouse_id=? AND order_id=?",
                            (warehouse, order)).one()

    def event(self, warehouse, event_id):
        return self.execute(
            "SELECT * FROM warehouse.processed_events WHERE warehouse_id=? AND event_id=?",
            (warehouse, UUID(str(event_id))),
        ).one()

    def prepare(self, event):
        warehouse = event["warehouse_id"]
        kind = event["event_type"]
        stamp = event["timestamp"]
        millis = int(stamp.timestamp() * 1000)
        order = None
        items = []
        if kind in ("ORDER_CREATED", "ORDER_COMPLETED"):
            if not event["order_id"]:
                raise DomainError("Для заказа нужен order_id")
            order = self.order(warehouse, event["order_id"])
            if order and int(order["last_event_at"].replace(tzinfo=timezone.utc).timestamp() * 1000) >= millis:
                return {"ignored": True}
            if kind == "ORDER_CREATED":
                if order:
                    raise DomainError("Заказ уже существует")
                items = event["items"]
                if not items:
                    raise DomainError("В заказе нет позиций")
            else:
                if not order or order["status"] != "CREATED":
                    raise DomainError("Завершить можно только существующий CREATED заказ")
                items = json.loads(order["items_json"])
        else:
            if not event["product_id"]:
                raise DomainError("Нужен product_id")
            zones = [event["from_zone_id"], event["to_zone_id"]] if kind == "PRODUCT_MOVED" else [event["zone_id"]]
            if any(not zone for zone in zones):
                raise DomainError("Нужны идентификаторы зон")
            if kind == "PRODUCT_MOVED" and zones[0] == zones[1]:
                raise DomainError("Зоны перемещения должны отличаться")
            if kind == "INVENTORY_COUNTED":
                if event["counted_quantity"] is None or event["counted_quantity"] < 0:
                    raise DomainError("counted_quantity должен быть неотрицательным")
            elif event["quantity"] <= 0:
                raise DomainError("quantity должен быть положительным")
            items = [{"product_id": event["product_id"], "zone_id": zone, "quantity": event["quantity"]}
                     for zone in zones]

        products = sorted({item["product_id"] for item in items})
        if any(not item["product_id"] or not item["zone_id"] or item["quantity"] <= 0 for item in items) and kind != "INVENTORY_COUNTED":
            raise DomainError("Неверная позиция заказа")
        stocks = {product: {row["zone_id"]: dict(row) for row in self.inventory(warehouse, product)}
                  for product in products}
        for product in products:
            total = self.total(warehouse, product)
            if total and total["last_event_at"].replace(tzinfo=timezone.utc) >= stamp:
                return {"ignored": True}

        affected = set()
        def adjust(product, zone, available=0, reserved=0, counted=None):
            row = stocks[product].setdefault(zone, {
                "warehouse_id": warehouse, "product_id": product, "zone_id": zone,
                "available_quantity": 0, "reserved_quantity": 0, "supplier_id": None,
            })
            row["available_quantity"] = counted if counted is not None else row["available_quantity"] + available
            row["reserved_quantity"] += reserved
            if min(row["available_quantity"], row["reserved_quantity"]) < 0:
                raise DomainError(f"Недостаточно доступного остатка или резерва: {product}/{zone}")
            row["last_event_at"] = millis
            if kind == "PRODUCT_RECEIVED":
                row["supplier_id"] = event["supplier_id"]
            affected.add((product, zone))

        if kind == "ORDER_CREATED":
            for item in items:
                adjust(item["product_id"], item["zone_id"], -item["quantity"], item["quantity"])
        elif kind == "ORDER_COMPLETED":
            for item in items:
                adjust(item["product_id"], item["zone_id"], reserved=-item["quantity"])
        else:
            product, zone, quantity = event["product_id"], event["zone_id"], event["quantity"]
            if kind == "PRODUCT_RECEIVED":
                adjust(product, zone, available=quantity)
            elif kind == "PRODUCT_SHIPPED":
                adjust(product, zone, available=-quantity)
            elif kind == "PRODUCT_MOVED":
                adjust(product, event["from_zone_id"], available=-quantity)
                adjust(product, event["to_zone_id"], available=quantity)
            elif kind == "PRODUCT_RESERVED":
                adjust(product, zone, -quantity, quantity)
            elif kind == "PRODUCT_RELEASED":
                adjust(product, zone, quantity, -quantity)
            elif kind == "INVENTORY_COUNTED":
                adjust(product, zone, counted=event["counted_quantity"])
        totals = [
            {"product_id": product,
             "available": sum(row["available_quantity"] for row in stocks[product].values()),
             "reserved": sum(row["reserved_quantity"] for row in stocks[product].values())}
            for product in products
        ]
        if any(max(total["available"], total["reserved"]) > 2**31 - 1 for total in totals):
            raise DomainError("Остаток превышает диапазон Cassandra int")
        return {
            "ignored": False,
            "stocks": [stocks[product][zone] for product, zone in sorted(affected)],
            "totals": totals,
            "order": {"order_id": event["order_id"], "status": "CREATED" if kind == "ORDER_CREATED" else "COMPLETED",
                      "items_json": json_dump(items)} if kind.startswith("ORDER_") else None,
            "event_millis": millis,
        }

    def load_plan(self, event, version):
        existing = self.event(event["warehouse_id"], event["event_id"])
        if existing:
            if json.loads(existing["original_json"]) != json.loads(json_dump(event)):
                raise DomainError("event_id уже использован с другим содержимым")
            return existing
        plan = self.prepare(event)
        timestamp = max(time.time_ns() // 1000, int(event["timestamp"].timestamp() * 1_000_000))
        self.execute(
            "INSERT INTO warehouse.processed_events "
            "(warehouse_id,event_id,status,plan_json,original_json,event_type,event_at,write_timestamp,schema_version,supplier_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?) IF NOT EXISTS",
            (event["warehouse_id"], UUID(event["event_id"]), "PENDING", json_dump(plan), json_dump(event),
             event["event_type"], event["timestamp"], timestamp, version, event["supplier_id"]),
        )
        return self.event(event["warehouse_id"], event["event_id"])

    def apply(self, record):
        if record["status"] != "PENDING":
            return "duplicate"
        plan = json.loads(record["plan_json"])
        warehouse = record["warehouse_id"]
        timestamp = record["write_timestamp"]
        if not plan["ignored"]:
            batch = BatchStatement(batch_type=BatchType.LOGGED, consistency_level=ConsistencyLevel.QUORUM)
            def insert(table, columns, values):
                sql = f"INSERT INTO warehouse.{table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)}) USING TIMESTAMP ?"
                if sql not in self.prepared:
                    self.prepared[sql] = self.session.prepare(sql)
                batch.add(self.prepared[sql], (*values, timestamp))
            for row in plan["stocks"]:
                insert("inventory_by_product_zone",
                       ["warehouse_id", "product_id", "zone_id", "available_quantity", "reserved_quantity", "last_event_at", "supplier_id"],
                       [warehouse, row["product_id"], row["zone_id"], row["available_quantity"], row["reserved_quantity"], row["last_event_at"], row["supplier_id"]])
                insert("inventory_by_zone",
                       ["warehouse_id", "zone_id", "product_id", "available_quantity", "reserved_quantity", "last_event_at", "supplier_id"],
                       [warehouse, row["zone_id"], row["product_id"], row["available_quantity"], row["reserved_quantity"], row["last_event_at"], row["supplier_id"]])
            for total in plan["totals"]:
                insert("inventory_by_product",
                       ["warehouse_id", "product_id", "total_available", "total_reserved", "last_event_at"],
                       [warehouse, total["product_id"], total["available"], total["reserved"], plan["event_millis"]])
            if plan["order"]:
                order = plan["order"]
                insert("orders", ["warehouse_id", "order_id", "status", "items_json", "last_event_at"],
                       [warehouse, order["order_id"], order["status"], order["items_json"], plan["event_millis"]])
            try:
                self.session.execute(batch)
            except (DriverException, NoHostAvailable):
                WRITE_ERRORS.inc()
                raise
        self.execute(
            "UPDATE warehouse.processed_events SET status=? WHERE warehouse_id=? AND event_id=?",
            ("IGNORED" if plan["ignored"] else "APPLIED", warehouse, record["event_id"]),
        )
        return "ignored" if plan["ignored"] else "processed"
