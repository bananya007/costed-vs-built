-- =====================================================================
-- Staging views: clean, typed, one meaning per view. Every later step
-- reads these, never the raw tables' quirks.
-- =====================================================================

-- Standard cost per item for each cost version (parts: material; sub-assemblies: total).
CREATE OR REPLACE VIEW stg_item_std AS
SELECT item_id, cost_version, total_std_cost AS std_cost, material_cost, bom_rev_used
FROM raw_warp_std_cost;

-- ERP inventory movements that count: voided rows are excluded.
CREATE OR REPLACE VIEW stg_inventory_txn AS
SELECT * FROM raw_warp_inventory_txn WHERE is_void = 0;

-- Every MOS event that should produce an ERP row, in one shape.
-- qty is positive in the direction MOS reports it (parts used, units built).
CREATE OR REPLACE VIEW stg_mes_events AS
SELECT event_id AS source_event_id, 'CONSUMPTION' AS event_type, work_order_id,
       component_item_id AS item_id, qty, event_ts, DATE_FORMAT(event_ts, '%Y-%m') AS mes_period
FROM raw_mos_consumption
UNION ALL
SELECT scrap_event_id, 'SCRAP', work_order_id, item_id, qty, event_ts, DATE_FORMAT(event_ts, '%Y-%m')
FROM raw_mos_scrap
UNION ALL
SELECT unit_serial, 'COMPLETION', work_order_id, item_id, 1, completed_ts, DATE_FORMAT(completed_ts, '%Y-%m')
FROM raw_mos_completion;

-- The ERP rows that MOS events create, with qty flipped into MOS's direction
-- (an issue of 4 parts is -4 in the ERP and 4 in MOS).
CREATE OR REPLACE VIEW stg_erp_mes_txns AS
SELECT txn_id, txn_type, source_event_id, work_order_id, item_id, fiscal_period, txn_ts,
       CASE WHEN txn_type = 'WO_COMPLETION' THEN qty ELSE -qty END AS mes_qty
FROM stg_inventory_txn
WHERE txn_type IN ('ISSUE_TO_WO', 'SCRAP', 'WO_COMPLETION');
