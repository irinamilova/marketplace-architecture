import asyncio
import json
import os
import time
from contextlib import asynccontextmanager, suppress
from datetime import date, datetime, timedelta, timezone

import httpx
import psycopg
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import FastAPI, HTTPException

from app.common import BUCKET, LOG, ROOT, clickhouse, configure_logging, postgres, retry, s3

DAILY_SQL = (ROOT / "sql/daily.sql").read_text() + "\nFORMAT JSONEachRow"
COHORT_SQL = (ROOT / "sql/cohort.sql").read_text() + "\nFORMAT JSONEachRow"
READ_METRICS = """
SELECT metric_name, dimension, value, calculated_at
FROM daily_metrics
WHERE metric_date = %s
ORDER BY metric_name, dimension
"""
UPSERT = """
INSERT INTO daily_metrics (metric_date, metric_name, dimension, value, calculated_at)
VALUES (%s, %s, %s, %s, %s)
ON CONFLICT (metric_date, metric_name, dimension) DO UPDATE
SET value = EXCLUDED.value, calculated_at = EXCLUDED.calculated_at
"""
DEPENDENCY_ERRORS = (psycopg.Error, httpx.HTTPError, RuntimeError, BotoCoreError, ClientError)


def utc_today():
    return datetime.now(timezone.utc).date()


def compute(day):
    daily = [json.loads(line) for line in clickhouse(DAILY_SQL, day=day).text.splitlines()]
    totals = next(row for row in daily if int(row["is_total"]) == 1)
    movies = [(row["movie_id"], int(row["views_started"])) for row in daily
              if int(row["is_total"]) == 0 and int(row["views_started"]) > 0]
    cohort = json.loads(clickhouse(COHORT_SQL, day=day).text)
    size = int(cohort["cohort_size"])
    returned = cohort["returned"] or [0] * 8
    rates = [
        int(returned[offset]) / size if size and (offset == 0 or day + timedelta(days=offset) < utc_today())
        else None
        for offset in range(8)
    ]
    started = int(totals["views_started"])
    finished = int(totals["views_finished"])
    return {
        "metric_date": day.isoformat(),
        "source_events": int(totals["source_events"]),
        "dau": int(totals["dau"]),
        "avg_view_seconds": totals["avg_view_seconds"],
        "views_started": started,
        "views_finished": finished,
        "view_conversion": finished / started if started else None,
        "top_movies": movies,
        "cohort_size": size,
        "retention": rates,
    }


def metric_values(snapshot):
    values = {(name, ""): snapshot[name] for name in (
        "source_events", "dau", "avg_view_seconds", "views_started",
        "views_finished", "view_conversion", "cohort_size",
    )}
    values.update({("top_movie_views", movie): count for movie, count in snapshot["top_movies"]})
    values.update({(f"retention_d{offset}", ""): rate
                   for offset, rate in enumerate(snapshot["retention"])})
    return values


def lock_day(conn, day):
    conn.execute("SELECT pg_advisory_xact_lock(%s, %s)", (5301, day.toordinal()))


def enqueue_export(conn, day, *, changed=False):
    conflict = "DO UPDATE SET exported_at = NULL" if changed else "DO NOTHING"
    conn.execute(
        f"INSERT INTO export_queue(metric_date) VALUES (%s) ON CONFLICT (metric_date) {conflict}",
        (day,),
    )


def refresh(day):
    started = time.monotonic()
    LOG.info("aggregation started date=%s", day)

    def save():
        with postgres() as conn:
            lock_day(conn, day)
            snapshot = compute(day)
            values = metric_values(snapshot)
            current = {(name, key): value for name, key, value, _
                       in conn.execute(READ_METRICS, (day,))}
            if current == values:
                if day < utc_today():
                    enqueue_export(conn, day)
                return snapshot, False

            now = datetime.now(timezone.utc)
            snapshot["calculated_at"] = now.strftime("%Y-%m-%d %H:%M:%S.%f")
            clickhouse(
                "INSERT INTO cinema.daily_aggregates FORMAT JSONEachRow",
                data=(json.dumps(snapshot, allow_nan=False) + "\n").encode(),
            )
            with conn.cursor() as cursor:
                cursor.executemany(UPSERT, [
                    (day, name, key, value, now) for (name, key), value in values.items()
                ])
                cursor.executemany("DELETE FROM daily_metrics WHERE metric_date=%s AND metric_name=%s AND dimension=%s",
                                   [(day, *key) for key in current.keys() - values.keys()])
            if day < utc_today():
                enqueue_export(conn, day, changed=True)
            return snapshot, True

    try:
        snapshot, changed = retry(f"aggregation {day}", save, (psycopg.Error, httpx.HTTPError, RuntimeError))
    except DEPENDENCY_ERRORS:
        LOG.exception("aggregation failed date=%s seconds=%.3f", day, time.monotonic() - started)
        raise
    LOG.info("aggregation completed date=%s records=%d seconds=%.3f changed=%s",
             day, snapshot["source_events"], time.monotonic() - started, changed)
    return {"date": day.isoformat(), "source_events": snapshot["source_events"], "changed": changed}


def export(day):
    key = f"daily/{day}/aggregates.json"
    with postgres() as conn:
        lock_day(conn, day)
        rows = conn.execute(READ_METRICS, (day,)).fetchall()
        if not rows:
            return None
        content = {
            "date": day.isoformat(),
            "metrics": [
                {"metric": name, "dimension": dimension, "value": value,
                 "calculated_at": calculated_at.astimezone(timezone.utc).isoformat()}
                for name, dimension, value, calculated_at in rows
            ],
        }
        s3().put_object(Bucket=BUCKET, Key=key, Body=json.dumps(content, allow_nan=False).encode(),
                        ContentType="application/json")
        conn.execute("UPDATE export_queue SET exported_at=now() WHERE metric_date=%s", (day,))
    LOG.info("export completed date=%s metrics=%d key=%s", day, len(rows), key)
    return {"date": day.isoformat(), "key": key, "metrics": len(rows)}


def cycle(lookback):
    today = utc_today()
    for offset in range(lookback, -1, -1):
        try:
            refresh(today - timedelta(days=offset))
        except DEPENDENCY_ERRORS:
            continue
    try:
        with postgres() as conn:
            dates = conn.execute(
                "SELECT metric_date FROM export_queue WHERE exported_at IS NULL ORDER BY metric_date"
            ).fetchall()
    except psycopg.Error:
        LOG.exception("Не удалось прочитать очередь экспорта; повтор в следующем цикле")
        return
    for (day,) in dates:
        try:
            export(day)
        except DEPENDENCY_ERRORS:
            LOG.exception("Экспорт %s не выполнен; повтор в следующем цикле", day)


async def schedule(interval, lookback):
    while True:
        await asyncio.to_thread(cycle, lookback)
        await asyncio.sleep(interval)


@asynccontextmanager
async def lifespan(app):
    configure_logging()
    interval = float(os.getenv("AGGREGATION_INTERVAL_SECONDS", "60"))
    lookback = max(1, int(os.getenv("AGGREGATION_LOOKBACK_DAYS", "14")))
    task = asyncio.create_task(schedule(interval, lookback)) if interval > 0 else None
    try:
        yield
    finally:
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


app = FastAPI(title="Aggregation Service", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/aggregations/{day}")
def recalculate(day: date):
    if day > utc_today():
        raise HTTPException(status_code=422, detail="Дата не может быть в будущем")
    try:
        return refresh(day)
    except DEPENDENCY_ERRORS as exc:
        raise HTTPException(status_code=503, detail="Не удалось пересчитать метрики") from exc


@app.post("/exports/{day}")
def export_date(day: date):
    if day >= utc_today():
        raise HTTPException(status_code=422, detail="Экспорт доступен за завершённый день UTC")
    try:
        with postgres() as conn:
            lock_day(conn, day)
            if not conn.execute(READ_METRICS, (day,)).fetchone():
                raise HTTPException(status_code=404, detail="Нет агрегатов за эту дату")
            enqueue_export(conn, day, changed=True)
        result = export(day)
    except DEPENDENCY_ERRORS as exc:
        LOG.exception("Ошибка ручного экспорта date=%s", day)
        raise HTTPException(status_code=503, detail="Не удалось экспортировать метрики") from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Нет агрегатов за эту дату")
    return result
