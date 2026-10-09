"""Synthetic demo warehouse used by certified-query row checks.

Values are the demo seed's own rows: transactions.txn_type is inbound/outbound
(13 outbound, 2 inbound). shipments.status is in_transit/delivered/delayed.
shipments.supplier_id is not stored on that seed; the column is nullable here
so a certified join can execute and return no match instead of a binder error.
"""

from __future__ import annotations

import duckdb


def open_synthetic_demo_warehouse() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.execute(
        """
        CREATE TABLE locations (
          location_id VARCHAR,
          name VARCHAR,
          capacity_kg DOUBLE,
          current_load_kg DOUBLE,
          location_code VARCHAR,
          is_cold_storage BOOLEAN,
          cctv_camera_id VARCHAR
        )
        """
    )
    con.execute(
        """
        INSERT INTO locations VALUES
          ('WH-A', 'Warehouse A', 100000, 72000, 'WH-A', FALSE, 'CAM-A-01'),
          ('WH-B', 'Warehouse B', 80000, 45000, 'WH-B', FALSE, 'CAM-B-01'),
          ('WH-C', 'Warehouse C', 60000, 58000, 'WH-C', TRUE, 'CAM-C-01'),
          ('WH-D', 'Warehouse D', 120000, 31000, 'WH-D', FALSE, 'CAM-D-01'),
          ('WH-E', 'Warehouse E', 90000, 88000, 'WH-E', FALSE, 'CAM-E-01')
        """
    )
    con.execute(
        """
        CREATE TABLE suppliers (
          supplier_id VARCHAR,
          supplier_name VARCHAR,
          country VARCHAR,
          lead_time_days INTEGER,
          risk_score DOUBLE,
          last_audit_date DATE
        )
        """
    )
    con.execute(
        """
        INSERT INTO suppliers VALUES
          ('SUP-01', 'Northshore Materials', 'MY', 7, 0.22, DATE '2025-01-01'),
          ('SUP-02', 'Peninsula Polymers', 'SG', 12, 0.41, DATE '2026-08-01'),
          ('SUP-03', 'Delta Logistics Co', 'MY', 5, 0.18, DATE '2024-06-01'),
          ('SUP-04', 'Orbit Packing', 'TH', 9, 0.55, DATE '2026-09-01')
        """
    )
    con.execute(
        """
        CREATE TABLE inventory (
          sku VARCHAR,
          location_id VARCHAR,
          quantity_kg DOUBLE,
          reorder_level_kg DOUBLE,
          unit_cost_myr DOUBLE,
          supplier_id VARCHAR,
          category VARCHAR,
          expiry_date DATE
        )
        """
    )
    con.execute(
        """
        INSERT INTO inventory VALUES
          ('RS622XK', 'WH-A', 1200, 500, 4.50, 'SUP-01', 'RAW', NULL),
          ('RS622XKR', 'WH-A', 80, 200, 5.20, 'SUP-01', 'RAW', NULL),
          ('SKU-ALPHA', 'WH-B', 3400, 1000, 2.10, 'SUP-02', 'PACKAGING', NULL),
          ('SKU-BETA', 'WH-B', 900, 400, 8.75, 'SUP-03', 'PACKAGING', NULL),
          ('SKU-GAMMA', 'WH-C', 150, 300, 12.00, 'SUP-04', 'CHEMICALS', DATE '2020-01-15'),
          ('SKU-DELTA', 'WH-D', 60, 250, 6.40, 'SUP-02', 'PARTS', NULL),
          ('SKU-EPSILON', 'WH-E', 2100, 800, 3.25, 'SUP-03', 'PARTS', NULL)
        """
    )
    con.execute(
        """
        CREATE TABLE shipments (
          shipment_id VARCHAR,
          sku VARCHAR,
          destination_location_id VARCHAR,
          quantity_kg DOUBLE,
          status VARCHAR,
          cost_myr DOUBLE,
          supplier_id VARCHAR
        )
        """
    )
    con.execute(
        """
        INSERT INTO shipments VALUES
          ('SH-100', 'SKU-ALPHA', 'WH-B', 400, 'in_transit', 820, NULL),
          ('SH-101', 'SKU-BETA', 'WH-B', 120, 'delivered', 310, NULL),
          ('SH-102', 'RS622XK', 'WH-A', 250, 'delayed', 540, NULL),
          ('SH-103', 'SKU-GAMMA', 'WH-C', 80, 'in_transit', 190, NULL),
          ('SH-104', 'SKU-DELTA', 'WH-D', 200, 'delayed', 460, NULL)
        """
    )
    con.execute(
        """
        CREATE TABLE alerts (
          alert_id VARCHAR,
          severity VARCHAR,
          location_id VARCHAR,
          message VARCHAR,
          resolved BOOLEAN
        )
        """
    )
    con.execute(
        """
        INSERT INTO alerts VALUES
          ('AL-1', 'high', 'WH-C', 'Load near capacity', FALSE),
          ('AL-2', 'medium', 'WH-A', 'SKU RS622XKR below reorder', FALSE),
          ('AL-3', 'low', 'WH-B', 'Inbound delayed 1 day', TRUE),
          ('AL-4', 'high', 'WH-E', 'Load near capacity', FALSE),
          ('AL-5', 'medium', 'WH-D', 'SKU-DELTA below reorder', FALSE)
        """
    )
    con.execute(
        """
        CREATE TABLE transactions (
          txn_id VARCHAR,
          sku VARCHAR,
          location_id VARCHAR,
          txn_type VARCHAR,
          quantity_kg DOUBLE,
          unit_cost_myr DOUBLE,
          ts TIMESTAMP
        )
        """
    )
    con.execute(
        """
        INSERT INTO transactions VALUES
          ('T001', 'RS622XK', 'WH-A', 'outbound', 400.0, 4.50, TIMESTAMP '2026-06-02 10:00:00'),
          ('T002', 'RS622XK', 'WH-A', 'outbound', 350.0, 4.50, TIMESTAMP '2026-06-15 11:00:00'),
          ('T003', 'RS622XKR', 'WH-A', 'outbound', 200.0, 5.20, TIMESTAMP '2026-06-20 09:00:00'),
          ('T004', 'SKU-ALPHA', 'WH-B', 'outbound', 1500.0, 2.10, TIMESTAMP '2026-07-01 08:00:00'),
          ('T005', 'SKU-ALPHA', 'WH-B', 'outbound', 800.0, 2.10, TIMESTAMP '2026-07-10 14:00:00'),
          ('T006', 'SKU-BETA', 'WH-B', 'outbound', 600.0, 8.75, TIMESTAMP '2026-07-12 16:00:00'),
          ('T007', 'SKU-GAMMA', 'WH-C', 'outbound', 220.0, 12.00, TIMESTAMP '2026-07-18 12:00:00'),
          ('T008', 'SKU-BETA', 'WH-B', 'outbound', 100.0, 8.75, TIMESTAMP '2026-07-22 10:00:00'),
          ('T009', 'RS622XK', 'WH-A', 'inbound', 500.0, 4.50, TIMESTAMP '2026-07-05 07:00:00'),
          ('T010', 'SKU-ALPHA', 'WH-B', 'inbound', 2000.0, 2.10, TIMESTAMP '2026-07-08 07:00:00'),
          ('T011', 'SKU-DELTA', 'WH-D', 'outbound', 180.0, 6.40, TIMESTAMP '2026-07-20 09:00:00'),
          ('T012', 'SKU-EPSILON', 'WH-E', 'outbound', 900.0, 3.25, TIMESTAMP '2026-07-21 11:00:00'),
          ('T013', 'SKU-BETA', 'WH-B', 'outbound', 250.0, 8.75, TIMESTAMP '2026-07-23 15:00:00'),
          ('T014', 'RS622XK', 'WH-A', 'outbound', 120.0, 4.50, TIMESTAMP '2026-07-24 10:00:00'),
          ('T015', 'SKU-ALPHA', 'WH-B', 'outbound', 400.0, 2.10, TIMESTAMP '2026-07-25 13:00:00')
        """
    )
    return con
