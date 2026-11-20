import json
import sys
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
from cassandra import ConsistencyLevel, DriverException
from cassandra.cluster import NoHostAvailable
from cassandra.query import SimpleStatement
from confluent_kafka import TopicPartition
from confluent_kafka.admin import AdminClient, AlterConfigOpType, ConfigEntry, ConfigResource, ResourceType

from app.common import BROKERS, DLQ, GROUP, TOPIC, committed_lag, connect, lag_reader
from app.monitoring import metrics
from app.state import State
from tests.integration import CONSUMER, dlq_reader, inventory, make_event, metric_sum, send, wait_committed, wait_dlq, wait_event

PROM = "http://prometheus:9090"


def blocked_dlq():
    admin = AdminClient({"bootstrap.servers": BROKERS})
    resource = ConfigResource(ResourceType.TOPIC, DLQ)
    original = admin.describe_configs([resource])[resource].result(timeout=15)["max.message.bytes"]
    def configure(value, operation=AlterConfigOpType.SET):
        resource = ConfigResource(ResourceType.TOPIC, DLQ, incremental_configs=[
            ConfigEntry("max.message.bytes", value, incremental_operation=operation),
        ])
        admin.incremental_alter_configs([resource])[resource].result(timeout=15)
    reader, offsets = dlq_reader(), lag_reader()
    bad = make_event("PRODUCT_SHIPPED", quantity=-5)
    try:
        try:
            configure("128")
            time.sleep(2)
            published = send(bad)
            time.sleep(8)
            position = TopicPartition(TOPIC, published["partition"])
            assert offsets.committed([position], timeout=5)[0].offset <= published["offset"]
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                message = reader.poll(1)
                if message and not message.error():
                    data = json.loads(message.value())
                    assert (data.get("original_event") or {}).get("event_id") != bad["event_id"]
            assert httpx.get(f"{CONSUMER}/health", timeout=15).status_code == 200
        finally:
            configure(original.value, AlterConfigOpType.DELETE if original.is_default else AlterConfigOpType.SET)
        wait_dlq(reader, bad["event_id"])
        wait_committed(published)
        valid = make_event()
        send(valid)
        wait_event(valid["event_id"])
        assert inventory(valid["product_id"])["total"]["total_available"] == 100
        print("Нет ACK DLQ: offset не подтверждён; после восстановления DLQ и следующее событие обработаны", flush=True)
    finally:
        reader.close()
        offsets.close()


def seed_node_failure():
    record = make_event(quantity=200, stamp=datetime.now(timezone.utc) - timedelta(minutes=1))
    send(record)
    wait_event(record["event_id"])
    assert inventory(record["product_id"])["total"]["total_available"] == 200
    print(json.dumps(record), flush=True)


def one_node_down():
    cluster, session = connect()
    try:
        for level in (ConsistencyLevel.ONE, ConsistencyLevel.QUORUM):
            session.execute(SimpleStatement("SELECT * FROM warehouse.zones WHERE warehouse_id='main'",
                                            consistency_level=level))
        try:
            session.execute(SimpleStatement("SELECT * FROM warehouse.zones WHERE warehouse_id='main'",
                                            consistency_level=ConsistencyLevel.ALL))
        except (DriverException, NoHostAvailable):
            pass
        else:
            raise AssertionError("ALL не должна работать без одной реплики")
        received = json.loads(sys.argv[2])
        base = datetime.fromisoformat(received["timestamp"])
        shipped = make_event("PRODUCT_SHIPPED", received["product_id"], base + timedelta(seconds=1), quantity=50)
        send(shipped)
        wait_event(shipped["event_id"])
        assert inventory(received["product_id"])["total"]["total_available"] == 150
        assert httpx.get(f"{CONSUMER}/health", timeout=20).status_code == 200
        print("ONE/QUORUM: OK; ALL: отказ; обработка 200-50=150 без cassandra-2", flush=True)
    finally:
        cluster.shutdown()


def stopped_consumer():
    product = f"LAG-{uuid4().hex}"
    base = datetime.now(timezone.utc) - timedelta(minutes=1)
    records = [make_event(product=product, stamp=base + timedelta(seconds=i), quantity=1) for i in range(10)]
    for record in records:
        send(record)
    reader = lag_reader()
    try:
        assert sum(committed_lag(reader).values()) >= 10
    finally:
        reader.close()
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        alerts = httpx.get(f"{PROM}/api/v1/alerts", timeout=10).json()["data"]["alerts"]
        if any(a["labels"]["alertname"] == "WarehouseConsumerLag" and a["state"] == "firing" for a in alerts):
            print("Consumer остановлен: lag >=10; WarehouseConsumerLag firing", flush=True)
            print(records[-1]["event_id"], flush=True)
            return
        time.sleep(2)
    raise AssertionError("Алерт lag не сработал")


def recovered_consumer():
    reader = lag_reader()
    try:
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if sum(committed_lag(reader).values()) == 0:
                break
            time.sleep(2)
        else:
            raise AssertionError("Consumer не догнал HEAD")
        assert httpx.get(f"{CONSUMER}/health", timeout=20).status_code == 200
        print(f"Группа {GROUP}: backlog обработан после рестарта, lag=0", flush=True)
    finally:
        reader.close()


def unavailable_cassandra():
    response = httpx.get(f"{CONSUMER}/health", timeout=25)
    assert response.status_code == 503, response.text
    cluster, session = connect()
    try:
        capacity = session.execute(SimpleStatement(
            "SELECT capacity FROM warehouse.zones WHERE warehouse_id='main' AND zone_id='ZONE-A'",
            consistency_level=ConsistencyLevel.ONE,
        )).one()["capacity"]
        before = metric_sum(metrics().body.decode(), "cassandra_write_errors_total")
        try:
            State(session).execute("UPDATE warehouse.zones SET capacity=? WHERE warehouse_id=? AND zone_id=?",
                                   (capacity, "main", "ZONE-A"))
        except (DriverException, NoHostAvailable):
            pass
        else:
            raise AssertionError("Запись QUORUM не должна проходить с одной репликой")
        assert metric_sum(metrics().body.decode(), "cassandra_write_errors_total") == before + 1
        print("Ошибка реальной записи QUORUM увеличила cassandra_write_errors_total", flush=True)
    finally:
        cluster.shutdown()
    record = make_event(quantity=25)
    published = send(record)
    reader = lag_reader()
    try:
        time.sleep(5)
        offset = reader.committed([TopicPartition(TOPIC, published["partition"])], timeout=5)[0].offset
        assert offset <= published["offset"], (offset, published)
        print("Cassandra без QUORUM: /health=503; offset не подтверждён", flush=True)
    finally:
        reader.close()


def cluster_ready():
    cluster, session = connect()
    try:
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            try:
                session.execute(SimpleStatement("SELECT * FROM warehouse.zones WHERE warehouse_id='main'",
                                                consistency_level=ConsistencyLevel.ALL))
                if sum(h.is_up for h in cluster.metadata.all_hosts()) == 3:
                    print("Три ноды снова доступны; CL=ALL проходит", flush=True)
                    return
            except (DriverException, NoHostAvailable):
                pass
            time.sleep(3)
        raise AssertionError("Кластер не восстановился")
    finally:
        cluster.shutdown()


if __name__ == "__main__":
    {"dlq-failure": blocked_dlq, "seed": seed_node_failure, "one-node": one_node_down, "lag": stopped_consumer, "caught-up": recovered_consumer,
     "unavailable": unavailable_cassandra, "ready": cluster_ready}[sys.argv[1]]()
