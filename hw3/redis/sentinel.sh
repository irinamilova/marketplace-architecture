#!/bin/sh
set -eu
# A writable config is required: Sentinel rewrites it during failover.
if [ ! -f /data/sentinel.conf ]; then
cat > /data/sentinel.conf <<CONF
port 26379
bind 0.0.0.0
protected-mode no
sentinel resolve-hostnames yes
sentinel announce-hostnames yes
sentinel announce-ip ${SENTINEL_HOST}
sentinel monitor flight-master redis-master 6379 2
sentinel down-after-milliseconds flight-master 3000
sentinel failover-timeout flight-master 10000
sentinel parallel-syncs flight-master 1
CONF
fi
exec redis-server /data/sentinel.conf --sentinel
