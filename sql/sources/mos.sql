-- =====================================================================
-- mos: floor events (stands in for Tesla's MOS, its manufacturing system).
-- What physically happened, station by station. Synthetic demo schema.
-- =====================================================================
CREATE DATABASE IF NOT EXISTS mos;
USE mos;

DROP TABLE IF EXISTS completion, consumption, scrap, labor;

-- One row per unit built. bom_rev_built = the revision actually built on the floor.
CREATE TABLE completion (
  unit_serial   VARCHAR(30) PRIMARY KEY,
  work_order_id VARCHAR(20) NOT NULL,
  item_id       VARCHAR(20) NOT NULL,
  bom_rev_built VARCHAR(5)  NOT NULL,
  station_id    VARCHAR(20) NOT NULL,
  first_pass    TINYINT NOT NULL,
  completed_ts  DATETIME NOT NULL,
  KEY ix_comp_ts (completed_ts)
);

-- One row per part-consumption event. For BACKFLUSH parts, qty = BOM qty_per by design.
CREATE TABLE consumption (
  event_id          VARCHAR(30) PRIMARY KEY,
  work_order_id     VARCHAR(20) NOT NULL,
  unit_serial       VARCHAR(30) NULL,
  component_item_id VARCHAR(20) NOT NULL,
  qty               DECIMAL(12,3) NOT NULL,
  consume_method    VARCHAR(10) NOT NULL,
  bom_rev_built     VARCHAR(5)  NOT NULL,
  event_ts          DATETIME NOT NULL,
  KEY ix_cons_ts (event_ts)
);

-- Scrap that was LOGGED. Scrap thrown away without a log never appears here.
CREATE TABLE scrap (
  scrap_event_id VARCHAR(30) PRIMARY KEY,
  work_order_id  VARCHAR(20) NOT NULL,
  item_id        VARCHAR(20) NOT NULL,
  qty            DECIMAL(12,3) NOT NULL,
  reason_code    VARCHAR(20) NOT NULL,
  station_id     VARCHAR(20) NOT NULL,
  event_ts       DATETIME NOT NULL
);

CREATE TABLE labor (
  labor_entry_id VARCHAR(20) PRIMARY KEY,
  work_order_id  VARCHAR(20) NOT NULL,
  op_seq         INT NOT NULL,
  hours          DECIMAL(10,2) NOT NULL,
  shift_date     DATE NOT NULL
);
