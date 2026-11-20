import json
import os
import sys

from confluent_kafka import TopicPartition

from app.common import TOPIC, connect, lag_reader
from app.consumer import Runtime
from app.state import State
from tests.integration import make_event, send, wait_committed, wait_event


def crash(window, descriptor):
    event_id = descriptor["event"]["event_id"]
    if window == "before-marker":
        execute = State.execute

        def execute_until_marker(self, sql, values=()):
            if sql.startswith("UPDATE warehouse.processed_events") and str(values[-1]) == event_id:
                os._exit(72)
            return execute(self, sql, values)

        State.execute = execute_until_marker
    elif window == "before-commit":
        process = Runtime.process

        def process_until_commit(self, message):
            process(self, message)
            event, _ = self.schemas.decode(message.value())
            if event["event_id"] == event_id:
                os._exit(72)

        Runtime.process = process_until_commit
    else:
        raise ValueError(window)
    runtime = Runtime()
    runtime.worker.join(90)
    raise AssertionError("Consumer не достиг точки аварийной остановки")


def inspect(window, descriptor, recovered=False):
    event = descriptor["event"]
    published = descriptor["published"]
    if recovered:
        wait_event(event["event_id"], timeout=90)
        wait_committed(published, timeout=90)
    cluster, session = connect()
    reader = lag_reader()
    try:
        state = State(session)
        row = state.event("main", event["event_id"])
        expected = "PENDING" if window == "before-marker" and not recovered else "APPLIED"
        assert row["status"] == expected, row
        zone = state.inventory("main", event["product_id"])[0]
        total = state.total("main", event["product_id"])
        projection = state.execute(
            "SELECT * FROM warehouse.inventory_by_zone WHERE warehouse_id=? AND zone_id=? AND product_id=?",
            ("main", "ZONE-A", event["product_id"]),
        ).one()
        assert zone["available_quantity"] == total["total_available"] == projection["available_quantity"] == 100
        offset = reader.committed([TopicPartition(TOPIC, published["partition"])], timeout=5)[0].offset
        assert offset > published["offset"] if recovered else offset <= published["offset"]
        print(f"{window}: status={expected}, три проекции=100, committed={offset}, event_offset={published['offset']}", flush=True)
    finally:
        reader.close()
        cluster.shutdown()


if __name__ == "__main__":
    action = sys.argv[1]
    if action == "publish":
        event = make_event()
        print(json.dumps({"event": event, "published": send(event)}), flush=True)
    else:
        window, descriptor = sys.argv[2], json.loads(sys.argv[3])
        if action == "crash":
            crash(window, descriptor)
        elif action in ("inspect", "verify"):
            inspect(window, descriptor, recovered=action == "verify")
        else:
            raise ValueError(action)
