import asyncio
import io
import json
import os
import struct
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from enum import StrEnum
from threading import Event
from uuid import UUID

import httpx
from confluent_kafka import KafkaException, Producer
from fastapi import FastAPI, HTTPException, Request
from fastavro import parse_schema, schemaless_writer
from fastavro.validation import ValidationError, validate
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.common import BROKERS, LOG, REGISTRY, ROOT, SUBJECT, TOPIC, configure_logging, retry
from app.generator import history, live


class EventType(StrEnum):
    VIEW_STARTED = "VIEW_STARTED"
    VIEW_FINISHED = "VIEW_FINISHED"
    VIEW_PAUSED = "VIEW_PAUSED"
    VIEW_RESUMED = "VIEW_RESUMED"
    LIKED = "LIKED"
    SEARCHED = "SEARCHED"


class DeviceType(StrEnum):
    MOBILE = "MOBILE"
    DESKTOP = "DESKTOP"
    TV = "TV"
    TABLET = "TABLET"


class MovieEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    event_id: UUID
    user_id: str = Field(min_length=1, max_length=256)
    movie_id: str = Field(min_length=1, max_length=256)
    event_type: EventType
    timestamp: datetime
    device_type: DeviceType
    session_id: str = Field(min_length=1, max_length=256)
    progress_seconds: int = Field(strict=True, ge=0, le=2**31 - 1)

    @field_validator("timestamp", mode="before")
    @classmethod
    def parse_timestamp(cls, value):
        if not isinstance(value, (str, datetime)):
            raise ValueError("timestamp должен быть ISO 8601")
        return value

    @field_validator("timestamp")
    @classmethod
    def utc_timestamp(cls, value):
        if value.utcoffset() is None:
            raise ValueError("timestamp должен содержать часовой пояс")
        return value.astimezone(timezone.utc)


class Publisher:
    def __init__(self):
        raw_schema = (ROOT / "schemas/movie-event.v1.avsc").read_text()
        response = httpx.post(
            f"{REGISTRY}/subjects/{SUBJECT}",
            json={"schemaType": "AVRO", "schema": raw_schema},
            timeout=10,
        )
        response.raise_for_status()
        self.schema_id = response.json()["id"]
        self.schema = parse_schema(json.loads(raw_schema))
        self.client = Producer({
            "bootstrap.servers": BROKERS,
            "acks": "all",
            "enable.idempotence": True,
            "linger.ms": 0,
            "message.timeout.ms": 7000,
            "retry.backoff.ms": 200,
            "retry.backoff.max.ms": 1500,
        })

    def publish(self, event: MovieEvent):
        record = event.model_dump(mode="python")
        record.update(event_id=str(event.event_id), event_type=event.event_type.value,
                      device_type=event.device_type.value)
        validate(record, self.schema, strict=True)
        buffer = io.BytesIO(struct.pack(">bI", 0, self.schema_id))
        buffer.seek(5)
        schemaless_writer(buffer, self.schema, record)
        value = buffer.getvalue()

        def send():
            done = Event()
            result = []

            def delivered(error, message):
                result.append((error, message))
                done.set()

            self.client.produce(TOPIC, key=event.user_id.encode(), value=value, on_delivery=delivered)
            while not done.is_set():
                self.client.poll(0.1)
            error, message = result[0]
            if error is not None:
                raise KafkaException(error)
            return message

        message = retry("Kafka publish", send, (KafkaException, BufferError), attempts=3)
        LOG.info("published event_id=%s event_type=%s timestamp=%s partition=%d offset=%d",
                 event.event_id, event.event_type, event.timestamp.isoformat(),
                 message.partition(), message.offset())


async def generate(publisher, interval):
    def publish(records):
        for record in records:
            publisher.publish(MovieEvent.model_validate(record))

    try:
        await asyncio.to_thread(publish, history())
    except (KafkaException, BufferError):
        LOG.exception("Не удалось полностью загрузить исторические демо-события")
    while True:
        try:
            await asyncio.to_thread(publish, live())
        except (KafkaException, BufferError):
            LOG.exception("Ошибка публикации демо-сессии")
        await asyncio.sleep(interval)


@asynccontextmanager
async def lifespan(app):
    configure_logging()
    publisher = Publisher()
    app.state.publisher = publisher
    interval = float(os.getenv("DEMO_INTERVAL_SECONDS", "8"))
    task = asyncio.create_task(generate(publisher, interval)) if interval > 0 else None
    try:
        yield
    finally:
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


app = FastAPI(title="Movie Service", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/events", status_code=201)
def add_event(event: MovieEvent, request: Request):
    try:
        request.app.state.publisher.publish(event)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (KafkaException, BufferError) as exc:
        LOG.exception("Публикация не подтверждена event_id=%s", event.event_id)
        raise HTTPException(status_code=503, detail="Kafka не подтвердила запись") from exc
    return {"event_id": str(event.event_id)}
