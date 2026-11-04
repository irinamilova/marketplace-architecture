CREATE TABLE airlines (
    code varchar(3) PRIMARY KEY CHECK (code ~ '^[A-Z0-9]{2,3}$'),
    name text NOT NULL CHECK (length(trim(name)) > 0)
);
CREATE TABLE flights (
    id uuid PRIMARY KEY,
    flight_number varchar(12) NOT NULL CHECK (length(trim(flight_number)) > 0),
    airline_code varchar(3) NOT NULL REFERENCES airlines(code),
    origin char(3) NOT NULL CHECK (origin ~ '^[A-Z]{3}$'),
    destination char(3) NOT NULL CHECK (destination ~ '^[A-Z]{3}$'),
    departure_time timestamptz NOT NULL,
    arrival_time timestamptz NOT NULL,
    total_seats integer NOT NULL CHECK (total_seats > 0),
    available_seats integer NOT NULL CHECK (available_seats >= 0 AND available_seats <= total_seats),
    price_minor bigint NOT NULL CHECK (price_minor > 0),
    currency char(3) NOT NULL DEFAULT 'RUB' CHECK (currency ~ '^[A-Z]{3}$'),
    status text NOT NULL CHECK (status IN ('SCHEDULED', 'DEPARTED', 'CANCELLED', 'COMPLETED')),
    CHECK (origin <> destination),
    CHECK (arrival_time > departure_time)
);
CREATE UNIQUE INDEX flights_number_departure_date ON flights (flight_number, ((departure_time AT TIME ZONE 'UTC')::date));
CREATE INDEX flights_search ON flights(origin, destination, departure_time) WHERE status = 'SCHEDULED';
CREATE TABLE seat_reservations (
    id uuid PRIMARY KEY,
    booking_id uuid NOT NULL UNIQUE,
    flight_id uuid NOT NULL REFERENCES flights(id),
    seat_count integer NOT NULL CHECK (seat_count > 0),
    status text NOT NULL CHECK (status IN ('ACTIVE', 'RELEASED', 'EXPIRED')),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX reservations_flight ON seat_reservations(flight_id);
-- Fence protects against delayed ReserveSeats arriving after compensation.
CREATE TABLE reservation_fences (booking_id uuid PRIMARY KEY, created_at timestamptz NOT NULL DEFAULT now());
-- Durable cache generation: prevents stale Redis data after outages/failover.
CREATE TABLE cache_epoch (id integer PRIMARY KEY CHECK(id = 1), value bigint NOT NULL);
INSERT INTO cache_epoch VALUES (1, 0);
