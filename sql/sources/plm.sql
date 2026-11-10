-- =====================================================================
-- plm: engineering change orders (stands in for a PLM system such as
-- Siemens Teamcenter or PTC Windchill; which one Tesla uses is not public).
-- =====================================================================
CREATE DATABASE IF NOT EXISTS plm;
USE plm;

DROP TABLE IF EXISTS eco;

-- One row per approved engineering change: the BOM of parent_item_id moves
-- from from_rev to to_rev on effective_date.
CREATE TABLE eco (
  eco_id         VARCHAR(20) PRIMARY KEY,
  parent_item_id VARCHAR(20) NOT NULL,
  from_rev       VARCHAR(5)  NOT NULL,
  to_rev         VARCHAR(5)  NOT NULL,
  released_date  DATE NOT NULL,
  effective_date DATE NOT NULL,
  reason         VARCHAR(200) NOT NULL,
  disposition    VARCHAR(20) NOT NULL   -- what to do with old parts: USE_AS_IS | SCRAP | REWORK
);
