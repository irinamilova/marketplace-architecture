import hmac
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from uuid import UUID, uuid4

import grpc
import psycopg
from psycopg_pool import PoolTimeout
import flight_pb2 as pb
import flight_pb2_grpc as rpc
from app.db import migrate, pool
from app.cache import Cache

log = logging.getLogger(__name__)
SELECT_FLIGHT = 'SELECT f.*, a.name AS airline_name FROM flights f JOIN airlines a ON a.code=f.airline_code'


def valid_uuid(value, ctx):
    try:
        return UUID(value)
    except (ValueError, AttributeError):
        ctx.abort(grpc.StatusCode.INVALID_ARGUMENT, 'ID must be a UUID')


def flight_message(row):
    result = pb.Flight(**{key: str(row[key]) for key in ('id', 'flight_number', 'airline_code', 'airline_name', 'origin', 'destination', 'currency')},
                       total_seats=row['total_seats'], available_seats=row['available_seats'],
                       price_minor=row['price_minor'], status=pb.FlightStatus.Value(row['status']))
    result.departure_time.FromDatetime(row['departure_time'])
    result.arrival_time.FromDatetime(row['arrival_time'])
    return result


def reservation_message(row):
    result = pb.SeatReservation(id=str(row['id']), booking_id=str(row['booking_id']), flight_id=str(row['flight_id']),
                                seat_count=row['seat_count'], status=pb.ReservationStatus.Value(row['status']))
    result.created_at.FromDatetime(row['created_at'])
    return result


class AuthAndErrors(grpc.ServerInterceptor):
    def __init__(self, key):
        if not key:
            raise ValueError('GRPC_API_KEY required')
        self.key = key

    def intercept_service(self, continuation, details):
        handler = continuation(details)
        if handler is None:
            return None

        def call(request, ctx):
            supplied = dict(ctx.invocation_metadata()).get('x-api-key', '')
            if not hmac.compare_digest(supplied.encode(), self.key.encode()):
                ctx.abort(grpc.StatusCode.UNAUTHENTICATED, 'Valid service API key required')
            try:
                return handler.unary_unary(request, ctx)
            except (psycopg.OperationalError, PoolTimeout):
                log.exception('database unavailable')
                ctx.abort(grpc.StatusCode.UNAVAILABLE, 'Flight database unavailable')
            except psycopg.Error:
                log.exception('database operation failed')
                ctx.abort(grpc.StatusCode.INTERNAL, 'Flight database operation failed')
        return grpc.unary_unary_rpc_method_handler(call, request_deserializer=handler.request_deserializer,
                                                   response_serializer=handler.response_serializer)


class Flights(rpc.FlightServiceServicer):
    def __init__(self, db, cache):
        self.db, self.cache = db, cache

    def GetFlight(self, request, ctx):
        flight_id = valid_uuid(request.flight_id, ctx)
        with self.db.connection() as conn:
            def fetch():
                row = conn.execute(SELECT_FLIGHT + ' WHERE f.id=%s', (flight_id,)).fetchone()
                if not row:
                    ctx.abort(grpc.StatusCode.NOT_FOUND, 'Flight not found')
                return flight_message(row)
            return self.cache.read(conn, f'flight:{flight_id}', pb.Flight, fetch)

    def SearchFlights(self, request, ctx):
        if not all(re.fullmatch('[A-Z]{3}', value) for value in (request.origin, request.destination)) or request.origin == request.destination:
            ctx.abort(grpc.StatusCode.INVALID_ARGUMENT, 'Use distinct three-letter uppercase IATA codes')
        date = None
        if request.HasField('date'):
            try:
                date = request.date.ToDatetime(tzinfo=timezone.utc)
                if request.date.nanos != 0 or date.time() != datetime.min.time():
                    raise ValueError('date must be midnight UTC')
            except (ValueError, OverflowError):
                ctx.abort(grpc.StatusCode.INVALID_ARGUMENT, 'date must be a valid midnight UTC Timestamp')
        key = f'search:{request.origin}:{request.destination}:{date.date() if date else "all"}'
        with self.db.connection() as conn:
            def fetch():
                sql = SELECT_FLIGHT + " WHERE origin=%s AND destination=%s AND status='SCHEDULED'"
                params = [request.origin, request.destination]
                if date:
                    sql += " AND departure_time >= %s AND departure_time < %s + interval '1 day'"
                    params.extend([date, date])
                rows = conn.execute(sql + ' ORDER BY departure_time, f.id', params).fetchall()
                return pb.SearchFlightsResponse(flights=[flight_message(row) for row in rows])
            return self.cache.read(conn, key, pb.SearchFlightsResponse, fetch)

    def ReserveSeats(self, request, ctx):
        flight_id = valid_uuid(request.flight_id, ctx)
        booking_id = valid_uuid(request.booking_id, ctx)
        if request.seat_count <= 0:
            ctx.abort(grpc.StatusCode.INVALID_ARGUMENT, 'seat_count must be positive')
        with self.db.connection() as conn:
            # Same lock for reserve/release, even when no reservation exists yet.
            conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (str(booking_id),))
            if conn.execute('SELECT 1 FROM reservation_fences WHERE booking_id=%s', (booking_id,)).fetchone():
                ctx.abort(grpc.StatusCode.FAILED_PRECONDITION, 'Booking has already been released')
            old = conn.execute('SELECT * FROM seat_reservations WHERE booking_id=%s', (booking_id,)).fetchone()
            if old:
                if old['flight_id'] != flight_id or old['seat_count'] != request.seat_count:
                    ctx.abort(grpc.StatusCode.ALREADY_EXISTS, 'booking_id reused with different parameters')
                if old['status'] != 'ACTIVE':
                    ctx.abort(grpc.StatusCode.FAILED_PRECONDITION, 'Reservation is no longer active')
                return reservation_message(old)
            flight = conn.execute('SELECT * FROM flights WHERE id=%s FOR UPDATE', (flight_id,)).fetchone()
            if not flight:
                ctx.abort(grpc.StatusCode.NOT_FOUND, 'Flight not found')
            if flight['status'] != 'SCHEDULED':
                ctx.abort(grpc.StatusCode.FAILED_PRECONDITION, 'Flight is not scheduled')
            if flight['available_seats'] < request.seat_count:
                ctx.abort(grpc.StatusCode.RESOURCE_EXHAUSTED, 'Not enough available seats')
            conn.execute('UPDATE flights SET available_seats=available_seats-%s WHERE id=%s', (request.seat_count, flight_id))
            row = conn.execute("INSERT INTO seat_reservations(id,booking_id,flight_id,seat_count,status) VALUES (%s,%s,%s,%s,'ACTIVE') RETURNING *",
                               (uuid4(), booking_id, flight_id, request.seat_count)).fetchone()
            self.cache.invalidate(conn)
            result = reservation_message(row)
        return result  # transaction committed before successful RPC response

    def ReleaseReservation(self, request, ctx):
        booking_id = valid_uuid(request.booking_id, ctx)
        with self.db.connection() as conn:
            conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (str(booking_id),))
            conn.execute('INSERT INTO reservation_fences(booking_id) VALUES (%s) ON CONFLICT DO NOTHING', (booking_id,))
            row = conn.execute('SELECT * FROM seat_reservations WHERE booking_id=%s FOR UPDATE', (booking_id,)).fetchone()
            if row and row['status'] == 'ACTIVE':
                conn.execute('SELECT id FROM flights WHERE id=%s FOR UPDATE', (row['flight_id'],))
                conn.execute('UPDATE flights SET available_seats=available_seats+%s WHERE id=%s', (row['seat_count'], row['flight_id']))
                conn.execute("UPDATE seat_reservations SET status='RELEASED' WHERE booking_id=%s", (booking_id,))
                self.cache.invalidate(conn)
        return pb.ReleaseReservationResponse(booking_id=str(booking_id), status=pb.RELEASED)

    def UpdateFlight(self, request, ctx):
        flight_id = valid_uuid(request.flight_id, ctx)
        if not (request.HasField('price_minor') or request.HasField('status')):
            ctx.abort(grpc.StatusCode.INVALID_ARGUMENT, 'Provide price_minor or status')
        if request.HasField('price_minor') and request.price_minor <= 0:
            ctx.abort(grpc.StatusCode.INVALID_ARGUMENT, 'price_minor must be positive')
        if request.HasField('status') and request.status not in (pb.SCHEDULED, pb.DEPARTED, pb.CANCELLED, pb.COMPLETED):
            ctx.abort(grpc.StatusCode.INVALID_ARGUMENT, 'Invalid flight status')
        with self.db.connection() as conn:
            old = conn.execute('SELECT * FROM flights WHERE id=%s FOR UPDATE', (flight_id,)).fetchone()
            if not old:
                ctx.abort(grpc.StatusCode.NOT_FOUND, 'Flight not found')
            conn.execute('UPDATE flights SET price_minor=%s,status=%s WHERE id=%s', (
                request.price_minor if request.HasField('price_minor') else old['price_minor'],
                pb.FlightStatus.Name(request.status) if request.HasField('status') else old['status'], flight_id))
            self.cache.invalidate(conn)
            result = flight_message(conn.execute(SELECT_FLIGHT + ' WHERE f.id=%s', (flight_id,)).fetchone())
        return result


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
    dsn = os.environ['DATABASE_URL']
    migrate(dsn, 'flight')
    db = pool(dsn)
    server = grpc.server(ThreadPoolExecutor(max_workers=16), interceptors=[AuthAndErrors(os.environ['GRPC_API_KEY'])])
    rpc.add_FlightServiceServicer_to_server(Flights(db, Cache()), server)
    server.add_insecure_port('[::]:50051')
    server.start()
    log.info('Flight gRPC listening on :50051')
    server.wait_for_termination()


if __name__ == '__main__':
    main()
