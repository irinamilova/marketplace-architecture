CREATE TABLE bookings (
    id uuid PRIMARY KEY,
    user_id text NOT NULL CHECK (length(trim(user_id)) BETWEEN 1 AND 128),
    flight_id uuid NOT NULL,
    passenger_name text NOT NULL CHECK (length(trim(passenger_name)) BETWEEN 1 AND 200),
    passenger_email text NOT NULL,
    seat_count integer NOT NULL CHECK (seat_count > 0),
    total_price_minor bigint NOT NULL CHECK (total_price_minor > 0),
    currency char(3) NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
    status text NOT NULL CHECK (status IN ('CONFIRMED','CANCELLED')),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX bookings_user ON bookings(user_id, created_at);
-- Durable saga intent is NOT a booking. Created before the remote mutation.
CREATE TABLE operations (
    id uuid PRIMARY KEY,
    booking_id uuid NOT NULL,
    kind text NOT NULL CHECK (kind IN ('CREATE','CANCEL')),
    state text NOT NULL DEFAULT 'PENDING' CHECK (state IN ('PENDING','DONE','COMPENSATED')),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (booking_id, kind)
);
CREATE INDEX operations_pending ON operations(created_at) WHERE state = 'PENDING';
