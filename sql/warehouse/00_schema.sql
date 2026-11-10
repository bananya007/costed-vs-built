-- =====================================================================
-- mfg_dw: the analytics warehouse. raw_* tables are created by the extract
-- step as copies of the source tables; this file creates the control and
-- output tables. Outputs are replaced one fiscal period at a time.
-- =====================================================================

CREATE TABLE IF NOT EXISTS etl_run_log (
  run_id      VARCHAR(40) NOT NULL,
  step        VARCHAR(40) NOT NULL,
  table_name  VARCHAR(80) NULL,
  periods     VARCHAR(80) NULL,
  rows_loaded INT NULL,
  finished_at DATETIME NOT NULL,
  detail      VARCHAR(500) NULL
);

-- Reconciliation: one row per MES event (or ERP row) that does not match.
CREATE TABLE IF NOT EXISTS out_recon_breaks (
  fiscal_period    VARCHAR(7)  NOT NULL,
  source_event_id  VARCHAR(30) NOT NULL,
  event_type       VARCHAR(12) NOT NULL,   -- CONSUMPTION | SCRAP | COMPLETION | ERP_ONLY
  work_order_id    VARCHAR(20) NULL,
  item_id          VARCHAR(20) NOT NULL,
  mes_qty          DECIMAL(12,3) NOT NULL,
  erp_qty_in_period    DECIMAL(12,3) NOT NULL,
  erp_qty_other_period DECIMAL(12,3) NOT NULL,
  erp_rows         INT NOT NULL,
  diff_qty         DECIMAL(12,3) NOT NULL, -- ERP minus MES, in this period
  diff_value       DECIMAL(14,2) NOT NULL, -- at standard cost
  break_type       VARCHAR(16) NOT NULL,   -- TIMING | MISSING_IN_ERP | MISSING_IN_MES | DUPLICATE | QTY_MISMATCH
  status           VARCHAR(10) NOT NULL,   -- OPEN | EXPLAINED
  detail           VARCHAR(200) NOT NULL,
  KEY ix_rb (fiscal_period)
);

-- Material variance per work order x component, split by cause.
CREATE TABLE IF NOT EXISTS out_wo_material_variance (
  fiscal_period     VARCHAR(7)  NOT NULL,
  work_order_id     VARCHAR(20) NOT NULL,
  parent_item_id    VARCHAR(20) NOT NULL,
  component_item_id VARCHAR(20) NOT NULL,
  costed_rev        VARCHAR(5)  NOT NULL,  -- revision frozen in the standard
  built_revs        VARCHAR(20) NOT NULL,  -- revisions actually built
  units             INT NOT NULL,
  std_price         DECIMAL(12,4) NOT NULL,
  std_qty           DECIMAL(12,3) NOT NULL,  -- what the standard allows
  built_qty         DECIMAL(12,3) NOT NULL,  -- what the built revision calls for
  consumed_qty      DECIMAL(12,3) NOT NULL,  -- MES consumption
  scrap_qty         DECIMAL(12,3) NOT NULL,  -- MES logged scrap
  eng_change_var    DECIMAL(14,2) NOT NULL,  -- (built - std) x price: the design changed
  scrap_var         DECIMAL(14,2) NOT NULL,  -- scrap x price: explained waste
  usage_var         DECIMAL(14,2) NOT NULL,  -- (consumed - built) x price: other real usage
  erp_actual_value  DECIMAL(14,2) NOT NULL,  -- the same usage as booked in the ERP
  PRIMARY KEY (fiscal_period, work_order_id, component_item_id)
);

CREATE TABLE IF NOT EXISTS out_eco_impact (
  fiscal_period      VARCHAR(7)  NOT NULL,
  eco_id             VARCHAR(20) NOT NULL,
  work_order_id      VARCHAR(20) NOT NULL,
  component_item_id  VARCHAR(20) NOT NULL,
  units_built_new_rev INT NOT NULL,
  std_qty            DECIMAL(12,3) NOT NULL,
  built_qty          DECIMAL(12,3) NOT NULL,
  actual_qty         DECIMAL(12,3) NOT NULL,
  std_price          DECIMAL(12,4) NOT NULL,
  eng_change_var     DECIMAL(14,2) NOT NULL,  -- noise from the design change
  true_usage_var     DECIMAL(14,2) NOT NULL,  -- real waste or saving
  cost_roll_pending  TINYINT NOT NULL,
  PRIMARY KEY (fiscal_period, eco_id, work_order_id, component_item_id)
);

CREATE TABLE IF NOT EXISTS out_wo_labor_variance (
  fiscal_period  VARCHAR(7)  NOT NULL,
  work_order_id  VARCHAR(20) NOT NULL,
  work_center_id VARCHAR(20) NOT NULL,
  units          INT NOT NULL,
  std_hours      DECIMAL(10,2) NOT NULL,
  actual_hours   DECIMAL(10,2) NOT NULL,
  std_rate       DECIMAL(10,2) NOT NULL,
  labor_eff_var  DECIMAL(14,2) NOT NULL,
  PRIMARY KEY (fiscal_period, work_order_id)
);

CREATE TABLE IF NOT EXISTS out_wc_variance (
  fiscal_period    VARCHAR(7)  NOT NULL,
  work_center_id   VARCHAR(20) NOT NULL,
  actual_hours     DECIMAL(10,2) NOT NULL,
  actual_labor     DECIMAL(14,2) NOT NULL,
  std_rate         DECIMAL(10,2) NOT NULL,
  labor_rate_var   DECIMAL(14,2) NOT NULL,
  overhead_applied DECIMAL(14,2) NOT NULL,
  overhead_actual  DECIMAL(14,2) NOT NULL,
  overhead_var     DECIMAL(14,2) NOT NULL,   -- positive = under-absorbed
  PRIMARY KEY (fiscal_period, work_center_id)
);

CREATE TABLE IF NOT EXISTS out_ppv (
  fiscal_period      VARCHAR(7)  NOT NULL,
  receipt_id         VARCHAR(20) NOT NULL,
  item_id            VARCHAR(20) NOT NULL,
  supplier_id        VARCHAR(20) NOT NULL,
  receipt_date       DATE NOT NULL,
  qty                DECIMAL(12,3) NOT NULL,
  actual_unit_price  DECIMAL(12,4) NOT NULL,
  std_unit_price     DECIMAL(12,4) NOT NULL,
  ppv                DECIMAL(14,2) NOT NULL,
  part_phased_out    TINYINT NOT NULL,       -- received after a change removed it from every recipe
  PRIMARY KEY (fiscal_period, receipt_id)
);

CREATE TABLE IF NOT EXISTS out_bom_drift (
  fiscal_period      VARCHAR(7)  NOT NULL,
  component_item_id  VARCHAR(20) NOT NULL,
  location_id        VARCHAR(20) NOT NULL,
  parent_item_ids    VARCHAR(100) NOT NULL,
  recorded_use_qty   DECIMAL(12,3) NOT NULL,  -- MES consumption this period
  recorded_scrap_qty DECIMAL(12,3) NOT NULL,  -- logged scrap (explained)
  count_shortage_qty DECIMAL(12,3) NOT NULL,  -- negative count adjustments (unexplained)
  count_overage_qty  DECIMAL(12,3) NOT NULL,
  shortage_events    INT NOT NULL,
  drift_pct          DECIMAL(9,4) NOT NULL,
  drift_value        DECIMAL(14,2) NOT NULL,
  shortage_ratio_avg DECIMAL(9,4) NULL,       -- shortage per unit of recorded use, per count
  shortage_ratio_cv  DECIMAL(9,4) NULL,       -- how steady that ratio is (0 = perfectly steady)
  likely_cause       VARCHAR(20) NOT NULL,    -- BOM_QTY | UNRECORDED_SCRAP | UNKNOWN (heuristic)
  current_qty_per    VARCHAR(40) NULL,
  suggested_qty_per  VARCHAR(40) NULL,
  count_priority     INT NOT NULL,
  PRIMARY KEY (fiscal_period, component_item_id, location_id)
);

CREATE TABLE IF NOT EXISTS out_orphaned_stock (
  fiscal_period       VARCHAR(7)  NOT NULL,
  item_id             VARCHAR(20) NOT NULL,
  location_id         VARCHAR(20) NOT NULL,
  superseded_by_eco   VARCHAR(20) NOT NULL,
  change_effective    DATE NOT NULL,
  on_hand_qty         DECIMAL(12,3) NOT NULL,
  std_cost            DECIMAL(12,4) NOT NULL,
  on_hand_value       DECIMAL(14,2) NOT NULL,
  remaining_daily_use DECIMAL(12,3) NOT NULL,
  days_of_supply      DECIMAL(10,1) NULL,     -- NULL = no remaining use at all
  receipts_after_change_qty DECIMAL(12,3) NOT NULL,
  status              VARCHAR(20) NOT NULL,   -- NO_REMAINING_USE | USING_UP
  PRIMARY KEY (fiscal_period, item_id, location_id)
);

CREATE TABLE IF NOT EXISTS out_close_summary (
  fiscal_period       VARCHAR(7) PRIMARY KEY,
  status              VARCHAR(10) NOT NULL,   -- READY | BLOCKED
  run_id              VARCHAR(40) NULL,
  run_at              DATETIME NOT NULL,
  materiality         DECIMAL(10,2) NOT NULL,
  open_break_count    INT NOT NULL,
  open_break_value    DECIMAL(14,2) NOT NULL,
  timing_break_count  INT NOT NULL,
  eng_change_var      DECIMAL(14,2) NULL,
  scrap_var           DECIMAL(14,2) NULL,
  usage_var           DECIMAL(14,2) NULL,
  labor_eff_var       DECIMAL(14,2) NULL,
  labor_rate_var      DECIMAL(14,2) NULL,
  overhead_var        DECIMAL(14,2) NULL,
  ppv                 DECIMAL(14,2) NULL,
  drift_value         DECIMAL(14,2) NULL,
  orphaned_value      DECIMAL(14,2) NULL,
  receipts_phased_out_value DECIMAL(14,2) NULL
);
