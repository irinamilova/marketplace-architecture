import time
from pathlib import Path

import httpx
from botocore.exceptions import ClientError
from confluent_kafka import KafkaError, KafkaException
from confluent_kafka.admin import AdminClient, ConfigResource, NewTopic

from app.common import (
    BROKERS, BUCKET, LOG, REGISTRY, ROOT, SUBJECT, TOPIC,
    clickhouse, configure_logging, postgres, s3,
)


def prepare_topic():
    admin = AdminClient({"bootstrap.servers": BROKERS})
    topic = NewTopic(TOPIC, 3, 3, config={"min.insync.replicas": "2"})
    try:
        admin.create_topics([topic], operation_timeout=30, request_timeout=40)[TOPIC].result()
    except KafkaException as exc:
        if exc.args[0].code() != KafkaError.TOPIC_ALREADY_EXISTS:
            raise
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        metadata = admin.list_topics(TOPIC, timeout=5).topics.get(TOPIC)
        if metadata and not metadata.error and len(metadata.partitions) == 3 and all(
            len(part.replicas) == 3 and part.leader >= 0 and len(part.isrs) >= 2
            for part in metadata.partitions.values()
        ):
            resource = ConfigResource("topic", TOPIC)
            config = admin.describe_configs([resource], request_timeout=10)[resource].result()
            if config["min.insync.replicas"].value != "2":
                raise RuntimeError("Для movie-events нужен min.insync.replicas=2")
            return
        time.sleep(1)
    raise RuntimeError("Топик movie-events не готов или имеет неверную конфигурацию")


def prepare_schemas():
    with httpx.Client(base_url=REGISTRY, timeout=15) as client:
        client.put(f"/config/{SUBJECT}", json={"compatibility": "BACKWARD"}).raise_for_status()
        for schema in sorted((ROOT / "schemas").glob("movie-event.v*.avsc")):
            body = {"schemaType": "AVRO", "schema": schema.read_text()}
            client.post(f"/subjects/{SUBJECT}/versions", json=body).raise_for_status()
            lookup = client.post(f"/subjects/{SUBJECT}", json=body)
            lookup.raise_for_status()
            metadata = lookup.json()
            if metadata["version"] != int(schema.stem.rsplit(".v", 1)[1]):
                raise RuntimeError(f"Версия схемы не соответствует файлу {schema.name}")
            LOG.info("%s version=%s id=%s", SUBJECT, metadata["version"], metadata["id"])


def migrate(directory: Path):
    for file in sorted(directory.glob("*_clickhouse.sql")):
        for sql in file.read_text().split(";"):
            if sql.strip():
                clickhouse(sql)
    with postgres() as conn:
        for file in sorted(directory.glob("*_postgres.sql")):
            conn.execute(file.read_text())


def prepare_bucket():
    client = s3()
    try:
        client.head_bucket(Bucket=BUCKET)
    except ClientError as exc:
        if exc.response["ResponseMetadata"]["HTTPStatusCode"] != 404:
            raise
        client.create_bucket(Bucket=BUCKET)


if __name__ == "__main__":
    configure_logging()
    prepare_topic()
    prepare_schemas()
    migrate(ROOT / "sql")
    prepare_bucket()
    LOG.info("Топик, схемы, миграции и бакет готовы")
