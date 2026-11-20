import io
import json
import random
import struct
import time
import unittest
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import httpx
from confluent_kafka import Consumer, TopicPartition
from confluent_kafka.admin import AdminClient, ConfigResource
from fastavro import schemaless_reader

from app.common import BROKERS, BUCKET, REGISTRY, ROOT, SUBJECT, TOPIC, clickhouse, postgres, s3
from app.generator import history

MOVIE = "http://movie-service:8000"
AGGREGATOR = "http://aggregator:8000"
GRAFANA = "http://grafana:3000"


def event(day, user, kind="VIEW_STARTED", seconds=0, movie="movie-a", session=None):
    stamp = datetime(day.year, day.month, day.day, 12, tzinfo=timezone.utc)
    if kind == "VIEW_FINISHED":
        stamp += timedelta(seconds=seconds)
    return {
        "event_id": str(uuid4()),
        "user_id": user,
        "movie_id": movie,
        "event_type": kind,
        "timestamp": stamp.isoformat(timespec="milliseconds"),
        "device_type": "TV",
        "session_id": session or str(uuid4()),
        "progress_seconds": seconds,
    }


def post(url, payload=None):
    response = httpx.post(url, json=payload, timeout=90)
    if response.is_error:
        raise AssertionError(f"POST {url}: {response.status_code}: {response.text}")
    return response.json()


def stored(event_id):
    result = clickhouse(
        "SELECT * FROM cinema.raw_events FINAL WHERE event_id={id:UUID} FORMAT JSONEachRow",
        id=event_id,
    )
    return [json.loads(line) for line in result.text.splitlines()]


def wait_for_events(events, timeout=60):
    ids = {e["event_id"] for e in events}
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for event_id in list(ids):
            if stored(event_id):
                ids.remove(event_id)
        if not ids:
            return
        time.sleep(0.5)
    raise AssertionError(f"Не дошли до ClickHouse: {sorted(ids)}")


def free_day():
    for _ in range(30):
        day = date(2000, 1, 1) + timedelta(days=random.randrange(5000))
        used = clickhouse(
            "SELECT count() FROM cinema.raw_events WHERE toDate(timestamp) BETWEEN {day:Date} AND addDays({day:Date}, 7)",
            day=day,
        )
        with postgres() as conn:
            snapshot = conn.execute("SELECT 1 FROM daily_metrics WHERE metric_date=%s LIMIT 1", (day,)).fetchone()
        if used.text.strip() == "0" and snapshot is None:
            return day
    raise AssertionError("Не удалось выделить окно для изолированного теста")


class PipelineTest(unittest.TestCase):
    def test_registered_avro_key_and_partition_order(self):
        schema = httpx.get(f"{REGISTRY}/subjects/{SUBJECT}/versions/1", timeout=10)
        schema.raise_for_status()
        schema = schema.json()
        user = f"test-{uuid4()}"
        start = event(date(1999, 12, 29), user)
        finish = event(date(1999, 12, 29), user, "VIEW_FINISHED", 1800, session=start["session_id"])
        consumer = Consumer({"bootstrap.servers": BROKERS, "group.id": str(uuid4()),
                             "enable.auto.commit": False})
        try:
            positions = [TopicPartition(TOPIC, index) for index in range(3)]
            for position in positions:
                _, position.offset = consumer.get_watermark_offsets(position, timeout=10)
            consumer.assign(positions)
            for sample in (start, finish):
                post(f"{MOVIE}/events", sample)
            received = []
            deadline = time.monotonic() + 20
            while len(received) < 2 and time.monotonic() < deadline:
                message = consumer.poll(1)
                if message is None or message.error() or message.key() != user.encode():
                    continue
                value = message.value()
                self.assertEqual(struct.unpack(">bI", value[:5]), (0, schema["id"]))
                decoded = schemaless_reader(io.BytesIO(value[5:]), json.loads(schema["schema"]))
                received.append((message.partition(), message.offset(), str(decoded["event_id"])))
            self.assertEqual([r[2] for r in received], [start["event_id"], finish["event_id"]])
            self.assertEqual(received[0][0], received[1][0])
            self.assertLess(received[0][1], received[1][1])
            wait_for_events([start, finish])
        finally:
            consumer.close()

    def test_registry_and_replicated_topic(self):
        response = httpx.get(f"{REGISTRY}/subjects/{SUBJECT}/versions/1", timeout=10)
        response.raise_for_status()
        schema = json.loads(response.json()["schema"])
        self.assertEqual(schema, json.loads((ROOT / "schemas/movie-event.v1.avsc").read_text()))
        admin = AdminClient({"bootstrap.servers": BROKERS})
        metadata = admin.list_topics(TOPIC, timeout=10)
        self.assertEqual(len(metadata.brokers), 3)
        partitions = metadata.topics[TOPIC].partitions.values()
        self.assertEqual(len(partitions), 3)
        self.assertTrue(all(len(part.replicas) == 3 and len(part.isrs) >= 2 for part in partitions))
        resource = ConfigResource("topic", TOPIC)
        config = admin.describe_configs([resource], request_timeout=10)[resource].result()
        self.assertEqual(config["min.insync.replicas"].value, "2")

    def test_http_kafka_clickhouse(self):
        sample = event(date(1999, 12, 31), f"test-{uuid4()}", "VIEW_FINISHED", 1200)
        for bad in ({"event_id": "not-uuid"}, {"event_type": "UNKNOWN"}, {"device_type": "CONSOLE"},
                    {"progress_seconds": -1}, {"progress_seconds": "3"},
                    {"timestamp": "2000-01-01T10:00:00"}, {"movie_id": " "}):
            response = httpx.post(f"{MOVIE}/events", json=sample | bad, timeout=10)
            self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(post(f"{MOVIE}/events", sample), {"event_id": sample["event_id"]})
        wait_for_events([sample])
        rows = stored(sample["event_id"])
        self.assertEqual(len(rows), 1)
        value = rows[0]
        value["timestamp"] = datetime.fromisoformat(value["timestamp"]).replace(
            tzinfo=timezone.utc
        ).isoformat(timespec="milliseconds")
        self.assertEqual(value, sample)

    def test_generator_preserves_session_order(self):
        today = date(2026, 4, 15)
        records = list(history(today))
        after_restart = {record["event_id"]: record for record in history(today + timedelta(days=1))}
        overlapping = [record for record in records if record["event_id"] in after_restart]
        self.assertGreater(len(overlapping), 100)
        self.assertTrue(all(after_restart[record["event_id"]] == record for record in overlapping))
        self.assertEqual({record["event_type"] for record in records},
                         {"VIEW_STARTED", "VIEW_PAUSED", "VIEW_RESUMED", "VIEW_FINISHED", "LIKED", "SEARCHED"})
        sessions = {}
        for record in records:
            sessions.setdefault(record["session_id"], []).append(record)
        self.assertGreater(len(sessions), 100)
        for records in sessions.values():
            views = [r for r in records if r["event_type"].startswith("VIEW_")]
            self.assertEqual(views[0]["event_type"], "VIEW_STARTED")
            self.assertEqual(len({(r["user_id"], r["movie_id"], r["device_type"]) for r in records}), 1)
            if any(r["event_type"] == "VIEW_PAUSED" for r in views):
                self.assertEqual([r["event_type"] for r in views[1:3]], ["VIEW_PAUSED", "VIEW_RESUMED"])
            progress = [r["progress_seconds"] for r in views]
            self.assertEqual(progress, sorted(progress))
            self.assertEqual(len({r["event_id"] for r in records}), len(records))

    def test_aggregates_export_and_dashboard(self):
        day = free_day()
        a, b = (f"test-{uuid4()}" for _ in range(2))
        first = event(day, a)
        second = event(day, b, movie="movie-b")
        finished = event(day, a, "VIEW_FINISHED", 1800, session=first["session_id"])
        events = [first, second, finished, event(day + timedelta(days=1), a),
                  event(day + timedelta(days=1), a, movie="movie-b"),
                  event(day + timedelta(days=7), b)]
        for sample in events + [finished]:
            post(f"{MOVIE}/events", sample)
        wait_for_events(events)
        self.assertEqual(post(f"{AGGREGATOR}/aggregations/{day}")["source_events"], 3)

        def metrics():
            with postgres() as conn:
                return conn.execute(
                    "SELECT metric_name, dimension, value, calculated_at FROM daily_metrics "
                    "WHERE metric_date=%s ORDER BY metric_name, dimension", (day,),
                ).fetchall()

        original = metrics()
        values = {(name, dim): value for name, dim, value, _ in original}
        self.assertEqual(values[("source_events", "")], 3)
        self.assertEqual(values[("dau", "")], 2)
        self.assertEqual(values[("avg_view_seconds", "")], 1800)
        self.assertEqual(values[("view_conversion", "")], 0.5)
        self.assertEqual(values[("top_movie_views", "movie-a")], 1)
        self.assertEqual(values[("top_movie_views", "movie-b")], 1)
        self.assertEqual([values[(f"retention_d{i}", "")] for i in range(8)],
                         [1.0, 0.5, 0.0, 0.0, 0.0, 0.0, 0.0, 0.5])
        self.assertFalse(post(f"{AGGREGATOR}/aggregations/{day}")["changed"])
        self.assertEqual(metrics(), original)

        key = f"daily/{day}/aggregates.json"
        self.assertEqual(post(f"{AGGREGATOR}/exports/{day}")["key"], key)
        self.assertEqual(post(f"{AGGREGATOR}/exports/{day}")["key"], key)
        client = s3()
        exported = json.loads(client.get_object(Bucket=BUCKET, Key=key)["Body"].read())
        self.assertEqual(exported["date"], day.isoformat())
        self.assertEqual({(r["metric"], r["dimension"]): r["value"] for r in exported["metrics"]}, values)
        self.assertTrue(all(datetime.fromisoformat(r["calculated_at"]).utcoffset() == timedelta(0)
                            for r in exported["metrics"]))
        self.assertEqual(client.list_objects_v2(Bucket=BUCKET, Prefix=f"daily/{day}/")["KeyCount"], 1)

        response = httpx.get(f"{GRAFANA}/api/dashboards/uid/cinema", timeout=10)
        response.raise_for_status()
        panels = response.json()["dashboard"]["panels"]
        self.assertGreaterEqual(len(panels), 3)
        for panel in panels:
            target = panel["targets"][0]
            sql = target["rawSql"]
            self.assertIn("cinema.daily_aggregates", sql)
            self.assertNotIn("raw_events", sql)
            target["rawSql"] = sql.replace("metric_date BETWEEN today() - 30 AND today()",
                                           f"metric_date = toDate('{day}')").replace(
                                               "metric_date = yesterday()", f"metric_date = toDate('{day}')")
            answer = post(f"{GRAFANA}/api/ds/query", {"queries": [target], "from": "0", "to": "now"})
            result = answer["results"]["A"]
            self.assertNotIn("error", result)
            self.assertTrue(result["frames"], panel["title"])
            if panel["title"] == "Retention Cohort Heatmap":
                data = result["frames"][0]["data"]["values"]
                self.assertEqual([col[0] for col in data[1:]], [1.0, 0.5, 0, 0, 0, 0, 0, 0.5])

        late = event(day, b, "VIEW_FINISHED", 2400, movie="movie-b", session=second["session_id"])
        post(f"{MOVIE}/events", late)
        wait_for_events([late])
        self.assertTrue(post(f"{AGGREGATOR}/aggregations/{day}")["changed"])
        updated = metrics()
        self.assertEqual(len(updated), len(original))
        self.assertEqual({name: value for name, _, value, _ in updated}["avg_view_seconds"], 2100)
        self.assertEqual({name: value for name, _, value, _ in updated}["view_conversion"], 1)
        post(f"{AGGREGATOR}/exports/{day}")
        replacement = json.loads(client.get_object(Bucket=BUCKET, Key=key)["Body"].read())
        self.assertEqual({(r["metric"], r["dimension"]): r["value"] for r in replacement["metrics"]},
                         {(name, dim): value for name, dim, value, _ in updated})
        self.assertEqual(client.list_objects_v2(Bucket=BUCKET, Prefix=f"daily/{day}/")["KeyCount"], 1)

    def test_utc_boundary_and_closed_day_export(self):
        day = free_day()
        following = day + timedelta(days=1)
        user = f"test-{uuid4()}"
        before = event(day, user, "VIEW_FINISHED", 45)
        before["timestamp"] = f"{following}T02:59:59.123+03:00"
        after = event(following, user)
        after["timestamp"] = f"{following}T03:00:00+03:00"
        for sample in (before, after):
            post(f"{MOVIE}/events", sample)
        wait_for_events([before, after])
        self.assertEqual(stored(before["event_id"])[0]["timestamp"], f"{day} 23:59:59.123")
        self.assertEqual(post(f"{AGGREGATOR}/aggregations/{day}")["source_events"], 1)
        with postgres() as conn:
            values = dict(conn.execute(
                "SELECT metric_name, value FROM daily_metrics WHERE metric_date=%s", (day,),
            ))
            self.assertEqual(values["avg_view_seconds"], 45)
            self.assertIsNone(values["view_conversion"])
            self.assertIsNone(values["retention_d7"])
            self.assertFalse(values["cohort_size"])
            # Model a snapshot calculated before the day closed, without an export task.
            conn.execute("DELETE FROM export_queue WHERE metric_date=%s", (day,))
        self.assertFalse(post(f"{AGGREGATOR}/aggregations/{day}")["changed"])
        with postgres() as conn:
            self.assertIsNotNone(conn.execute(
                "SELECT 1 FROM export_queue WHERE metric_date=%s", (day,),
            ).fetchone())
        self.assertEqual(post(f"{AGGREGATOR}/aggregations/{following}")["source_events"], 1)


if __name__ == "__main__":
    unittest.main()
