import io
import json
import logging
import os
import struct
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import httpx
from cassandra import ConsistencyLevel
from cassandra.cluster import Cluster, ExecutionProfile, EXEC_PROFILE_DEFAULT
from cassandra.policies import DCAwareRoundRobinPolicy, TokenAwarePolicy
from cassandra.query import dict_factory
from confluent_kafka import Consumer, Producer, TopicPartition
from fastavro import parse_schema, schemaless_reader, schemaless_writer
from fastavro.validation import validate

ROOT = Path(__file__).resolve().parents[1]
BROKERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
REGISTRY = os.getenv("SCHEMA_REGISTRY_URL", "http://schema-registry:8081")
GROUP = "warehouse-state-consumer"
TOPIC = "warehouse-events"
DLQ = "warehouse-events-dlq"
LOG = logging.getLogger("warehouse")


def configure_logging():
    logging.basicConfig(level=logging.INFO, format="%(asctime)sZ %(levelname)s %(name)s %(message)s")
    logging.Formatter.converter = time.gmtime
    logging.getLogger("httpx").setLevel(logging.WARNING)


def now_utc():
    return datetime.now(timezone.utc)


def iso(value):
    return value.replace(tzinfo=timezone.utc).isoformat() if isinstance(value, datetime) else value


def json_dump(value):
    return json.dumps(value, default=iso, ensure_ascii=False, allow_nan=False)


def connect():
    cluster = Cluster(
        os.getenv("CASSANDRA_HOSTS", "cassandra-1,cassandra-2,cassandra-3").split(","),
        execution_profiles={EXEC_PROFILE_DEFAULT: ExecutionProfile(
            load_balancing_policy=TokenAwarePolicy(DCAwareRoundRobinPolicy(local_dc="dc1")),
            consistency_level=ConsistencyLevel.QUORUM,
            serial_consistency_level=ConsistencyLevel.SERIAL,
            request_timeout=8,
            row_factory=dict_factory,
        )},
        protocol_version=4,
        connect_timeout=5,
    )
    session = cluster.connect()
    return cluster, session


class Schemas:
    def __init__(self):
        self.schemas = {}
        self.ids = {}
        with httpx.Client(base_url=REGISTRY, timeout=10) as client:
            for version in (1, 2):
                result = client.get(f"/subjects/{TOPIC}-value/versions/{version}")
                result.raise_for_status()
                result = result.json()
                self.schemas[version] = parse_schema(json.loads(result["schema"]))
                self.ids[result["id"]] = version

    def encode(self, event, version=2):
        schema = self.schemas[version]
        allowed = {field["name"] for field in schema["fields"]}
        if set(event) - allowed:
            raise ValueError(f"Неизвестные поля V{version}: {sorted(set(event) - allowed)}")
        record = dict(event)
        record["event_id"] = str(UUID(str(record["event_id"])))
        timestamp = record["timestamp"]
        if isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if not isinstance(timestamp, datetime) or timestamp.utcoffset() is None:
            raise ValueError("timestamp должен содержать часовой пояс")
        record["timestamp"] = timestamp.astimezone(timezone.utc)
        for field in schema["fields"]:
            if field["name"] not in record and "default" in field:
                record[field["name"]] = field["default"]
        validate(record, schema, strict=True)
        schema_id = next(key for key, value in self.ids.items() if value == version)
        buffer = io.BytesIO()
        buffer.write(struct.pack(">bI", 0, schema_id))
        schemaless_writer(buffer, schema, record)
        return buffer.getvalue(), record

    def decode(self, value):
        if len(value) < 5 or value[0] != 0:
            raise ValueError("Неверный AvroConfluent заголовок")
        schema_id = struct.unpack(">I", value[1:5])[0]
        version = self.ids.get(schema_id)
        if version is None:
            raise ValueError(f"Неподдерживаемый schema_id={schema_id}")
        record = schemaless_reader(io.BytesIO(value[5:]), self.schemas[version])
        if version == 1:
            record["supplier_id"] = None
        elif version == 2:
            record["supplier_id"] = record.get("supplier_id")
        record["event_id"] = str(record["event_id"])
        return record, version


def producer():
    return Producer({"bootstrap.servers": BROKERS, "acks": "all", "enable.idempotence": True,
                     "message.timeout.ms": 6000, "retry.backoff.ms": 200})


def publish(client, topic, key, value):
    for attempt in range(3):
        result = []
        try:
            client.produce(topic, key=key, value=value, on_delivery=lambda error, msg: result.append((error, msg)))
            while not result:
                client.poll(0.1)
            error, message = result[0]
            if error:
                raise RuntimeError(str(error))
            return message
        except (RuntimeError, BufferError):
            LOG.warning("publish retry=%s topic=%s", attempt + 1, topic, exc_info=True)
            if attempt == 2:
                raise
            time.sleep(0.25 * 2 ** attempt)


def lag_reader():
    return Consumer({"bootstrap.servers": BROKERS, "group.id": GROUP, "enable.auto.commit": False,
                     "enable.auto.offset.store": False})


def committed_lag(reader):
    topic = reader.list_topics(TOPIC, timeout=3).topics[TOPIC]
    partitions = [TopicPartition(TOPIC, p) for p in topic.partitions]
    committed = reader.committed(partitions, timeout=3)
    return {p.partition: max(0, reader.get_watermark_offsets(p, timeout=3)[1] - max(p.offset, 0))
            for p in committed}
