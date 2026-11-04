import logging
import os
from google.protobuf.message import DecodeError
from redis import RedisError
from redis.sentinel import Sentinel

log = logging.getLogger(__name__)


class Cache:
    def __init__(self):
        nodes = [node.rsplit(':', 1) for node in os.environ['REDIS_SENTINELS'].split(',')]
        self.redis = Sentinel([(host, int(port)) for host, port in nodes],
                              socket_timeout=0.3, socket_connect_timeout=0.3,
                              sentinel_kwargs={'socket_timeout': 0.3, 'socket_connect_timeout': 0.3}).master_for(
                                  os.getenv('REDIS_MASTER', 'flight-master'))
        self.ttl = int(os.getenv('CACHE_TTL', '300'))
        if not 300 <= self.ttl <= 600:
            raise ValueError('CACHE_TTL must be 300..600 seconds')

    def read(self, conn, key, message_type, fetch):
        raw = None
        try:
            raw = self.redis.get(key)
        except RedisError as exc:
            log.warning('cache read unavailable: %s', type(exc).__name__)
        # The shared lock prevents a mutation while a miss is being refilled.
        epoch = conn.execute('SELECT value FROM cache_epoch WHERE id=1 FOR SHARE').fetchone()['value']
        if raw:
            try:
                version, payload = raw.split(b':', 1)
                if int(version) == epoch:
                    value = message_type.FromString(payload)
                    log.info('cache hit key=%s', key)
                    return value
            except (ValueError, DecodeError):
                log.warning('invalid cache entry key=%s', key)
        log.info('cache miss key=%s', key)
        value = fetch()
        try:
            self.redis.set(key, str(epoch).encode() + b':' + value.SerializeToString(), ex=self.ttl)
        except RedisError as exc:
            log.warning('cache write unavailable: %s', type(exc).__name__)
        return value

    def invalidate(self, conn):
        # Called within the mutation transaction, not asynchronously after it.
        conn.execute('UPDATE cache_epoch SET value=value+1 WHERE id=1')
        try:
            for pattern in ('flight:*', 'search:*'):
                for key in self.redis.scan_iter(match=pattern, count=100):
                    self.redis.delete(key)
        except RedisError as exc:
            log.warning('cache invalidation unavailable; epoch protects reads: %s', type(exc).__name__)
