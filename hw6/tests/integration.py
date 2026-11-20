import base64
import json
import time
import unittest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
from cassandra import ConsistencyLevel
from cassandra.query import SimpleStatement
from confluent_kafka import Consumer, TopicPartition
from prometheus_client.parser import text_string_to_metric_families

from app.common import BROKERS, DLQ, GROUP, REGISTRY, TOPIC, Schemas, connect, json_dump
from app.state import State

WMS = "http://wms:8000"
CONSUMER = "http://consumer:8000"


def make_event(kind="PRODUCT_RECEIVED", product=None, stamp=None, **extra):
    return {"event_id": str(uuid4()), "event_type": kind,
            "timestamp": (stamp or datetime.now(timezone.utc)).isoformat(),
            "product_id": product or f"TEST-{uuid4().hex}", "zone_id": "ZONE-A",
            "quantity": 100, **extra}


def send(payload, version=2):
    response = httpx.post(f"{WMS}/events", params={"version": version}, json=payload, timeout=35)
    response.raise_for_status()
    return response.json()


def wait_event(event_id, timeout=75):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            response = httpx.get(f"{CONSUMER}/events/{event_id}", timeout=15)
        except httpx.TransportError:
            time.sleep(0.3)
            continue
        response.raise_for_status()
        row = response.json()
        if row and row["status"] in ("APPLIED", "IGNORED"):
            return row
        time.sleep(0.3)
    raise AssertionError(f"Не обработано событие {event_id}")


def inventory(product):
    response = httpx.get(f"{CONSUMER}/inventory/{product}", timeout=15)
    response.raise_for_status()
    return response.json()


def dlq_reader():
    reader = Consumer({"bootstrap.servers": BROKERS, "group.id": str(uuid4()),
                       "enable.auto.commit": False, "auto.offset.reset": "earliest"})
    positions = [TopicPartition(DLQ, n) for n in range(3)]
    for position in positions:
        _, position.offset = reader.get_watermark_offsets(position, timeout=10)
    reader.assign(positions)
    return reader


def wait_dlq(reader, event_id):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        message = reader.poll(1)
        if message is None or message.error():
            continue
        data = json.loads(message.value())
        if data["original_event"] and data["original_event"]["event_id"] == event_id:
            return data
    raise AssertionError(f"Нет события {event_id} в DLQ")


def wait_committed(published, timeout=30):
    with ConsumerGuard() as reader:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            position = TopicPartition(TOPIC, published["partition"])
            if reader.committed([position], timeout=5)[0].offset > published["offset"]:
                return
            time.sleep(0.2)
    raise AssertionError(f"Offset не подтверждён: {published}")


def metric_sum(text, name):
    return sum(sample.value for family in text_string_to_metric_families(text)
               for sample in family.samples if sample.name == name)


class WarehouseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cluster, cls.session = connect()
        cls.state = State(cls.session)

    @classmethod
    def tearDownClass(cls):
        cls.cluster.shutdown()

    def assert_projections(self, product):
        result = inventory(product)
        for zone in result["zones"]:
            projected = self.state.execute(
                "SELECT * FROM warehouse.inventory_by_zone WHERE warehouse_id=? AND zone_id=? AND product_id=?",
                ("main", zone["zone_id"], product),
            ).one()
            for field in ("warehouse_id", "product_id", "zone_id", "available_quantity", "reserved_quantity", "supplier_id"):
                self.assertEqual(projected[field], zone[field])
            self.assertEqual(projected["last_event_at"], datetime.fromisoformat(zone["last_event_at"]))
        self.assertEqual(result["total"]["total_available"], sum(r["available_quantity"] for r in result["zones"]))
        self.assertEqual(result["total"]["total_reserved"], sum(r["reserved_quantity"] for r in result["zones"]))
        return result

    def test_warehouse_operations_and_projections(self):
        product = f"TEST-{uuid4().hex}"
        order = f"ORDER-{uuid4().hex}"
        first = datetime.now(timezone.utc) - timedelta(minutes=1)
        operations = [
            ("PRODUCT_RECEIVED", {"quantity": 100}, (100, 0, 100, 0)),
            ("PRODUCT_RESERVED", {"quantity": 30}, (70, 30, 70, 30)),
            ("PRODUCT_MOVED", {"quantity": 20, "from_zone_id": "ZONE-A", "to_zone_id": "ZONE-B"}, (50, 30, 70, 30)),
            ("PRODUCT_SHIPPED", {"quantity": 10}, (40, 30, 60, 30)),
            ("ORDER_CREATED", {"order_id": order, "items": [{"product_id": product, "zone_id": "ZONE-A", "quantity": 15}]}, (25, 45, 45, 45)),
            ("ORDER_COMPLETED", {"order_id": order}, (25, 30, 45, 30)),
            ("PRODUCT_RELEASED", {"quantity": 10}, (35, 20, 55, 20)),
            ("INVENTORY_COUNTED", {"counted_quantity": 80}, (80, 20, 100, 20)),
        ]
        for index, (kind, fields, expected) in enumerate(operations):
            record = make_event(kind, product, first + timedelta(seconds=index), **fields)
            send(record)
            self.assertEqual(wait_event(record["event_id"])["status"], "APPLIED")
            result = self.assert_projections(product)
            zone = next(row for row in result["zones"] if row["zone_id"] == "ZONE-A")
            total = result["total"]
            self.assertEqual((zone["available_quantity"], zone["reserved_quantity"],
                              total["total_available"], total["total_reserved"]), expected)
            duplicate = send(record)
            wait_committed(duplicate)
            self.assertEqual(inventory(product), result)
            if kind.startswith("ORDER_"):
                self.assertEqual(self.state.order("main", order)["status"],
                                 "CREATED" if kind == "ORDER_CREATED" else "COMPLETED")
        self.assertEqual(self.state.order("main", order)["status"], "COMPLETED")

    def test_order_with_multiple_products(self):
        base = datetime.now(timezone.utc) - timedelta(minutes=1)
        products = [f"TEST-{uuid4().hex}", f"TEST-{uuid4().hex}"]
        for i, product in enumerate(products):
            record = make_event(product=product, stamp=base + timedelta(seconds=i), quantity=100)
            send(record)
            wait_event(record["event_id"])
        order_id = f"ORDER-{uuid4().hex}"
        created = make_event("ORDER_CREATED", stamp=base + timedelta(seconds=2), order_id=order_id,
                             items=[{"product_id": p, "zone_id": "ZONE-A", "quantity": q}
                                    for p, q in zip(products, (10, 20))])
        send(created)
        wait_event(created["event_id"])
        for product, quantity in zip(products, (10, 20)):
            total = inventory(product)["total"]
            self.assertEqual((total["total_available"], total["total_reserved"]), (100 - quantity, quantity))
        completed = make_event("ORDER_COMPLETED", stamp=base + timedelta(seconds=3), order_id=order_id)
        send(completed)
        wait_event(completed["event_id"])
        for product, quantity in zip(products, (10, 20)):
            total = inventory(product)["total"]
            self.assertEqual((total["total_available"], total["total_reserved"]), (100 - quantity, 0))

    def test_duplicate_old_event_and_recovery_plan(self):
        first = datetime.now(timezone.utc) - timedelta(minutes=10)
        initial = make_event(stamp=first)
        product = initial["product_id"]
        for _ in range(2):
            send(initial)
        wait_event(initial["event_id"])
        self.assertEqual(inventory(product)["total"]["total_available"], 100)
        shipped = make_event("PRODUCT_SHIPPED", product, first + timedelta(minutes=5), quantity=20)
        send(shipped)
        wait_event(shipped["event_id"])
        old = make_event(product=product, stamp=first + timedelta(minutes=2), quantity=50)
        send(old)
        self.assertEqual(wait_event(old["event_id"])["status"], "IGNORED")
        self.assertEqual(inventory(product)["total"]["total_available"], 80)
        equal_time = make_event(product=product, stamp=first + timedelta(minutes=5), quantity=50)
        send(equal_time)
        self.assertEqual(wait_event(equal_time["event_id"])["status"], "IGNORED")
        self.assertEqual(self.assert_projections(product)["total"]["total_available"], 80)

        fifty = make_event(quantity=50)
        send(fifty)
        wait_event(fifty["event_id"])
        wait_committed(send(fifty))
        self.assertEqual(self.assert_projections(fifty["product_id"])["total"]["total_available"], 50)

        prepared = make_event()
        schema = Schemas()
        encoded, _ = schema.encode(prepared)
        decoded, _ = schema.decode(encoded)
        plan = self.state.load_plan(decoded, 2)
        self.assertEqual(plan["status"], "PENDING")
        send(prepared)
        wait_event(prepared["event_id"])
        self.state.apply(plan)
        self.assertEqual(inventory(prepared["product_id"])["total"]["total_available"], 100)
        wait_committed(send(prepared))
        self.assertEqual(inventory(prepared["product_id"])["total"]["total_available"], 100)

    def test_dlq_and_next_valid_event(self):
        reader = dlq_reader()
        bad = make_event("PRODUCT_SHIPPED", quantity=-5)
        sent = send(bad)
        try:
            found = wait_dlq(reader, bad["event_id"])
            schemas = Schemas()
            wire, _ = schemas.encode(bad)
            decoded, _ = schemas.decode(wire)
            self.assertEqual(found["original_event"], json.loads(json_dump(decoded)))
            self.assertEqual(base64.b64decode(found["original_bytes_base64"]), wire)
            self.assertEqual(found["original_event"]["quantity"], -5)
            self.assertIn("положительным", found["error_reason"])
            self.assertEqual(found["error_code"], "VALIDATION_ERROR")
            self.assertIsNotNone(datetime.fromisoformat(found["failed_at"]).utcoffset())
            self.assertEqual(found["kafka_metadata"]["topic"], TOPIC)
            self.assertEqual(found["kafka_metadata"]["offset"], sent["offset"])
            self.assertEqual(found["kafka_metadata"]["partition"], sent["partition"])
            self.assertTrue(found["original_bytes_base64"])
            valid = make_event()
            send(valid)
            wait_event(valid["event_id"])
            self.assertEqual(inventory(valid["product_id"])["total"]["total_available"], 100)
        finally:
            reader.close()

    def test_insufficient_stock_is_rejected_atomically(self):
        base = datetime.now(timezone.utc) - timedelta(minutes=1)
        initial = make_event(stamp=base)
        send(initial)
        wait_event(initial["event_id"])
        product = initial["product_id"]
        before = self.assert_projections(product)
        order = f"ORDER-{uuid4().hex}"
        invalid = [
            ("PRODUCT_SHIPPED", {"quantity": 101}),
            ("PRODUCT_RESERVED", {"quantity": 101}),
            ("PRODUCT_MOVED", {"quantity": 101, "from_zone_id": "ZONE-A", "to_zone_id": "ZONE-B"}),
            ("PRODUCT_RELEASED", {"quantity": 1}),
            ("ORDER_CREATED", {"order_id": order, "items": [
                {"product_id": product, "zone_id": "ZONE-A", "quantity": 60},
                {"product_id": product, "zone_id": "ZONE-A", "quantity": 41},
            ]}),
            ("ORDER_COMPLETED", {"order_id": order}),
        ]
        reader = dlq_reader()
        try:
            for i, (kind, fields) in enumerate(invalid, start=1):
                bad = make_event(kind, product, base + timedelta(seconds=i), **fields)
                published = send(bad)
                wait_dlq(reader, bad["event_id"])
                wait_committed(published)
                self.assertEqual(self.assert_projections(product), before)
                self.assertIsNone(self.state.order("main", order))
                self.assertIsNone(self.state.event("main", bad["event_id"]))
        finally:
            reader.close()

    def test_quantity_overflow_goes_to_dlq(self):
        base = datetime.now(timezone.utc) - timedelta(minutes=1)
        initial = make_event(quantity=2**31 - 1, stamp=base)
        send(initial)
        wait_event(initial["event_id"])
        reader = dlq_reader()
        try:
            for i, zone in enumerate(("ZONE-A", "ZONE-B"), start=1):
                bad = make_event(product=initial["product_id"], stamp=base + timedelta(seconds=i),
                                 zone_id=zone, quantity=1)
                published = send(bad)
                self.assertIn("диапазон", wait_dlq(reader, bad["event_id"])["error_reason"])
                wait_committed(published)
                self.assertIsNone(self.state.event("main", bad["event_id"]))
                self.assertEqual(self.assert_projections(initial["product_id"])["total"]["total_available"], 2**31 - 1)
            following = make_event("PRODUCT_SHIPPED", initial["product_id"], base + timedelta(seconds=3), quantity=1)
            send(following)
            wait_event(following["event_id"])
            self.assertEqual(self.assert_projections(initial["product_id"])["total"]["total_available"], 2**31 - 2)
        finally:
            reader.close()

    def test_schema_evolution_and_registry(self):
        versions = httpx.get(f"{REGISTRY}/subjects/{TOPIC}-value/versions", timeout=10)
        self.assertEqual(versions.json(), [1, 2])
        for version in (1, 2, 1):
            extra = {"supplier_id": "SUP-001"} if version == 2 else {}
            event = make_event(**extra)
            send(event, version)
            recorded = wait_event(event["event_id"])
            expected = "SUP-001" if version == 2 else None
            self.assertEqual(recorded["supplier_id"], expected)
            self.assertEqual(recorded["schema_version"], version)
            self.assertEqual(inventory(event["product_id"])["zones"][0]["supplier_id"], expected)
        self.assertEqual(httpx.get(f"{REGISTRY}/config/{TOPIC}-value").json()["compatibilityLevel"], "BACKWARD")

    def test_monitoring_and_health(self):
        self.assertEqual(httpx.get(f"{CONSUMER}/health", timeout=15).status_code, 200)
        metric = httpx.get(f"{CONSUMER}/metrics", timeout=10).text
        for name in ("consumer_lag", "events_processed_total", "event_processing_duration_seconds",
                     "cassandra_write_errors_total"):
            self.assertIn(name, metric)
        demo = httpx.post(f"{WMS}/demo", timeout=35)
        demo.raise_for_status()
        wait_event(demo.json()["events"][-1]["event_id"])
        base = datetime.now(timezone.utc)
        record = make_event(stamp=base)
        send(record)
        shipped = make_event("PRODUCT_SHIPPED", record["product_id"], base + timedelta(seconds=1), quantity=1)
        published = send(shipped)
        wait_event(shipped["event_id"])
        wait_committed(published)
        updated = httpx.get(f"{CONSUMER}/metrics", timeout=10).text
        self.assertGreaterEqual(metric_sum(updated, "events_processed_total"), metric_sum(metric, "events_processed_total") + 10)
        self.assertGreaterEqual(metric_sum(updated, "event_processing_duration_seconds_count"),
                                metric_sum(metric, "event_processing_duration_seconds_count") + 10)
        self.assertGreater(metric_sum(updated, "event_processing_duration_seconds_sum"), 0)
        self.assertGreaterEqual(metric_sum(updated, "cassandra_write_errors_total"), 0)
        response = httpx.get("http://grafana:3000/api/dashboards/uid/warehouse", timeout=10)
        response.raise_for_status()
        panels = response.json()["dashboard"]["panels"]
        self.assertGreaterEqual(len(panels), 3)
        self.assertEqual({p["datasource"]["uid"] for p in panels}, {"warehouse-prometheus"})
        self.assertEqual(httpx.get("http://prometheus:9090/-/ready", timeout=10).status_code, 200)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            response = httpx.get(
                "http://grafana:3000/api/datasources/proxy/uid/warehouse-prometheus/api/v1/query",
                params={"query": 'sum(events_processed_total{job="warehouse-consumer"})'}, timeout=10,
            )
            response.raise_for_status()
            values = response.json()["data"]["result"]
            if values and float(values[0]["value"][1]) > 0:
                break
            time.sleep(1)
        else:
            self.fail("Grafana не получила реальные метрики consumer из Prometheus")
        with ConsumerGuard() as reader:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                partitions = [TopicPartition(TOPIC, p) for p in range(3)]
                committed = reader.committed(partitions, timeout=5)
                lag = sum(reader.get_watermark_offsets(p, timeout=5)[1] - max(p.offset, 0) for p in committed)
                if lag == 0:
                    break
                time.sleep(1)
            self.assertEqual(lag, 0)

    def test_cassandra_replication_and_levels(self):
        row = self.session.execute("SELECT replication FROM system_schema.keyspaces WHERE keyspace_name='warehouse'").one()
        self.assertIn("NetworkTopologyStrategy", row["replication"]["class"])
        self.assertEqual(row["replication"]["dc1"], "3")
        self.assertEqual(len(self.cluster.metadata.all_hosts()), 3)
        for level in (ConsistencyLevel.ONE, ConsistencyLevel.QUORUM, ConsistencyLevel.ALL):
            self.session.execute(SimpleStatement("SELECT * FROM warehouse.zones WHERE warehouse_id='main'",
                                                 consistency_level=level))


class ConsumerGuard:
    def __enter__(self):
        self.reader = Consumer({"bootstrap.servers": BROKERS, "group.id": GROUP, "enable.auto.commit": False})
        return self.reader

    def __exit__(self, *args):
        self.reader.close()


if __name__ == "__main__":
    unittest.main()
