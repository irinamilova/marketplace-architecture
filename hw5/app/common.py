import logging
import os
import time
from pathlib import Path

import boto3
import httpx
import psycopg
from botocore.config import Config

ROOT = Path(__file__).resolve().parents[1]
BROKERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka-1:9092,kafka-2:9092,kafka-3:9092")
REGISTRY = os.getenv("SCHEMA_REGISTRY_URL", "http://schema-registry:8081")
TOPIC = "movie-events"
SUBJECT = f"{TOPIC}-value"
BUCKET = "movie-analytics"
LOG = logging.getLogger("cinema")


def configure_logging():
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)sZ %(levelname)s %(name)s %(message)s"
    )
    logging.Formatter.converter = time.gmtime


def retry(operation, action, errors, attempts=4):
    for attempt in range(1, attempts + 1):
        try:
            return action()
        except errors as exc:
            LOG.warning("%s: попытка %d/%d: %s", operation, attempt, attempts, exc)
            if attempt == attempts:
                raise
            time.sleep(0.25 * 2 ** (attempt - 1))


def clickhouse(sql, *, data=None, **params):
    response = httpx.post(
        os.getenv("CLICKHOUSE_URL", "http://clickhouse:8123"),
        params={"query": sql, **{f"param_{k}": str(v) for k, v in params.items()}},
        auth=(os.getenv("CLICKHOUSE_USER", "cinema"), os.getenv("CLICKHOUSE_PASSWORD", "cinema_ch")),
        content=data,
        timeout=30,
    )
    if response.is_error:
        raise RuntimeError(f"ClickHouse {response.status_code}: {response.text.strip()}")
    return response


def postgres():
    return psycopg.connect(
        os.getenv("POSTGRES_DSN", "postgresql://cinema:cinema_pg@postgres:5432/cinema"),
        connect_timeout=5,
        options="-c statement_timeout=45000",
    )


def s3():
    return boto3.client(
        "s3",
        endpoint_url=os.getenv("S3_ENDPOINT", "http://s3:8333"),
        aws_access_key_id=os.getenv("S3_ACCESS_KEY", "cinema"),
        aws_secret_access_key=os.getenv("S3_SECRET_KEY", "cinema_s3_local"),
        region_name="us-east-1",
        config=Config(connect_timeout=4, read_timeout=10, retries={"total_max_attempts": 1},
                      s3={"addressing_style": "path"}),
    )
