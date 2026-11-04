import os
from types import SimpleNamespace
from uuid import uuid4
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
import grpc
import httpx
import psycopg
from psycopg import sql
import pytest
from redis.sentinel import Sentinel
import flight_pb2 as pb
from app.resilience import CircuitBreaker, ResilienceInterceptor, client

FD=os.getenv('TEST_FLIGHT_DSN', 'postgresql://flight:flight-local-password@flight-db/flights')
BD=os.environ['DATABASE_URL']

@pytest.fixture
def stub():
    s,c,_=client()
    yield s
    c.close()

@pytest.fixture
def api():
    with httpx.Client(base_url='http://localhost:8000',timeout=20) as c:
        yield c


def flight(seats=5):
    fid=str(uuid4())
    with psycopg.connect(FD) as c:
        c.execute("INSERT INTO flights VALUES (%s,%s,'SU','VKO','LED','2026-12-15 00:00Z','2026-12-15 02:00Z',%s,%s,12345,'RUB','SCHEDULED')",(fid,fid[:12],seats,seats))
    return fid


def body(fid,**kwargs):
    return dict(user_id=str(uuid4()),flight_id=fid,passenger_name='Аудит',passenger_email='audit@example.com',seat_count=1,**kwargs)


class Outcome:
    def __init__(self,code): self.status=code
    def code(self): return self.status


@pytest.mark.parametrize('code',[grpc.StatusCode.INTERNAL,grpc.StatusCode.UNKNOWN])
def test_half_open_server_failure_must_reopen(code):
    now=[0.0]
    cb=CircuitBreaker(threshold=1,reset_timeout=10,clock=lambda:now[0])
    ticket,_=cb.enter()
    cb.finish(ticket,True)
    now[0]=11
    mw=ResilienceInterceptor('key',cb,sleep=lambda _:None)
    calls=[]
    def call(details,request):
        calls.append(request)
        return Outcome(code)
    mw.intercept_unary_unary(call,SimpleNamespace(method='/test',timeout=None,metadata=(),credentials=None),None)
    assert len(calls)==1
    assert cb.state=='OPEN', f'Failed probe {code.name} incorrectly changed state to {cb.state}'


@pytest.mark.parametrize('code',[grpc.StatusCode.INTERNAL,grpc.StatusCode.UNKNOWN])
def test_repeated_server_errors_must_open_circuit(code):
    cb=CircuitBreaker(threshold=2)
    mw=ResilienceInterceptor('key',cb,sleep=lambda _:None)
    details=SimpleNamespace(method='/test',timeout=None,metadata=(),credentials=None)
    for _ in range(2):
        mw.intercept_unary_unary(lambda d,r:Outcome(code),details,None)
    assert cb.state=='OPEN',f'{code.name} errors were ignored: {cb.state}'


@pytest.mark.parametrize('field,value',[
    ('seat_count',-1),('seat_count',0),('seat_count',True),('seat_count',1.5),
    ('seat_count','2'),('seat_count',2147483648),('passenger_name','   '),
    ('passenger_email','bad'),('flight_id','not-uuid'),('user_id',''),
])
def test_bad_rest_input_is_rejected_without_writes(api,field,value):
    payload=body(flight()); payload[field]=value
    with psycopg.connect(BD) as c:
        before=c.execute('SELECT count(*) FROM bookings').fetchone()[0]
    r=api.post('/bookings',json=payload)
    assert r.status_code==422,r.text
    with psycopg.connect(BD) as c:
        assert c.execute('SELECT count(*) FROM bookings').fetchone()[0]==before


def test_empty_json_lists_all_missing_fields(api):
    r=api.post('/bookings',json={})
    assert r.status_code==422
    assert {e['loc'][-1] for e in r.json()['detail']}=={'user_id','flight_id','seat_count','passenger_name','passenger_email'}


@pytest.mark.parametrize('column,value',[
    ('seat_count',0),('total_price_minor',0),('status','PENDING'),('user_id',''),('passenger_name','')
])
def test_booking_constraints(api,column,value):
    r=api.post('/bookings',json=body(flight())); assert r.status_code==201,r.text
    bid=r.json()['id']
    try:
        with pytest.raises(psycopg.errors.CheckViolation):
            with psycopg.connect(BD) as c:
                c.execute(sql.SQL('UPDATE bookings SET {}=%s WHERE id=%s').format(sql.Identifier(column)),(value,bid))
    finally:
        assert api.post('/bookings/'+bid+'/cancel').status_code==200


def test_user_filter_excludes_other_users(api):
    payload=body(flight())
    created=api.post('/bookings',json=payload); assert created.status_code==201
    bid=created.json()['id']
    try:
        assert [b['id'] for b in api.get('/bookings',params={'user_id':payload['user_id']}).json()]==[bid]
        assert api.get('/bookings',params={'user_id':str(uuid4())}).json()==[]
    finally:
        api.post('/bookings/'+bid+'/cancel')


def test_overflow_is_rejected_before_reservation(api,stub):
    fid=flight(2)
    stub.UpdateFlight(pb.UpdateFlightRequest(flight_id=fid,price_minor=2**63-1))
    payload=body(fid); payload['seat_count']=2
    r=api.post('/bookings',json=payload)
    assert r.status_code==400,r.text
    assert stub.GetFlight(pb.GetFlightRequest(flight_id=fid)).available_seats==2
    assert api.get('/bookings',params={'user_id':payload['user_id']}).json()==[]


def test_search_route_and_midnight_boundaries(stub):
    fid=flight()
    req=pb.SearchFlightsRequest(origin='VKO',destination='LED')
    req.date.FromDatetime(datetime(2026,12,15,tzinfo=timezone.utc))
    assert fid in {f.id for f in stub.SearchFlights(req).flights}
    req.date.FromDatetime(datetime(2026,12,14,tzinfo=timezone.utc))
    assert fid not in {f.id for f in stub.SearchFlights(req).flights}
    assert fid not in {f.id for f in stub.SearchFlights(pb.SearchFlightsRequest(origin='SVO',destination='LED')).flights}


@pytest.mark.parametrize('seconds,nanos',[(0,1),(1,0),(253402300800,0),(-62135596801,0)])
def test_invalid_grpc_dates(stub,seconds,nanos):
    req=pb.SearchFlightsRequest(origin='VKO',destination='LED'); req.date.seconds=seconds; req.date.nanos=nanos
    with pytest.raises(grpc.RpcError) as e: stub.SearchFlights(req)
    assert e.value.code()==grpc.StatusCode.INVALID_ARGUMENT


def test_fresh_cache_ttl_and_all_mutations_delete_both_keys(stub):
    fid=flight()
    cache=Sentinel([('sentinel-1',26379),('sentinel-2',26379),('sentinel-3',26379)],socket_timeout=2).master_for('flight-master')
    req=pb.GetFlightRequest(flight_id=fid)
    sr=pb.SearchFlightsRequest(origin='VKO',destination='LED')
    keys=['flight:'+fid,'search:VKO:LED:all']
    bid=str(uuid4())
    actions=[lambda:stub.ReserveSeats(pb.ReserveSeatsRequest(flight_id=fid,booking_id=bid,seat_count=1)),
             lambda:stub.ReleaseReservation(pb.ReleaseReservationRequest(booking_id=bid)),
             lambda:stub.UpdateFlight(pb.UpdateFlightRequest(flight_id=fid,price_minor=56789))]
    for action in actions:
        stub.GetFlight(req); stub.SearchFlights(sr)
        assert all(299<=cache.ttl(k)<=600 for k in keys)
        action()
        assert all(cache.exists(k)==0 for k in keys)


def test_concurrent_rest_cancel_single_return_of_seats(api,stub):
    fid=flight()
    r=api.post('/bookings',json=body(fid)); assert r.status_code==201
    bid=r.json()['id']
    def cancel(_):
        return httpx.post('http://localhost:8000/bookings/'+bid+'/cancel',timeout=20).status_code
    with ThreadPoolExecutor(max_workers=8) as ex: codes=list(ex.map(cancel,range(8)))
    assert codes.count(200)==1 and codes.count(409)==7,codes
    assert stub.GetFlight(pb.GetFlightRequest(flight_id=fid)).available_seats==5


def test_foreign_key_and_unique_next_date():
    fid=flight()
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with psycopg.connect(FD) as c:
            c.execute("INSERT INTO seat_reservations(id,booking_id,flight_id,seat_count,status) VALUES (%s,%s,%s,1,'ACTIVE')",(uuid4(),uuid4(),uuid4()))
    with psycopg.connect(FD) as c:
        c.execute("INSERT INTO flights SELECT %s,flight_number,airline_code,origin,destination,departure_time+interval '1 day',arrival_time+interval '1 day',total_seats,available_seats,price_minor,currency,status FROM flights WHERE id=%s",(uuid4(),fid))


def test_corrupt_cache_falls_back_to_database(stub):
    fid=flight()
    stub.GetFlight(pb.GetFlightRequest(flight_id=fid))
    cache=Sentinel([('sentinel-1',26379)],socket_timeout=2).master_for('flight-master')
    key='flight:'+fid
    original=cache.get(key)
    try:
        cache.set(key,original.split(b':',1)[0]+b':\xff',ex=300)
        assert stub.GetFlight(pb.GetFlightRequest(flight_id=fid)).id==fid
    finally:
        cache.delete(key)


def test_compensation_is_durable_when_release_is_unavailable(stub, monkeypatch):
    from contextlib import contextmanager
    from app.booking import app, create_booking, CreateBooking, recover_operation
    from app.db import pool
    from app.resilience import ServiceUnavailable
    fid, user = flight(), str(uuid4())
    db = pool(BD)

    class BrokenWrite:
        @contextmanager
        def connection(self):
            with db.connection() as conn:
                class Connection:
                    def execute(self, query, params=None):
                        if query.startswith('INSERT INTO bookings'):
                            raise psycopg.InterfaceError('injected booking write failure')
                        return conn.execute(query, params)
                yield Connection()

    class BrokenRelease:
        GetFlight = stub.GetFlight
        ReserveSeats = stub.ReserveSeats

        def ReleaseReservation(self, request):
            raise ServiceUnavailable('injected outage during compensation')

    monkeypatch.setattr(app.state, 'db', BrokenWrite(), raising=False)
    monkeypatch.setattr(app.state, 'flights', BrokenRelease(), raising=False)
    try:
        with pytest.raises(psycopg.InterfaceError):
            create_booking(CreateBooking(user_id=user, flight_id=fid, passenger_name='Recovery test',
                                         passenger_email='recovery@example.com', seat_count=2))
        with psycopg.connect(FD) as conn:
            bid, status = conn.execute('SELECT booking_id,status FROM seat_reservations WHERE flight_id=%s', (fid,)).fetchone()
            assert status == 'ACTIVE'
            assert conn.execute('SELECT available_seats FROM flights WHERE id=%s',(fid,)).fetchone()[0] == 3
        with psycopg.connect(BD) as conn:
            assert conn.execute('SELECT count(*) FROM bookings WHERE user_id=%s',(user,)).fetchone()[0] == 0
            oid, state = conn.execute('SELECT id,state FROM operations WHERE booking_id=%s',(bid,)).fetchone()
            assert state == 'PENDING'
        # A new state object models a restarted process: no in-memory intent is needed.
        state = SimpleNamespace(db=db, flights=stub)
        recover_operation(state, oid)
        recover_operation(state, oid)
        assert stub.GetFlight(pb.GetFlightRequest(flight_id=fid)).available_seats == 5
        with psycopg.connect(BD) as conn:
            assert conn.execute('SELECT state FROM operations WHERE id=%s',(oid,)).fetchone()[0] == 'COMPENSATED'
    finally:
        db.close()


def test_website_and_local_assets_are_served(api):
    response = api.get('/')
    assert response.status_code == 200 and 'text/html' in response.headers['content-type']
    assert 'Куда отправимся?' in response.text
    for path, content_type in [('/static/style.css','text/css'),('/static/app.js','javascript')]:
        response = api.get(path)
        assert response.status_code == 200 and content_type in response.headers['content-type']
    assert '/' not in api.get('/openapi.json').json()['paths']
