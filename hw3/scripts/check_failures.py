#!/usr/bin/env python3
"""Real, reversible failure checks; run from any directory with Docker running."""
import json
import os
from pathlib import Path
import subprocess
import time
from urllib.error import HTTPError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
URL = 'http://localhost:' + os.getenv('BOOKING_PORT', '8003')
FLIGHT = '/flights/11111111-1111-4111-8111-111111111111'


def compose(*args):
    return subprocess.check_output(['docker', 'compose', *args], cwd=ROOT, text=True).strip()


def get(path):
    started = time.monotonic()
    try:
        with urlopen(URL + path, timeout=20) as response:
            return response.status, json.load(response), time.monotonic() - started
    except HTTPError as error:
        return error.code, json.load(error), time.monotonic() - started


def master():
    return compose('exec','-T','sentinel-1','redis-cli','--raw','-p','26379',
                   'sentinel','get-master-addr-by-name','flight-master').splitlines()


def main():
    stopped = []
    try:
        assert get(FLIGHT)[0] == 200
        compose('stop','flight-service')
        stopped.append('flight-service')
        threshold = int(compose('exec','-T','booking-service','printenv','CB_FAILURE_THRESHOLD'))
        for _ in range(threshold):
            assert get(FLIGHT)[0] in (503, 504)
        status, body, elapsed = get(FLIGHT)
        assert status == 503 and 'OPEN' in body['detail'] and elapsed < 1, (status,body,elapsed)
        print(f'PASS circuit OPEN: HTTP 503 in {elapsed:.3f}s', flush=True)
        compose('start','flight-service')
        stopped.remove('flight-service')
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if get(FLIGHT)[0] == 200:
                break
            time.sleep(1)
        else:
            raise AssertionError('Flight recovery timed out')
        logs = compose('logs','--no-color','booking-service')
        assert 'HALF_OPEN -> CLOSED' in logs
        print('PASS HALF_OPEN -> CLOSED after Flight recovery', flush=True)

        old = master()
        candidates = ['redis-master','redis-replica']
        active = [s for s in candidates if compose('exec','-T',s,'redis-cli','--raw','role').splitlines()[0] == 'master']
        assert len(active) == 1, active
        compose('stop',active[0])
        stopped.append(active[0])
        assert get(FLIGHT)[0] == 200  # Redis unavailable: PostgreSQL fallback
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if master() != old:
                break
            time.sleep(1)
        else:
            raise AssertionError('Sentinel did not elect a new master')
        assert get(FLIGHT)[0] == 200
        # Ensure the application's Sentinel client can actually fill/read the new cache.
        code = "from app.cache import Cache; c=Cache(); c.redis.set('failover:probe','ok',ex=30); assert c.redis.get('failover:probe') == b'ok'"
        compose('exec','-T','flight-service','python','-c',code)
        print(f'PASS Redis failover {old} -> {master()}, REST available, new master writable', flush=True)
        elected = master()
        compose('restart', 'sentinel-1', 'sentinel-2', 'sentinel-3')
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                assert master() == elected
                compose('exec','-T','flight-service','python','-c',code)
                break
            except (AssertionError, subprocess.CalledProcessError):
                time.sleep(1)
        else:
            raise AssertionError('Sentinel lost the promoted master after restart')
        assert get(FLIGHT)[0] == 200
        print('PASS Sentinel restart: promoted master retained while old master is down', flush=True)
        promoted = next(s for s in candidates if s != active[0])
        compose('restart', promoted)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                assert compose('exec','-T',promoted,'redis-cli','--raw','role').splitlines()[0] == 'master'
                compose('exec','-T','flight-service','python','-c',code)
                break
            except (AssertionError, subprocess.CalledProcessError):
                time.sleep(1)
        else:
            raise AssertionError('Promoted Redis lost its master role after restart')
        print('PASS promoted Redis restart: master role persisted', flush=True)
    finally:
        for service in reversed(stopped):
            compose('start',service)
        print('Stopped containers restored.', flush=True)


if __name__ == '__main__':
    main()
