import re
import time

import httpx
from confluent_kafka import KafkaError, KafkaException
from confluent_kafka.admin import AdminClient, NewTopic

from app.common import BROKERS, DLQ, LOG, REGISTRY, ROOT, TOPIC, configure_logging, connect


def bootstrap():
    admin = AdminClient({"bootstrap.servers": BROKERS})
    for topic in (TOPIC, DLQ):
        try:
            admin.create_topics([NewTopic(topic, 3, 1)], request_timeout=30)[topic].result()
        except KafkaException as exc:
            if exc.args[0].code() != KafkaError.TOPIC_ALREADY_EXISTS:
                raise
    with httpx.Client(base_url=REGISTRY, timeout=15) as client:
        subject = f"{TOPIC}-value"
        client.put(f"/config/{subject}", json={"compatibility": "BACKWARD"}).raise_for_status()
        for version in (1, 2):
            schema = (ROOT / f"schemas/warehouse-event.v{version}.avsc").read_text()
            if version == 2:
                response = client.post(f"/compatibility/subjects/{subject}/versions/latest",
                                       json={"schemaType": "AVRO", "schema": schema})
                response.raise_for_status()
                assert response.json()["is_compatible"], "V2 несовместима с V1"
            response = client.post(f"/subjects/{subject}/versions",
                                   json={"schemaType": "AVRO", "schema": schema})
            response.raise_for_status()
            registered = client.post(f"/subjects/{subject}", json={"schema": schema})
            registered.raise_for_status()
            assert registered.json()["version"] == version
            LOG.info("schema version=%s id=%s", version, response.json()["id"])

    cluster, session = connect()
    try:
        deadline = time.monotonic() + 150
        while sum(host.is_up for host in cluster.metadata.all_hosts()) < 3:
            if time.monotonic() >= deadline:
                raise RuntimeError("Ожидались три доступные ноды Cassandra")
            time.sleep(2)
        for migration in sorted((ROOT / "migrations").glob("*.cql")):
            for sql in migration.read_text().split(";"):
                if not sql.strip():
                    continue
                altered = re.search(r"ALTER TABLE warehouse\.(\w+) ADD (\w+)", sql)
                if altered:
                    exists = session.execute(
                        "SELECT column_name FROM system_schema.columns WHERE keyspace_name=%s AND table_name=%s",
                        ("warehouse", altered[1]),
                    )
                    if altered[2] in {row["column_name"] for row in exists}:
                        continue
                session.execute(sql)
            LOG.info("migration=%s applied", migration.name)
    finally:
        cluster.shutdown()


if __name__ == "__main__":
    configure_logging()
    bootstrap()
