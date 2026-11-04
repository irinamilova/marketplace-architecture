from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import grpc
import pytest
from app.resilience import CircuitBreaker, ResilienceInterceptor, ServiceUnavailable


class Outcome:
    def __init__(self, status):
        self.status = status
    def code(self):
        return self.status


@pytest.mark.parametrize('code,count', [
    (grpc.StatusCode.UNAVAILABLE, 3), (grpc.StatusCode.DEADLINE_EXCEEDED, 3),
    (grpc.StatusCode.INVALID_ARGUMENT, 1), (grpc.StatusCode.NOT_FOUND, 1),
    (grpc.StatusCode.RESOURCE_EXHAUSTED, 1), (grpc.StatusCode.OK, 1)])
def test_retry_policy_and_metadata(code, count):
    calls, delays = [], []
    middleware = ResilienceInterceptor('secret', CircuitBreaker(), sleep=delays.append)
    def call(details, request):
        calls.append(details)
        return Outcome(code)
    middleware.intercept_unary_unary(call, SimpleNamespace(method='/test', timeout=None, metadata=(), credentials=None), object())
    assert len(calls) == count
    assert delays == ([0.1, 0.2] if count == 3 else [])
    assert all(('x-api-key', 'secret') in c.metadata and c.timeout == 1.5 for c in calls)


def test_breaker_single_probe_and_recovery():
    now = [0]
    breaker = CircuitBreaker(2, 10, clock=lambda: now[0])
    for _ in range(2):
        ticket, _ = breaker.enter()
        breaker.finish(ticket, True)
    with pytest.raises(ServiceUnavailable):
        breaker.enter()
    now[0] = 11
    def enter(_):
        try:
            return breaker.enter()
        except ServiceUnavailable:
            return None
    with ThreadPoolExecutor(max_workers=10) as executor:
        probes = [p for p in executor.map(enter, range(10)) if p is not None]
    assert len(probes) == 1 and probes[0][1]
    breaker.finish(probes[0][0], True)
    assert breaker.state == 'OPEN'
    now[0] = 22
    ticket, _ = breaker.enter()
    breaker.finish(ticket, False)
    assert breaker.state == 'CLOSED'


def test_late_response_cannot_close_open_circuit():
    breaker = CircuitBreaker(1)
    ticket, _ = breaker.enter()
    breaker.finish(ticket, True)
    breaker.finish(ticket, False)
    assert breaker.state == 'OPEN'


def test_error_window_expires_and_env_configuration(monkeypatch):
    now = [0]
    breaker = CircuitBreaker(2, 10, clock=lambda: now[0], window_seconds=5)
    ticket, _ = breaker.enter()
    breaker.finish(ticket, True)
    now[0] = 6
    ticket, _ = breaker.enter()
    breaker.finish(ticket, True)
    assert breaker.state == 'CLOSED'
    now[0] = 7
    ticket, _ = breaker.enter()
    breaker.finish(ticket, True)
    assert breaker.state == 'OPEN'
    from app.resilience import client
    monkeypatch.setenv('CB_FAILURE_THRESHOLD','7')
    monkeypatch.setenv('CB_RESET_TIMEOUT','12')
    monkeypatch.setenv('CB_WINDOW_SECONDS','45')
    monkeypatch.setenv('GRPC_API_KEY','test')
    _, channel, configured = client()
    try:
        assert (configured.threshold, configured.reset_timeout, configured.window_seconds) == (7,12,45)
    finally:
        channel.close()


def test_open_makes_no_calls_and_half_open_has_only_one_attempt():
    now = [0]
    breaker = CircuitBreaker(1, 10, clock=lambda: now[0])
    middleware = ResilienceInterceptor('secret',breaker,sleep=lambda _: None)
    details = SimpleNamespace(method='/test',timeout=None,metadata=(),credentials=None)
    calls = []
    def call(details, request):
        calls.append(request)
        return Outcome(grpc.StatusCode.UNAVAILABLE)
    middleware.intercept_unary_unary(call,details,'initial')
    assert len(calls) == 3 and breaker.state == 'OPEN'
    with pytest.raises(ServiceUnavailable):
        middleware.intercept_unary_unary(call,details,'blocked')
    assert len(calls) == 3
    now[0] = 11
    middleware.intercept_unary_unary(call,details,'probe')
    assert calls == ['initial'] * 3 + ['probe']
    assert breaker.state == 'OPEN'
