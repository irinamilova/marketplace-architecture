#!/bin/sh
set -eu
cd "$(dirname "$0")/.."

restore() {
    docker compose start kafka-1
}
trap restore EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

docker compose stop kafka-1
docker compose run --rm --no-deps tests python -m unittest -v tests.integration.PipelineTest.test_http_kafka_clickhouse
