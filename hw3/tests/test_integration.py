"""Run inside booking-service against the real Compose dependencies."""
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import grpc
import httpx
import psycopg
import pytest
from redis.sentinel import Sentinel
import flight_pb2 as pb
import flight_pb2_grpc as rpc
from app.db import pool
from app.booking import recover_operation
from types import SimpleNamespace

FLIGHT = '11111111-1111-4111-8111-111111111111'
FD = os.getenv('TEST_FLIGHT_DSN', 'postgresql://flight:flight-local-password@flight-db/flights')
BD = os.environ['DATABASE_URL']

@pytest.fixture
def api():
    with httpx.Client(base_url='http://localhost:8000', timeout=20) as client:
        yield client

@pytest.fixture
def stub():
    from app.resilience import client
    stub, channel, _ = client()
    yield stub
    channel.close()


def make_flight(seats=10):
    fid = str(uuid4())
    with psycopg.connect(FD) as conn:
        conn.execute("INSERT INTO flights VALUES (%s,%s,'SU','SVO','LED','2026-12-01 09:00Z','2026-12-01 11:00Z',%s,%s,120000,'RUB','SCHEDULED')", (fid, fid[:12], seats, seats))
    return fid


def test_rest_lifecycle_price_snapshot_and_validation(api, stub):
    fid = make_flight()
    user = str(uuid4())
    body = dict(user_id=user, flight_id=fid, passenger_name='Ирина Милова', passenger_email='irina@example.com', seat_count=2)
    assert api.get('/flights', params={'origin':'SVO','destination':'LED','date':'2026-12-01'}).status_code == 200
    assert api.get('/flights', params={'origin':'SVO'}).status_code == 422
    assert api.get('/flights/' + str(uuid4())).status_code == 404
    assert api.post('/bookings', json={**body,'seat_count':0}).status_code == 422
    response = api.post('/bookings', json=body)
    assert response.status_code == 201, response.text
    booking = response.json()
    assert booking['total_price_minor'] == 240000
    stub.UpdateFlight(pb.UpdateFlightRequest(flight_id=fid, price_minor=999000))
    assert api.get('/bookings/' + booking['id']).json()['total_price_minor'] == 240000
    assert len(api.get('/bookings', params={'user_id':user}).json()) == 1
    assert api.get('/flights/' + fid).json()['available_seats'] == 8
    assert api.post('/bookings/' + booking['id'] + '/cancel').json()['status'] == 'CANCELLED'
    assert api.post('/bookings/' + booking['id'] + '/cancel').status_code == 409
    assert api.get('/flights/' + fid).json()['available_seats'] == 10
    assert api.post('/bookings', json={**body,'seat_count':11}).status_code == 409
    assert len(api.get('/bookings', params={'user_id':user}).json()) == 1


@pytest.mark.parametrize('method,payload', [
    ('SearchFlights', pb.SearchFlightsRequest(origin='SVO',destination='LED')),
    ('GetFlight', pb.GetFlightRequest(flight_id=FLIGHT)),
    ('ReserveSeats', pb.ReserveSeatsRequest(flight_id=FLIGHT,booking_id=str(uuid4()),seat_count=1)),
    ('ReleaseReservation', pb.ReleaseReservationRequest(booking_id=str(uuid4()))),
    ('UpdateFlight', pb.UpdateFlightRequest(flight_id=FLIGHT,price_minor=100))])
@pytest.mark.parametrize('metadata', [(), (('x-api-key','incorrect'),)])
def test_auth_all_methods(method, payload, metadata):
    with grpc.insecure_channel('flight-service:50051') as channel:
        with pytest.raises(grpc.RpcError) as error:
            getattr(rpc.FlightServiceStub(channel),method)(payload, metadata=metadata,timeout=3)
        assert error.value.code() == grpc.StatusCode.UNAUTHENTICATED


def test_last_seat_race_and_idempotency(stub):
    fid = make_flight(1)
    ids = [str(uuid4()) for _ in range(12)]
    def reserve(bid):
        try:
            return stub.ReserveSeats(pb.ReserveSeatsRequest(flight_id=fid,booking_id=bid,seat_count=1))
        except grpc.RpcError as exc:
            assert exc.code() == grpc.StatusCode.RESOURCE_EXHAUSTED
            return None
    with ThreadPoolExecutor(max_workers=12) as executor:
        winners = [r for r in executor.map(reserve, ids) if r]
    assert len(winners) == 1
    winner = winners[0]
    assert reserve(winner.booking_id).id == winner.id
    assert stub.GetFlight(pb.GetFlightRequest(flight_id=fid)).available_seats == 0
    for _ in range(2):
        stub.ReleaseReservation(pb.ReleaseReservationRequest(booking_id=winner.booking_id))
    assert stub.GetFlight(pb.GetFlightRequest(flight_id=fid)).available_seats == 1
    with pytest.raises(grpc.RpcError) as error:
        stub.ReserveSeats(pb.ReserveSeatsRequest(flight_id=fid,booking_id=winner.booking_id,seat_count=1))
    assert error.value.code() == grpc.StatusCode.FAILED_PRECONDITION


def test_cache_ttl_invalidation_and_status(stub):
    fid = make_flight()
    redis = Sentinel([('sentinel-1',26379),('sentinel-2',26379),('sentinel-3',26379)],socket_timeout=2).master_for('flight-master')
    req = pb.GetFlightRequest(flight_id=fid)
    search = pb.SearchFlightsRequest(origin='SVO',destination='LED')
    stub.GetFlight(req)
    stub.GetFlight(req)
    stub.SearchFlights(search)
    assert 0 < redis.ttl('flight:' + fid) <= 600
    assert 0 < redis.ttl('search:SVO:LED:all') <= 600
    bid = str(uuid4())
    stub.ReserveSeats(pb.ReserveSeatsRequest(flight_id=fid,booking_id=bid,seat_count=2))
    assert stub.GetFlight(req).available_seats == 8
    assert next(f for f in stub.SearchFlights(search).flights if f.id == fid).available_seats == 8
    stub.ReleaseReservation(pb.ReleaseReservationRequest(booking_id=bid))
    assert stub.GetFlight(req).available_seats == 10
    stub.UpdateFlight(pb.UpdateFlightRequest(flight_id=fid,status=pb.CANCELLED,price_minor=777000))
    assert stub.GetFlight(req).price_minor == 777000
    assert fid not in [f.id for f in stub.SearchFlights(search).flights]


def test_durable_compensation_and_delayed_reserve_fence(stub):
    fid, bid, oid = make_flight(), str(uuid4()), str(uuid4())
    with psycopg.connect(BD) as conn:
        conn.execute("INSERT INTO operations(id,booking_id,kind) VALUES (%s,%s,'CREATE')", (oid,bid))
    stub.ReserveSeats(pb.ReserveSeatsRequest(flight_id=fid,booking_id=bid,seat_count=2))
    db = pool(BD)
    try:
        recover_operation(SimpleNamespace(db=db,flights=stub), oid)
    finally:
        db.close()
    assert stub.GetFlight(pb.GetFlightRequest(flight_id=fid)).available_seats == 10
    with psycopg.connect(BD) as conn:
        assert conn.execute('SELECT state FROM operations WHERE id=%s',(oid,)).fetchone()[0] == 'COMPENSATED'
        assert conn.execute('SELECT count(*) FROM bookings WHERE id=%s',(bid,)).fetchone()[0] == 0
    unknown = str(uuid4())
    stub.ReleaseReservation(pb.ReleaseReservationRequest(booking_id=unknown))
    with pytest.raises(grpc.RpcError) as error:
        stub.ReserveSeats(pb.ReserveSeatsRequest(flight_id=fid,booking_id=unknown,seat_count=1))
    assert error.value.code() == grpc.StatusCode.FAILED_PRECONDITION


def test_lost_reserve_response_is_retried_without_duplicate(stub):
    from app.resilience import CircuitBreaker, ResilienceInterceptor
    fid, bid = make_flight(2), str(uuid4())
    calls = []
    class LoseFirstReply(rpc.FlightServiceServicer):
        def ReserveSeats(self, payload, context):
            result = stub.ReserveSeats(payload)
            calls.append(result.id)
            if len(calls) == 1:
                context.abort(grpc.StatusCode.UNAVAILABLE, 'simulated lost reply after commit')
            return result
    server = grpc.server(ThreadPoolExecutor(max_workers=2))
    rpc.add_FlightServiceServicer_to_server(LoseFirstReply(), server)
    port = server.add_insecure_port('127.0.0.1:0')
    server.start()
    channel = grpc.intercept_channel(grpc.insecure_channel(f'127.0.0.1:{port}'),
                                     ResilienceInterceptor('test',CircuitBreaker(),backoff=0))
    try:
        result = rpc.FlightServiceStub(channel).ReserveSeats(pb.ReserveSeatsRequest(flight_id=fid,booking_id=bid,seat_count=1))
        assert len(calls) == 2 and calls == [result.id,result.id]
        assert stub.GetFlight(pb.GetFlightRequest(flight_id=fid)).available_seats == 1
    finally:
        channel.close()
        server.stop(0).wait()
        stub.ReleaseReservation(pb.ReleaseReservationRequest(booking_id=bid))


@pytest.mark.parametrize('failed_statement', ['INSERT INTO bookings', "UPDATE operations SET state='DONE'"])
def test_booking_write_failure_compensates_real_reservation(stub, monkeypatch, failed_statement):
    from contextlib import contextmanager
    from app.booking import app, create_booking, CreateBooking
    fid = make_flight(3)
    db = pool(BD)
    class FailBookingInsert:
        @contextmanager
        def connection(self):
            with db.connection() as conn:
                class Connection:
                    def execute(self, sql, params=None):
                        if sql.startswith(failed_statement):
                            raise psycopg.InterfaceError('simulated Booking write failure')
                        return conn.execute(sql,params)
                yield Connection()
    monkeypatch.setattr(app.state, 'db', FailBookingInsert(), raising=False)
    monkeypatch.setattr(app.state, 'flights', stub, raising=False)
    user = str(uuid4())
    try:
        with pytest.raises(psycopg.InterfaceError):
            create_booking(CreateBooking(user_id=user,flight_id=fid,passenger_name='Test',passenger_email='test@example.com',seat_count=2))
        assert stub.GetFlight(pb.GetFlightRequest(flight_id=fid)).available_seats == 3
        with psycopg.connect(BD) as conn:
            assert conn.execute('SELECT count(*) FROM bookings WHERE user_id=%s',(user,)).fetchone()[0] == 0
        with psycopg.connect(FD) as conn:
            assert conn.execute('SELECT status FROM seat_reservations WHERE flight_id=%s',(fid,)).fetchone()[0] == 'RELEASED'
    finally:
        db.close()


def test_reservation_transaction_rolls_back_if_cache_step_fails():
    from app.flight import Flights
    fid, bid = make_flight(2), str(uuid4())
    class BrokenCache:
        def invalidate(self, conn):
            raise RuntimeError('simulated failure before commit')
    db = pool(FD)
    try:
        with pytest.raises(RuntimeError):
            Flights(db,BrokenCache()).ReserveSeats(pb.ReserveSeatsRequest(flight_id=fid,booking_id=bid,seat_count=1),None)
        with psycopg.connect(FD) as conn:
            assert conn.execute('SELECT available_seats FROM flights WHERE id=%s',(fid,)).fetchone()[0] == 2
            assert conn.execute('SELECT count(*) FROM seat_reservations WHERE booking_id=%s',(bid,)).fetchone()[0] == 0
    finally:
        db.close()


@pytest.mark.parametrize('column,value', [
    ('available_seats',-1),('available_seats',11),('total_seats',0),
    ('price_minor',0),('status','INVALID'),('origin','xxx'),
    ('destination','SVO'),('arrival_time','2026-01-01 00:00Z'),
])
def test_database_flight_constraints(column,value):
    fid = make_flight()
    from psycopg import sql
    with pytest.raises(psycopg.errors.CheckViolation):
        with psycopg.connect(FD) as conn:
            conn.execute(sql.SQL('UPDATE flights SET {}=%s WHERE id=%s').format(sql.Identifier(column)), (value,fid))


def test_unique_flight_date_reservation_and_automatic_migrations(stub):
    from app.db import migrate
    fid, bid = make_flight(), str(uuid4())
    with psycopg.connect(FD) as conn:
        number = conn.execute('SELECT flight_number FROM flights WHERE id=%s',(fid,)).fetchone()[0]
    with pytest.raises(psycopg.errors.UniqueViolation):
        with psycopg.connect(FD) as conn:
            conn.execute("INSERT INTO flights VALUES (%s,%s,'SU','VKO','LED','2026-12-01 15:00Z','2026-12-01 17:00Z',10,10,100,'RUB','SCHEDULED')", (uuid4(),number))
    stub.ReserveSeats(pb.ReserveSeatsRequest(flight_id=fid,booking_id=bid,seat_count=1))
    with pytest.raises(psycopg.errors.UniqueViolation):
        with psycopg.connect(FD) as conn:
            conn.execute("INSERT INTO seat_reservations(id,booking_id,flight_id,seat_count,status) VALUES (%s,%s,%s,1,'ACTIVE')", (uuid4(),bid,fid))
    stub.ReleaseReservation(pb.ReleaseReservationRequest(booking_id=bid))
    for dsn, service, expected in [(FD,'flight',2),(BD,'booking',1)]:
        migrate(dsn,service)
        migrate(dsn,service)
        with psycopg.connect(dsn) as conn:
            assert conn.execute('SELECT count(*) FROM schema_migrations').fetchone()[0] == expected


def test_search_date_optional_and_all_statuses(stub):
    from datetime import datetime, timezone
    ids = [make_flight() for _ in range(4)]
    for fid, status in zip(ids,[pb.SCHEDULED,pb.DEPARTED,pb.CANCELLED,pb.COMPLETED]):
        stub.UpdateFlight(pb.UpdateFlightRequest(flight_id=fid,status=status))
    request = pb.SearchFlightsRequest(origin='SVO',destination='LED')
    all_dates = {f.id for f in stub.SearchFlights(request).flights}
    request.date.FromDatetime(datetime(2026,12,1,tzinfo=timezone.utc))
    same_date = {f.id for f in stub.SearchFlights(request).flights}
    assert ids[0] in all_dates & same_date
    assert not set(ids[1:]) & (all_dates | same_date)
    request.date.FromDatetime(datetime(2026,12,2,tzinfo=timezone.utc))
    assert not set(ids) & {f.id for f in stub.SearchFlights(request).flights}
    request.date.FromDatetime(datetime(9999,12,31,tzinfo=timezone.utc))
    assert not stub.SearchFlights(request).flights


@pytest.mark.parametrize('method,payload,code', [
    ('GetFlight',pb.GetFlightRequest(flight_id=str(uuid4())),grpc.StatusCode.NOT_FOUND),
    ('GetFlight',pb.GetFlightRequest(flight_id='bad-id'),grpc.StatusCode.INVALID_ARGUMENT),
    ('ReserveSeats',pb.ReserveSeatsRequest(flight_id=str(uuid4()),booking_id=str(uuid4()),seat_count=1),grpc.StatusCode.NOT_FOUND),
    ('ReserveSeats',pb.ReserveSeatsRequest(flight_id=FLIGHT,booking_id=str(uuid4()),seat_count=0),grpc.StatusCode.INVALID_ARGUMENT),
    ('SearchFlights',pb.SearchFlightsRequest(origin='SVO'),grpc.StatusCode.INVALID_ARGUMENT),
    ('UpdateFlight',pb.UpdateFlightRequest(flight_id=FLIGHT,price_minor=0),grpc.StatusCode.INVALID_ARGUMENT),
    ('UpdateFlight',pb.UpdateFlightRequest(flight_id=str(uuid4()),price_minor=100),grpc.StatusCode.NOT_FOUND),
])
def test_grpc_business_errors(stub,method,payload,code):
    with pytest.raises(grpc.RpcError) as error:
        getattr(stub,method)(payload)
    assert error.value.code() == code


def test_old_redis_value_cannot_survive_committed_mutation(stub):
    fid = make_flight()
    redis = Sentinel([('sentinel-1',26379),('sentinel-2',26379),('sentinel-3',26379)],socket_timeout=2).master_for('flight-master')
    request = pb.GetFlightRequest(flight_id=fid)
    stub.GetFlight(request)
    stale = redis.get('flight:' + fid)
    stub.UpdateFlight(pb.UpdateFlightRequest(flight_id=fid,price_minor=123456))
    redis.set('flight:' + fid, stale, ex=300)  # old replica contents after failover
    assert stub.GetFlight(request).price_minor == 123456
    for pattern in ('flight:*','search:*'):
        for key in redis.scan_iter(match=pattern):
            assert 0 < redis.ttl(key) <= 600


def test_proto_contract_timestamp_and_enums():
    service = pb.DESCRIPTOR.services_by_name['FlightService']
    assert set(service.methods_by_name) == {'SearchFlights','GetFlight','ReserveSeats','ReleaseReservation','UpdateFlight'}
    for message, fields in [(pb.Flight,['departure_time','arrival_time']),(pb.SeatReservation,['created_at']),(pb.SearchFlightsRequest,['date'])]:
        for field in fields:
            assert message.DESCRIPTOR.fields_by_name[field].message_type.full_name == 'google.protobuf.Timestamp'
    assert pb.Flight.DESCRIPTOR.fields_by_name['status'].enum_type is not None
    assert pb.SeatReservation.DESCRIPTOR.fields_by_name['status'].enum_type is not None
    assert set(pb.ReservationStatus.keys()) == {'RESERVATION_STATUS_UNSPECIFIED','ACTIVE','RELEASED','EXPIRED'}


def test_conflicting_retry_and_cancelled_flight(stub):
    fid, bid = make_flight(), str(uuid4())
    stub.ReserveSeats(pb.ReserveSeatsRequest(flight_id=fid,booking_id=bid,seat_count=1))
    with pytest.raises(grpc.RpcError) as error:
        stub.ReserveSeats(pb.ReserveSeatsRequest(flight_id=fid,booking_id=bid,seat_count=2))
    assert error.value.code() == grpc.StatusCode.ALREADY_EXISTS
    stub.ReleaseReservation(pb.ReleaseReservationRequest(booking_id=bid))
    stub.UpdateFlight(pb.UpdateFlightRequest(flight_id=fid,status=pb.CANCELLED))
    with pytest.raises(grpc.RpcError) as error:
        stub.ReserveSeats(pb.ReserveSeatsRequest(flight_id=fid,booking_id=str(uuid4()),seat_count=1))
    assert error.value.code() == grpc.StatusCode.FAILED_PRECONDITION


@pytest.mark.parametrize('kind',['CREATE','CANCEL'])
def test_background_worker_reconciles_interrupted_operations(api,stub,kind):
    import time
    fid = make_flight(5)
    if kind == 'CREATE':
        bid = str(uuid4())
        stub.ReserveSeats(pb.ReserveSeatsRequest(flight_id=fid,booking_id=bid,seat_count=2))
    else:
        response = api.post('/bookings',json={'user_id':str(uuid4()),'flight_id':fid,
                                            'passenger_name':'Test','passenger_email':'test@example.com','seat_count':2})
        assert response.status_code == 201
        bid = response.json()['id']
        stub.ReleaseReservation(pb.ReleaseReservationRequest(booking_id=bid))
    oid = str(uuid4())
    with psycopg.connect(BD) as conn:
        conn.execute("INSERT INTO operations(id,booking_id,kind,created_at) VALUES (%s,%s,%s,now()-interval '1 hour')",(oid,bid,kind))
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        with psycopg.connect(BD) as conn:
            state = conn.execute('SELECT state FROM operations WHERE id=%s',(oid,)).fetchone()[0]
        if state != 'PENDING':
            break
        time.sleep(0.2)
    assert state == ('COMPENSATED' if kind == 'CREATE' else 'DONE')
    assert stub.GetFlight(pb.GetFlightRequest(flight_id=fid)).available_seats == 5
    if kind == 'CANCEL':
        assert api.get('/bookings/' + bid).json()['status'] == 'CANCELLED'
    else:
        assert api.get('/bookings/' + bid).status_code == 404


def test_simultaneous_identical_reserves_and_releases(stub):
    fid, bid = make_flight(10), str(uuid4())
    request = pb.ReserveSeatsRequest(flight_id=fid, booking_id=bid, seat_count=3)
    with ThreadPoolExecutor(max_workers=12) as executor:
        reservations = list(executor.map(lambda _: stub.ReserveSeats(request), range(12)))
    assert len({r.id for r in reservations}) == 1
    assert stub.GetFlight(pb.GetFlightRequest(flight_id=fid)).available_seats == 7
    with ThreadPoolExecutor(max_workers=12) as executor:
        list(executor.map(lambda _: stub.ReleaseReservation(pb.ReleaseReservationRequest(booking_id=bid)), range(12)))
    assert stub.GetFlight(pb.GetFlightRequest(flight_id=fid)).available_seats == 10


def test_release_transaction_rolls_back_both_changes(stub):
    from app.flight import Flights
    fid, bid = make_flight(5), str(uuid4())
    request = pb.ReserveSeatsRequest(flight_id=fid, booking_id=bid, seat_count=2)
    stub.ReserveSeats(request)
    class BrokenCache:
        def invalidate(self, conn):
            raise RuntimeError('failure before cancellation commit')
    db = pool(FD)
    try:
        with pytest.raises(RuntimeError):
            Flights(db, BrokenCache()).ReleaseReservation(pb.ReleaseReservationRequest(booking_id=bid), None)
        with psycopg.connect(FD) as conn:
            assert conn.execute('SELECT available_seats FROM flights WHERE id=%s', (fid,)).fetchone()[0] == 3
            assert conn.execute('SELECT status FROM seat_reservations WHERE booking_id=%s', (bid,)).fetchone()[0] == 'ACTIVE'
        assert stub.ReserveSeats(request).status == pb.ACTIVE
    finally:
        db.close()
        stub.ReleaseReservation(pb.ReleaseReservationRequest(booking_id=bid))


def test_all_reserve_replies_lost_compensates_without_booking(stub, monkeypatch):
    from app.booking import app, create_booking, CreateBooking
    from app.resilience import CircuitBreaker, ResilienceInterceptor
    fid, user, calls = make_flight(2), str(uuid4()), []

    class LoseReplies(rpc.FlightServiceServicer):
        def GetFlight(self, request, context):
            calls.append('GetFlight')
            return stub.GetFlight(request)

        def ReserveSeats(self, request, context):
            result = stub.ReserveSeats(request)
            calls.append(('ReserveSeats', result.id))
            context.abort(grpc.StatusCode.UNAVAILABLE, 'reply lost after commit')

        def ReleaseReservation(self, request, context):
            calls.append('ReleaseReservation')
            return stub.ReleaseReservation(request)

    server = grpc.server(ThreadPoolExecutor(max_workers=2))
    rpc.add_FlightServiceServicer_to_server(LoseReplies(), server)
    port = server.add_insecure_port('127.0.0.1:0')
    server.start()
    channel = grpc.intercept_channel(grpc.insecure_channel(f'127.0.0.1:{port}'),
                                     ResilienceInterceptor('test', CircuitBreaker(), backoff=0))
    db = pool(BD)
    monkeypatch.setattr(app.state, 'db', db, raising=False)
    monkeypatch.setattr(app.state, 'flights', rpc.FlightServiceStub(channel), raising=False)
    try:
        with pytest.raises(grpc.RpcError) as error:
            create_booking(CreateBooking(user_id=user, flight_id=fid, passenger_name='Test',
                                         passenger_email='test@example.com', seat_count=1))
        assert error.value.code() == grpc.StatusCode.UNAVAILABLE
        assert len(calls) == 5 and calls[0] == 'GetFlight' and calls[-1] == 'ReleaseReservation'
        assert len(set(calls[1:4])) == 1
        assert stub.GetFlight(pb.GetFlightRequest(flight_id=fid)).available_seats == 2
        with psycopg.connect(FD) as conn:
            reservations = conn.execute('SELECT booking_id,status FROM seat_reservations WHERE flight_id=%s',(fid,)).fetchall()
            assert len(reservations) == 1 and reservations[0][1] == 'RELEASED'
        with psycopg.connect(BD) as conn:
            assert conn.execute('SELECT count(*) FROM bookings WHERE user_id=%s',(user,)).fetchone()[0] == 0
            assert conn.execute('SELECT state FROM operations WHERE booking_id=%s',(reservations[0][0],)).fetchone()[0] == 'COMPENSATED'
    finally:
        channel.close()
        server.stop(0).wait()
        db.close()


def test_cache_hit_skips_fetch_and_expired_entry_refills(monkeypatch, caplog):
    import logging
    from app.cache import Cache
    monkeypatch.setenv('REDIS_SENTINELS', 'sentinel-1:26379,sentinel-2:26379,sentinel-3:26379')
    monkeypatch.setenv('CACHE_TTL', '300')
    cache, fid, reads = Cache(), make_flight(), []
    key = 'flight:' + fid
    db = pool(FD)
    caplog.set_level(logging.INFO, logger='app.cache')
    try:
        with db.connection() as conn:
            def fetch():
                reads.append(fid)
                row = conn.execute('SELECT available_seats FROM flights WHERE id=%s',(fid,)).fetchone()
                return pb.Flight(id=fid, available_seats=row['available_seats'])
            assert cache.read(conn,key,pb.Flight,fetch).available_seats == 10
            assert cache.read(conn,key,pb.Flight,fetch).available_seats == 10
            assert len(reads) == 1
            assert 299 <= cache.redis.ttl(key) <= 300
            cache.redis.expire(key, 0)
            assert cache.read(conn,key,pb.Flight,fetch).available_seats == 10
            assert len(reads) == 2
        messages = [r.message for r in caplog.records if key in r.message]
        assert sum('cache miss' in m for m in messages) == 2
        assert sum('cache hit' in m for m in messages) == 1
    finally:
        cache.redis.delete(key)
        db.close()
