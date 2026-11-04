"""Versioned SQL migrations: checksum validation + transactional advisory lock."""
import hashlib
import logging
from pathlib import Path
import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool


def migrate(dsn, service):
    with psycopg.connect(dsn) as conn:
        conn.execute('SELECT pg_advisory_xact_lock(743921)')
        conn.execute('CREATE TABLE IF NOT EXISTS schema_migrations (version text PRIMARY KEY, checksum text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())')
        for path in sorted((Path(__file__).resolve().parent.parent / 'db' / service).glob('*.sql')):
            sql = path.read_text()
            digest = hashlib.sha256(sql.encode()).hexdigest()
            old = conn.execute('SELECT checksum FROM schema_migrations WHERE version=%s', (path.name,)).fetchone()
            if old:
                if old[0] != digest:
                    raise RuntimeError(f'Migration checksum changed: {path.name}')
            else:
                conn.execute(sql)
                conn.execute('INSERT INTO schema_migrations(version,checksum) VALUES (%s,%s)', (path.name, digest))
                logging.info('migration applied: %s/%s', service, path.name)


def pool(dsn):
    return ConnectionPool(dsn, open=True, min_size=1, max_size=16, kwargs={'row_factory': dict_row}, timeout=5)
