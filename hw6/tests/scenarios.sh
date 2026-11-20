#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
dc() { docker compose "$@"; }
run() { dc run --rm --no-deps tests python -m tests.scenarios "$@"; }
restore() { dc start cassandra-2 cassandra-3 consumer; }
trap restore EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

run dlq-failure
received=$(run seed)
dc stop cassandra-2
run one-node "$received"
dc stop cassandra-3
run unavailable
dc start cassandra-2 cassandra-3
run ready
dc exec -T cassandra-1 nodetool status
run caught-up

dc stop consumer
run lag
dc start consumer
run caught-up

for window in before-marker before-commit; do
    dc stop consumer
    descriptor=$(dc run --rm --no-deps tests python -m tests.recovery publish)
    code=0
    dc run --rm --no-deps tests python -m tests.recovery crash "$window" "$descriptor" || code=$?
    [ "$code" -eq 72 ] || exit 1
    dc run --rm --no-deps tests python -m tests.recovery inspect "$window" "$descriptor"
    dc start consumer
    dc run --rm --no-deps tests python -m tests.recovery verify "$window" "$descriptor"
done
trap - EXIT
