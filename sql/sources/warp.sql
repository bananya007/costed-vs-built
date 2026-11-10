-- =====================================================================
-- warp: the ERP books (stands in for Tesla's Warp, which is MySQL-backed).
-- Master data (items, BOMs, standard costs) plus the inventory subledger.
-- Synthetic demo schema: names and columns are a general design, not Tesla's.
-- =====================================================================
CREATE DATABASE IF NOT EXISTS warp;
USE warp;

DROP TABLE IF EXISTS item, bom, routing, work_center,
  std_cost, work_order, inventory_txn, po_receipt, actual_cost_pool, cycle_count;

-- One row per part number: a bolt, a sub-assembly, a vehicle.
CREATE TABLE item (
  item_id     VARCHAR(20) PRIMARY KEY,
  item_desc   VARCHAR(100) NOT NULL,
  item_type   VARCHAR(10)  NOT NULL,  -- RAW (bought part) | SUBASSY (built in-house)
  uom         VARCHAR(5)   NOT NULL,  -- EA = each
  program     VARCHAR(20)  NOT NULL,
  status      VARCHAR(10)  NOT NULL   -- ACTIVE | OBSOLETE
);

-- The recipe. One row per parent x component x revision.
-- consume_method says how the floor records usage of this part:
--   SCAN = an operator scans each part; BACKFLUSH = MES assumes qty_per was used.
CREATE TABLE bom (
  bom_line_id       INT AUTO_INCREMENT PRIMARY KEY,
  parent_item_id    VARCHAR(20) NOT NULL,
  component_item_id VARCHAR(20) NOT NULL,
  bom_rev           VARCHAR(5)  NOT NULL,
  qty_per           DECIMAL(10,3) NOT NULL,
  effective_from    DATE NOT NULL,
  effective_to      DATE NULL,           -- NULL = still current
  eco_id            VARCHAR(20) NULL,    -- change order that created this revision
  consume_method    VARCHAR(10) NOT NULL,
  UNIQUE KEY uq_bom (parent_item_id, component_item_id, bom_rev)
);

-- Steps and standard labor hours to build one unit.
CREATE TABLE routing (
  item_id        VARCHAR(20) NOT NULL,
  op_seq         INT NOT NULL,
  work_center_id VARCHAR(20) NOT NULL,
  std_labor_hrs  DECIMAL(8,3) NOT NULL,
  PRIMARY KEY (item_id, op_seq)
);

CREATE TABLE work_center (
  work_center_id    VARCHAR(20) PRIMARY KEY,
  line_name         VARCHAR(40) NOT NULL,
  area              VARCHAR(40) NOT NULL,
  plant             VARCHAR(20) NOT NULL,
  std_labor_rate    DECIMAL(10,2) NOT NULL,  -- $ per labor hour at standard
  std_overhead_rate DECIMAL(10,2) NOT NULL   -- $ overhead applied per standard labor hour
);

-- What one unit SHOULD cost, per cost roll (cost_version).
-- bom_rev_used = the BOM revision frozen into this standard until the next roll.
CREATE TABLE std_cost (
  item_id        VARCHAR(20) NOT NULL,
  cost_version   VARCHAR(7)  NOT NULL,       -- e.g. 2026-10
  effective_from DATE NOT NULL,
  effective_to   DATE NOT NULL,
  bom_rev_used   VARCHAR(5) NULL,            -- NULL for bought parts
  material_cost  DECIMAL(12,4) NOT NULL,
  labor_cost     DECIMAL(12,4) NOT NULL,
  overhead_cost  DECIMAL(12,4) NOT NULL,
  total_std_cost DECIMAL(12,4) NOT NULL,
  PRIMARY KEY (item_id, cost_version)
);

-- Instruction to build N units of an item on a given BOM revision.
CREATE TABLE work_order (
  work_order_id  VARCHAR(20) PRIMARY KEY,
  item_id        VARCHAR(20) NOT NULL,
  work_center_id VARCHAR(20) NOT NULL,
  bom_rev        VARCHAR(5)  NOT NULL,       -- revision at release
  planned_qty    INT NOT NULL,
  released_ts    DATETIME NOT NULL,
  status         VARCHAR(12) NOT NULL
);

-- The inventory subledger: one row per movement, valued at standard.
-- source_event_id = the MOS event that triggered the row (the lineage key).
-- is_void = 1 when the ERP voids a row in place (a correction with no new timestamp).
CREATE TABLE inventory_txn (
  txn_id          VARCHAR(20) PRIMARY KEY,
  txn_type        VARCHAR(16) NOT NULL,      -- OPENING_BAL | RECEIPT | ISSUE_TO_WO | SCRAP | WO_COMPLETION | CYCLE_ADJ
  item_id         VARCHAR(20) NOT NULL,
  qty             DECIMAL(12,3) NOT NULL,    -- signed: negative = leaves the location
  location_id     VARCHAR(20) NOT NULL,
  work_order_id   VARCHAR(20) NULL,
  std_unit_cost   DECIMAL(12,4) NOT NULL,
  ext_value       DECIMAL(14,2) NOT NULL,
  source_event_id VARCHAR(30) NULL,
  txn_ts          DATETIME NOT NULL,
  fiscal_period   VARCHAR(7) NOT NULL,       -- the month whose books it lands in
  is_void         TINYINT NOT NULL DEFAULT 0,
  note            VARCHAR(200) NULL,
  KEY ix_txn_period (fiscal_period),
  KEY ix_txn_src (source_event_id)
);

CREATE TABLE po_receipt (
  receipt_id        VARCHAR(20) PRIMARY KEY,
  po_line_id        VARCHAR(20) NOT NULL,
  supplier_id       VARCHAR(20) NOT NULL,
  item_id           VARCHAR(20) NOT NULL,
  qty               DECIMAL(12,3) NOT NULL,
  actual_unit_price DECIMAL(12,4) NOT NULL,
  std_unit_price    DECIMAL(12,4) NOT NULL,
  location_id       VARCHAR(20) NOT NULL,
  receipt_ts        DATETIME NOT NULL,
  fiscal_period     VARCHAR(7) NOT NULL
);

-- Actual labor and overhead spend per work center per month.
CREATE TABLE actual_cost_pool (
  fiscal_period  VARCHAR(7) NOT NULL,
  work_center_id VARCHAR(20) NOT NULL,
  cost_type      VARCHAR(10) NOT NULL,       -- LABOR | OVERHEAD
  actual_amount  DECIMAL(14,2) NOT NULL,
  actual_hours   DECIMAL(10,2) NULL,         -- LABOR rows: hours paid
  PRIMARY KEY (fiscal_period, work_center_id, cost_type)
);

-- Physical counts: book (system) vs counted. adj_qty is booked as a CYCLE_ADJ txn.
CREATE TABLE cycle_count (
  count_id      VARCHAR(20) PRIMARY KEY,
  item_id       VARCHAR(20) NOT NULL,
  location_id   VARCHAR(20) NOT NULL,
  count_ts      DATETIME NOT NULL,
  fiscal_period VARCHAR(7) NOT NULL,
  book_qty      DECIMAL(12,3) NOT NULL,
  counted_qty   DECIMAL(12,3) NOT NULL,
  adj_qty       DECIMAL(12,3) NOT NULL
);
