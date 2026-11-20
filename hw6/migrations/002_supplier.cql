ALTER TABLE warehouse.inventory_by_product_zone ADD supplier_id text;
ALTER TABLE warehouse.inventory_by_zone ADD supplier_id text;
ALTER TABLE warehouse.processed_events ADD supplier_id text;
