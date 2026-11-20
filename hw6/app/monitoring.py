import threading

from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST

from fastapi.responses import Response

from app.common import LOG, committed_lag, lag_reader

LAG = Gauge("consumer_lag", "Latest offset minus committed offset", ["partition"])
PROCESSED = Counter("events_processed", "Successfully applied events", ["event_type"])
DURATION = Histogram("event_processing_duration_seconds", "Time spent handling one delivery")
WRITE_ERRORS = Counter("cassandra_write_errors", "Failed Cassandra write operations")


def start_lag_monitor(stop):
    def run():
        reader = lag_reader()
        try:
            while not stop.is_set():
                try:
                    for partition, value in committed_lag(reader).items():
                        LAG.labels(str(partition)).set(value)
                except Exception:
                    LOG.warning("Не удалось обновить consumer lag", exc_info=True)
                stop.wait(3)
        finally:
            reader.close()
    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    return worker


def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
