import asyncio
import base64
import struct
import threading
from contextlib import asynccontextmanager
from uuid import UUID

from cassandra import DriverException
from cassandra.cluster import NoHostAvailable
from confluent_kafka import Consumer
from fastapi import FastAPI, HTTPException
from fastavro.validation import ValidationError

from app.common import BROKERS, DLQ, GROUP, LOG, TOPIC, Schemas, configure_logging, connect, json_dump, now_utc, producer, publish
from app.monitoring import DURATION, PROCESSED, metrics, start_lag_monitor
from app.state import DomainError, State


class Runtime:
    def __init__(self):
        self.cluster, self.session = connect()
        self.state = State(self.session)
        self.schemas = Schemas()
        self.stop = threading.Event()
        self.writer = producer()
        self.reader = Consumer({
            "bootstrap.servers": BROKERS, "group.id": GROUP,
            "enable.auto.commit": False, "enable.auto.offset.store": False,
            "auto.offset.reset": "earliest", "max.poll.interval.ms": 86400000,
        })
        self.reader.subscribe([TOPIC])
        self.worker = threading.Thread(target=self.run, daemon=True)
        self.worker.start()

    def dead_letter(self, message, error, original):
        payload = {
            "original_event": original,
            "original_bytes_base64": base64.b64encode(message.value() or b"").decode(),
            "error_reason": str(error), "error_code": "VALIDATION_ERROR",
            "failed_at": now_utc().isoformat(),
            "kafka_metadata": {"topic": TOPIC, "partition": message.partition(), "offset": message.offset()},
        }
        publish(self.writer, DLQ, message.key(), json_dump(payload).encode())
        LOG.warning("DLQ event_id=%s event_type=%s partition=%s offset=%s reason=%s",
                    (original or {}).get("event_id"), (original or {}).get("event_type"),
                    message.partition(), message.offset(), error)

    def process(self, message):
        original = None
        try:
            original, version = self.schemas.decode(message.value() or b"")
            if message.key() != original["warehouse_id"].encode():
                raise DomainError("Ключ Kafka должен совпадать с warehouse_id")
            UUID(original["event_id"])
            record = self.state.load_plan(original, version)
        except (DomainError, ValueError, KeyError, TypeError, EOFError, IndexError, struct.error, ValidationError) as exc:
            self.dead_letter(message, exc, original)
            return
        result = self.state.apply(record)
        if result == "processed":
            PROCESSED.labels(original["event_type"]).inc()
        LOG.info("processed event_id=%s event_type=%s offset=%s partition=%s result=%s",
                 original["event_id"], original["event_type"], message.offset(), message.partition(), result)

    def run(self):
        try:
            while not self.stop.is_set():
                message = self.reader.poll(1)
                if message is None:
                    continue
                if message.error():
                    LOG.warning("Kafka read: %s", message.error())
                    continue
                while not self.stop.is_set():
                    try:
                        with DURATION.time():
                            self.process(message)
                        self.reader.commit(message=message, asynchronous=False)
                        break
                    except (DriverException, NoHostAvailable, RuntimeError) as exc:
                        LOG.warning("Событие не подтверждено, повтор после сбоя: %s", exc)
                        self.stop.wait(2)
                    except Exception:
                        LOG.exception("Непредвиденная ошибка; offset не подтверждён")
                        self.stop.wait(2)
        finally:
            self.reader.close()

    def health(self):
        if not self.worker.is_alive():
            raise RuntimeError("Consumer loop остановлен")
        metadata = self.writer.list_topics(TOPIC, timeout=3)
        if metadata.topics[TOPIC].error or any(p.leader < 0 for p in metadata.topics[TOPIC].partitions.values()):
            raise RuntimeError("Kafka недоступна")
        self.session.execute("SELECT zone_id FROM warehouse.zones WHERE warehouse_id=%s", ("main",))


@asynccontextmanager
async def lifespan(app):
    configure_logging()
    runtime = Runtime()
    app.state.runtime = runtime
    monitor = start_lag_monitor(runtime.stop)
    try:
        yield
    finally:
        runtime.stop.set()
        await asyncio.to_thread(runtime.worker.join, 12)
        await asyncio.to_thread(monitor.join, 5)
        runtime.cluster.shutdown()


app = FastAPI(title="Warehouse Consumer", lifespan=lifespan)
app.get("/metrics")(metrics)


@app.get("/health")
def health():
    try:
        app.state.runtime.health()
        return {"status": "ok"}
    except Exception as exc:
        raise HTTPException(503, "Kafka или Cassandra недоступна") from exc


@app.get("/inventory/{product_id}")
def inventory(product_id: str, warehouse_id: str = "main"):
    state = app.state.runtime.state
    return {"total": state.total(warehouse_id, product_id),
            "zones": state.inventory(warehouse_id, product_id)}


@app.get("/zones/{zone_id}")
def zone(zone_id: str, warehouse_id: str = "main"):
    return list(app.state.runtime.state.execute(
        "SELECT * FROM warehouse.inventory_by_zone WHERE warehouse_id=? AND zone_id=?",
        (warehouse_id, zone_id),
    ))


@app.get("/orders/{order_id}")
def order(order_id: str, warehouse_id: str = "main"):
    return app.state.runtime.state.order(warehouse_id, order_id)


@app.get("/events/{event_id}")
def event(event_id: UUID, warehouse_id: str = "main"):
    return app.state.runtime.state.event(warehouse_id, event_id)
