import logging
import os
import threading
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timezone
from uuid import UUID, uuid4
from pathlib import Path
from typing import Literal

import grpc
import psycopg
from psycopg_pool import PoolTimeout
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, EmailStr, Field
import flight_pb2 as pb
from app.api_errors import responses
from app.db import migrate, pool
from app.resilience import client

log = logging.getLogger(__name__)


class CreateBooking(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid', json_schema_extra={
        'example': {'user_id': 'student-1', 'flight_id': '11111111-1111-4111-8111-111111111111',
                    'passenger_name': 'Ирина Милова', 'passenger_email': 'irina@example.com', 'seat_count': 2}
    })
    user_id: str = Field(min_length=1, max_length=128)
    flight_id: UUID
    passenger_name: str = Field(min_length=1, max_length=200)
    passenger_email: EmailStr
    seat_count: int = Field(gt=0, le=2**31 - 1, strict=True)


class BookingResponse(BaseModel):
    model_config = ConfigDict(json_schema_extra={'example': {
        'id': 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'user_id': 'student-1',
        'flight_id': '11111111-1111-4111-8111-111111111111',
        'passenger_name': 'Ирина Милова', 'passenger_email': 'irina@example.com',
        'seat_count': 2, 'total_price_minor': 1100000, 'currency': 'RUB',
        'status': 'CONFIRMED', 'created_at': '2026-10-23T12:00:00Z'
    }})
    id: UUID
    user_id: str
    flight_id: UUID
    passenger_name: str
    passenger_email: str
    seat_count: int
    total_price_minor: int = Field(gt=0, description='Снимок общей стоимости в копейках')
    currency: str
    status: Literal['CONFIRMED', 'CANCELLED']
    created_at: datetime


class FlightResponse(BaseModel):
    model_config = ConfigDict(json_schema_extra={'example': {
        'id': '11111111-1111-4111-8111-111111111111', 'flight_number': 'SU1234',
        'airline_code': 'SU', 'airline_name': 'Аэрофлот', 'origin': 'SVO', 'destination': 'LED',
        'departure_time': '2026-11-20T09:00:00Z', 'arrival_time': '2026-11-20T10:30:00Z',
        'total_seats': 100, 'available_seats': 100, 'price_minor': 550000,
        'currency': 'RUB', 'status': 'SCHEDULED'
    }})
    id: UUID
    flight_number: str
    airline_code: str
    airline_name: str
    origin: str
    destination: str
    departure_time: datetime
    arrival_time: datetime
    total_seats: int
    available_seats: int
    price_minor: int = Field(gt=0, description='Цена одного места в копейках')
    currency: str
    status: Literal['SCHEDULED', 'DEPARTED', 'CANCELLED', 'COMPLETED']


def flight_json(f):
    return FlightResponse(**{key: getattr(f, key) for key in ('id', 'flight_number', 'airline_code', 'airline_name', 'origin',
                           'destination', 'total_seats', 'available_seats', 'price_minor', 'currency')},
                          departure_time=f.departure_time.ToDatetime(tzinfo=timezone.utc),
                          arrival_time=f.arrival_time.ToDatetime(tzinfo=timezone.utc), status=pb.FlightStatus.Name(f.status))


def recover_operation(state, operation_id):
    with state.db.connection() as conn:
        op = conn.execute("SELECT * FROM operations WHERE id=%s AND state='PENDING' FOR UPDATE SKIP LOCKED", (operation_id,)).fetchone()
        if not op:
            return
        booking = conn.execute('SELECT * FROM bookings WHERE id=%s FOR UPDATE', (op['booking_id'],)).fetchone()
        if op['kind'] == 'CREATE' and booking:
            conn.execute("UPDATE operations SET state='DONE' WHERE id=%s", (op['id'],))
            return
        # Idempotent release also fences an unknown booking against delayed reserve.
        state.flights.ReleaseReservation(pb.ReleaseReservationRequest(booking_id=str(op['booking_id'])))
        if op['kind'] == 'CANCEL' and booking:
            conn.execute("UPDATE bookings SET status='CANCELLED' WHERE id=%s", (op['booking_id'],))
        conn.execute('UPDATE operations SET state=%s WHERE id=%s', ('COMPENSATED' if op['kind'] == 'CREATE' else 'DONE', op['id']))
        log.info('saga recovered operation=%s kind=%s', op['id'], op['kind'])


def recover_loop(state, stop):
    while not stop.wait(float(os.getenv('SAGA_INTERVAL', '3'))):
        try:
            with state.db.connection() as conn:
                pending = conn.execute("SELECT id FROM operations WHERE state='PENDING' AND created_at < now() - (%s * interval '1 second') ORDER BY created_at LIMIT 100",
                                       (float(os.getenv('SAGA_GRACE_SECONDS', '30')),)).fetchall()
            for row in pending:
                try:
                    recover_operation(state, row['id'])
                except Exception:
                    log.exception('saga recovery deferred operation=%s', row['id'])
        except Exception:
            log.exception('saga scan deferred')


@asynccontextmanager
async def lifespan(app):
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
    dsn = os.environ['DATABASE_URL']
    migrate(dsn, 'booking')
    app.state.db = pool(dsn)
    app.state.flights, app.state.channel, app.state.breaker = client()
    stop = threading.Event()
    worker = threading.Thread(target=recover_loop, args=(app.state, stop), daemon=True)
    worker.start()
    yield
    stop.set()
    worker.join(timeout=10)
    app.state.channel.close()
    app.state.db.close()


app = FastAPI(title='Flight Booking API', version='1.0.0', lifespan=lifespan, redoc_url=None,
              description='Prices are integer kopecks (RUB). Dates use UTC. Separate Booking and Flight databases.')


STATIC = Path(__file__).with_name('static')
app.mount('/static', StaticFiles(directory=STATIC), name='static')


@app.get('/', include_in_schema=False)
def website():
    return FileResponse(STATIC / 'index.html')


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc):
    code = {400: 'BAD_REQUEST', 404: 'NOT_FOUND', 409: 'FAILED_PRECONDITION', 503: 'UNAVAILABLE'}.get(exc.status_code, 'HTTP_ERROR')
    return JSONResponse(status_code=exc.status_code, content={'error': code, 'detail': exc.detail}, headers=exc.headers)


@app.exception_handler(grpc.RpcError)
async def grpc_error(request: Request, exc):
    status = {grpc.StatusCode.INVALID_ARGUMENT: 400, grpc.StatusCode.NOT_FOUND: 404,
              grpc.StatusCode.RESOURCE_EXHAUSTED: 409, grpc.StatusCode.ALREADY_EXISTS: 409,
              grpc.StatusCode.FAILED_PRECONDITION: 409, grpc.StatusCode.UNAVAILABLE: 503,
              grpc.StatusCode.DEADLINE_EXCEEDED: 504, grpc.StatusCode.UNAUTHENTICATED: 502}.get(exc.code(), 502)
    return JSONResponse(status_code=status, content={'error': exc.code().name, 'detail': exc.details()})


@app.exception_handler(psycopg.Error)
@app.exception_handler(PoolTimeout)
async def database_error(request: Request, exc):
    log.error('booking database error', exc_info=exc)
    return JSONResponse(status_code=503, content={'error': 'DATABASE_UNAVAILABLE', 'detail': 'Booking database operation failed; pending operations will be reconciled'})


@app.get('/flights', response_model=list[FlightResponse],
         responses=responses(400, 502, 503, 504, examples={400: {'invalid_argument'}, 503: {'circuit_open', 'recovery_probe', 'flight_database'}}))
def search_flights(origin: str = Query(pattern='^[A-Z]{3}$'), destination: str = Query(pattern='^[A-Z]{3}$'), date: date | None = None):
    request = pb.SearchFlightsRequest(origin=origin, destination=destination)
    if date:
        request.date.FromDatetime(datetime.combine(date, time.min, timezone.utc))
    return [flight_json(f) for f in app.state.flights.SearchFlights(request).flights]


@app.get('/flights/{flight_id}', response_model=FlightResponse,
         responses=responses(404, 502, 503, 504, examples={404: {'flight'}, 503: {'circuit_open', 'recovery_probe', 'flight_database'}}))
def get_flight(flight_id: UUID):
    return flight_json(app.state.flights.GetFlight(pb.GetFlightRequest(flight_id=str(flight_id))))


@app.post('/bookings', status_code=201, response_model=BookingResponse,
          responses=responses(400, 404, 409, 502, 503, 504, examples={400: {'price_overflow'}, 404: {'flight'}, 409: {'no_seats', 'flight_status', 'released', 'duplicate'}}))
def create_booking(body: CreateBooking):
    flight = app.state.flights.GetFlight(pb.GetFlightRequest(flight_id=str(body.flight_id)))
    total = body.seat_count * flight.price_minor
    if total > 2**63 - 1:
        raise HTTPException(400, 'Total price exceeds supported range')
    booking_id, operation_id = uuid4(), uuid4()
    # Commit durable intent separately; a crash cannot lose the compensation job.
    with app.state.db.connection() as conn:
        conn.execute("INSERT INTO operations(id,booking_id,kind) VALUES (%s,%s,'CREATE')", (operation_id, booking_id))
    try:
        with app.state.db.connection() as conn:
            op = conn.execute('SELECT state FROM operations WHERE id=%s FOR UPDATE', (operation_id,)).fetchone()
            if op['state'] != 'PENDING':
                raise HTTPException(503, 'Creation was already compensated; submit a new request')
            app.state.flights.ReserveSeats(pb.ReserveSeatsRequest(flight_id=str(body.flight_id), booking_id=str(booking_id), seat_count=body.seat_count))
            booking = conn.execute("INSERT INTO bookings(id,user_id,flight_id,passenger_name,passenger_email,seat_count,total_price_minor,currency,status) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'CONFIRMED') RETURNING *",
                                   (booking_id, body.user_id, body.flight_id, body.passenger_name, str(body.passenger_email), body.seat_count, total, flight.currency)).fetchone()
            conn.execute("UPDATE operations SET state='DONE' WHERE id=%s", (operation_id,))
        return booking
    except Exception:
        try:
            recover_operation(app.state, operation_id)
        except Exception:
            log.exception('compensation persisted for background recovery operation=%s', operation_id)
        raise


@app.get('/bookings', response_model=list[BookingResponse],
         responses=responses(503, examples={503: {'booking_database'}}))
def list_bookings(user_id: str = Query(min_length=1, max_length=128)):
    with app.state.db.connection() as conn:
        return conn.execute('SELECT * FROM bookings WHERE user_id=%s ORDER BY created_at,id', (user_id,)).fetchall()


@app.get('/bookings/{booking_id}', response_model=BookingResponse,
         responses=responses(404, 503, examples={404: {'booking'}, 503: {'booking_database'}}))
def get_booking(booking_id: UUID):
    with app.state.db.connection() as conn:
        row = conn.execute('SELECT * FROM bookings WHERE id=%s', (booking_id,)).fetchone()
        if not row:
            raise HTTPException(404, 'Booking not found')
        return row


@app.post('/bookings/{booking_id}/cancel', response_model=BookingResponse,
          responses=responses(404, 409, 502, 503, 504, examples={404: {'booking'}, 409: {'booking_cancelled'}, 503: {'booking_database', 'circuit_open', 'recovery_probe', 'flight_database'}}))
def cancel_booking(booking_id: UUID):
    booking = get_booking(booking_id)
    if booking['status'] != 'CONFIRMED':
        raise HTTPException(409, 'Booking must be CONFIRMED to cancel')
    with app.state.db.connection() as conn:
        conn.execute("INSERT INTO operations(id,booking_id,kind) VALUES (%s,%s,'CANCEL') ON CONFLICT (booking_id,kind) DO NOTHING", (uuid4(), booking_id))
    with app.state.db.connection() as conn:
        op = conn.execute("SELECT * FROM operations WHERE booking_id=%s AND kind='CANCEL' FOR UPDATE", (booking_id,)).fetchone()
        row = conn.execute('SELECT * FROM bookings WHERE id=%s FOR UPDATE', (booking_id,)).fetchone()
        if row['status'] != 'CONFIRMED':
            raise HTTPException(409, 'Booking must be CONFIRMED to cancel')
        app.state.flights.ReleaseReservation(pb.ReleaseReservationRequest(booking_id=str(booking_id)))
        row = conn.execute("UPDATE bookings SET status='CANCELLED' WHERE id=%s RETURNING *", (booking_id,)).fetchone()
        conn.execute("UPDATE operations SET state='DONE' WHERE id=%s", (op['id'],))
    return row
