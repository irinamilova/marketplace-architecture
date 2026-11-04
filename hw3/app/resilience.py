"""Client transport middleware: credentials, deadlines, retry, circuit breaker."""
import logging
import os
import threading
import time
from collections import namedtuple, deque
import grpc
import flight_pb2_grpc as rpc

log = logging.getLogger(__name__)
TRANSIENT = {grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.DEADLINE_EXCEEDED}
# A server failure opens the circuit even when retrying it is not safe.
CIRCUIT_FAILURES = TRANSIENT | {grpc.StatusCode.INTERNAL, grpc.StatusCode.UNKNOWN,
                               grpc.StatusCode.DATA_LOSS}


class ServiceUnavailable(grpc.RpcError):
    def __init__(self, message):
        self.message = message

    def code(self):
        return grpc.StatusCode.UNAVAILABLE

    def details(self):
        return self.message


class CircuitBreaker:
    def __init__(self, threshold=5, reset_timeout=10, clock=time.monotonic, window_seconds=60):
        if threshold < 1 or reset_timeout <= 0 or window_seconds <= 0:
            raise ValueError('Invalid circuit breaker settings')
        self.threshold, self.reset_timeout, self.clock = threshold, reset_timeout, clock
        self.window_seconds = window_seconds
        self.failures = deque()
        self.state, self.opened_at, self.generation = 'CLOSED', 0, 0
        self.lock = threading.Lock()

    def transition(self, state):
        log.warning('circuit breaker %s -> %s', self.state, state)
        self.state = state
        self.generation += 1

    def enter(self):
        with self.lock:
            if self.state == 'OPEN':
                if self.clock() - self.opened_at < self.reset_timeout:
                    raise ServiceUnavailable('Flight service circuit is OPEN; retry later')
                self.transition('HALF_OPEN')
                return self.generation, True
            if self.state == 'HALF_OPEN':
                raise ServiceUnavailable('Flight service recovery probe is in progress; retry later')
            return self.generation, False

    def finish(self, ticket, failed):
        with self.lock:
            if ticket != self.generation:
                return  # late completion from an earlier circuit state
            if failed:
                now = self.clock()
                while self.failures and self.failures[0] <= now - self.window_seconds:
                    self.failures.popleft()
                self.failures.append(now)
                if self.state == 'HALF_OPEN' or len(self.failures) >= self.threshold:
                    self.opened_at = self.clock()
                    self.transition('OPEN')
            else:
                self.failures.clear()
                if self.state == 'HALF_OPEN':
                    self.transition('CLOSED')


Details = namedtuple('Details', 'method timeout metadata credentials wait_for_ready compression')


class ResilienceInterceptor(grpc.UnaryUnaryClientInterceptor):
    def __init__(self, key, breaker, attempts=3, timeout=1.5, backoff=0.1, sleep=time.sleep):
        if not key or not 1 <= attempts <= 3 or timeout <= 0 or backoff < 0:
            raise ValueError('Invalid gRPC client settings')
        self.key, self.breaker, self.attempts = key, breaker, attempts
        self.timeout, self.backoff, self.sleep = timeout, backoff, sleep

    def intercept_unary_unary(self, continuation, details, request):
        ticket, probe = self.breaker.enter()
        attempts = 1 if probe else self.attempts
        call_details = Details(details.method, min(details.timeout, self.timeout) if details.timeout else self.timeout,
                               tuple(details.metadata or ()) + (('x-api-key', self.key),),
                               details.credentials, False, getattr(details, 'compression', None))
        for attempt in range(attempts):
            try:
                outcome = continuation(call_details, request)
                code = outcome.code()  # waits for this attempt, including deadline
            except grpc.RpcError as exc:
                outcome, code = exc, exc.code()
            if code not in TRANSIENT or attempt == attempts - 1:
                self.breaker.finish(ticket, code in CIRCUIT_FAILURES)
                if isinstance(outcome, grpc.RpcError) and not isinstance(outcome, grpc.Future):
                    raise outcome
                return outcome
            delay = self.backoff * (2 ** attempt)
            log.warning('grpc retry method=%s attempt=%s/%s code=%s backoff=%.3f',
                        details.method, attempt + 2, attempts, code.name, delay)
            self.sleep(delay)


def client():
    breaker = CircuitBreaker(int(os.getenv('CB_FAILURE_THRESHOLD', '5')), float(os.getenv('CB_RESET_TIMEOUT', '10')),
                             window_seconds=float(os.getenv('CB_WINDOW_SECONDS', '60')))
    interceptor = ResilienceInterceptor(os.environ['GRPC_API_KEY'], breaker,
                                       int(os.getenv('GRPC_MAX_ATTEMPTS', '3')),
                                       float(os.getenv('GRPC_TIMEOUT', '1.5')),
                                       float(os.getenv('GRPC_BACKOFF', '0.1')))
    channel = grpc.intercept_channel(grpc.insecure_channel(os.getenv('FLIGHT_TARGET', 'flight-service:50051')), interceptor)
    return rpc.FlightServiceStub(channel), channel, breaker
