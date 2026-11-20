import asyncio
import threading
from contextlib import asynccontextmanager
from uuid import uuid4
from datetime import timedelta

from fastapi import Body, FastAPI, HTTPException, Query

from app.common import LOG, TOPIC, Schemas, configure_logging, now_utc, producer, publish
from app.monitoring import metrics, start_lag_monitor


@asynccontextmanager
async def lifespan(app):
    configure_logging()
    app.state.schemas = Schemas()
    app.state.producer = producer()
    stop = threading.Event()
    worker = start_lag_monitor(stop)
    try:
        yield
    finally:
        stop.set()
        await asyncio.to_thread(worker.join, 5)


app = FastAPI(title="Warehouse WMS", lifespan=lifespan)
app.get("/metrics")(metrics)


@app.get("/health")
def health():
    try:
        app.state.producer.list_topics(TOPIC, timeout=3)
        return {"status": "ok"}
    except Exception as exc:
        raise HTTPException(503, "Kafka недоступна") from exc


@app.post("/events", status_code=201)
def event(payload: dict = Body(...), version: int = Query(2, ge=1, le=2)):
    try:
        value, record = app.state.schemas.encode(payload, version)
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(422, str(exc)) from exc
    try:
        message = publish(app.state.producer, TOPIC, record["warehouse_id"].encode(), value)
    except (RuntimeError, BufferError) as exc:
        raise HTTPException(503, "Kafka не подтвердила публикацию") from exc
    LOG.info("published event_id=%s event_type=%s partition=%s offset=%s",
             record["event_id"], record["event_type"], message.partition(), message.offset())
    return {"event_id": record["event_id"], "partition": message.partition(), "offset": message.offset(), "schema_version": version}


@app.post("/demo")
def demo():
    product = f"DEMO-{uuid4().hex[:8]}"
    order = f"ORDER-{uuid4().hex[:8]}"
    base = now_utc()
    results = []
    operations = [
        ("PRODUCT_RECEIVED", {"quantity": 100}),
        ("PRODUCT_RESERVED", {"quantity": 30}),
        ("PRODUCT_MOVED", {"quantity": 20, "from_zone_id": "ZONE-A", "to_zone_id": "ZONE-B"}),
        ("PRODUCT_SHIPPED", {"quantity": 10}),
        ("PRODUCT_RELEASED", {"quantity": 5}),
        ("INVENTORY_COUNTED", {"counted_quantity": 80}),
        ("ORDER_CREATED", {"order_id": order, "items": [{"product_id": product, "zone_id": "ZONE-A", "quantity": 15}]}),
        ("ORDER_COMPLETED", {"order_id": order}),
    ]
    for index, (kind, extra) in enumerate(operations):
        record = {"event_id": str(uuid4()), "event_type": kind, "timestamp": (base + timedelta(seconds=index)).isoformat(),
                  "product_id": product, "zone_id": "ZONE-A", **extra}
        results.append(event(record, 2))
    return {"product_id": product, "order_id": order, "events": results}
