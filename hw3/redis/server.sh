#!/bin/sh
set -eu
# Sentinel persists promotion/demotion with CONFIG REWRITE in this volume.
if [ ! -f /data/redis.conf ]; then
  cat > /data/redis.conf <<CONF
bind 0.0.0.0
protected-mode no
appendonly yes
dir /data
replica-announce-ip ${REDIS_HOST}
CONF
  if [ "${REDIS_INITIAL_REPLICA:-no}" = yes ]; then
    echo 'replicaof redis-master 6379' >> /data/redis.conf
  fi
fi
exec redis-server /data/redis.conf
