CREATE KEYSPACE IF NOT EXISTS warehouse
WITH replication = {'class': 'NetworkTopologyStrategy', 'dc1': 3};

CREATE TABLE IF NOT EXISTS warehouse.inventory_by_product_zone (
    warehouse_id text, product_id text, zone_id text,
    available_quantity int, reserved_quantity int, last_event_at timestamp,
    PRIMARY KEY ((warehouse_id, product_id), zone_id)
);

CREATE TABLE IF NOT EXISTS warehouse.inventory_by_product (
    warehouse_id text, product_id text,
    total_available int, total_reserved int, last_event_at timestamp,
    PRIMARY KEY ((warehouse_id, product_id))
);

CREATE TABLE IF NOT EXISTS warehouse.inventory_by_zone (
    warehouse_id text, zone_id text, product_id text,
    available_quantity int, reserved_quantity int, last_event_at timestamp,
    PRIMARY KEY ((warehouse_id, zone_id), product_id)
);

CREATE TABLE IF NOT EXISTS warehouse.orders (
    warehouse_id text, order_id text, status text, items_json text, last_event_at timestamp,
    PRIMARY KEY ((warehouse_id, order_id))
);

CREATE TABLE IF NOT EXISTS warehouse.processed_events (
    warehouse_id text, event_id uuid, status text, plan_json text, original_json text,
    event_type text, event_at timestamp, write_timestamp bigint, schema_version int,
    PRIMARY KEY ((warehouse_id, event_id))
);

CREATE TABLE IF NOT EXISTS warehouse.zones (
    warehouse_id text, zone_id text, capacity int,
    PRIMARY KEY ((warehouse_id), zone_id)
);

INSERT INTO warehouse.zones (warehouse_id, zone_id, capacity) VALUES ('main', 'ZONE-A', 100000);
INSERT INTO warehouse.zones (warehouse_id, zone_id, capacity) VALUES ('main', 'ZONE-B', 100000);
