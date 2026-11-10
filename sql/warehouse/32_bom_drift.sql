-- =====================================================================
-- Check 2, unknown drift: parts the floor keeps using differently from the BOM.
-- Backflushed parts always look perfect in MOS (it writes BOM qty x units),
-- so the truth only shows in cycle-count shortages. For each count we compare
-- the shortage with the recorded use since the previous count:
--   steady ratio over 3+ counts  -> likely BOM_QTY (the recipe quantity is wrong)
--   shortages on 2+ counts, not steady -> likely UNRECORDED_SCRAP (lumpy losses)
--   a single shortage            -> UNKNOWN (one-off; investigate)
-- These cause labels are heuristics and are reported as "likely".
-- =====================================================================
DELETE FROM out_bom_drift WHERE fiscal_period = {{close_period}};

INSERT INTO out_bom_drift
WITH counts AS (
  SELECT cc.*, LAG(count_ts) OVER (PARTITION BY item_id, location_id ORDER BY count_ts) AS prev_ts
  FROM raw_warp_cycle_count cc
),
per_count AS (
  SELECT c.item_id, c.location_id, c.adj_qty,
         (SELECT COALESCE(SUM(m.qty), 0) FROM raw_mos_consumption m
          WHERE m.component_item_id = c.item_id
            AND m.event_ts <= c.count_ts
            AND m.event_ts > COALESCE(c.prev_ts, '1900-01-01')) AS used_since
  FROM counts c
  WHERE c.fiscal_period = {{close_period}}
),
agg AS (
  SELECT item_id, location_id,
         SUM(CASE WHEN adj_qty < 0 THEN -adj_qty ELSE 0 END) AS shortage,
         SUM(CASE WHEN adj_qty > 0 THEN adj_qty ELSE 0 END) AS overage,
         SUM(CASE WHEN adj_qty < 0 THEN 1 ELSE 0 END) AS events,
         AVG(CASE WHEN adj_qty < 0 AND used_since > 0 THEN -adj_qty / used_since END) AS ratio_avg,
         STDDEV_POP(CASE WHEN adj_qty < 0 AND used_since > 0 THEN -adj_qty / used_since END) AS ratio_sd
  FROM per_count GROUP BY item_id, location_id
),
used AS (
  SELECT component_item_id AS item_id, SUM(qty) AS q FROM raw_mos_consumption
  WHERE DATE_FORMAT(event_ts, '%Y-%m') = {{close_period}} GROUP BY component_item_id
),
scrapped AS (
  SELECT item_id, SUM(qty) AS q FROM raw_mos_scrap
  WHERE DATE_FORMAT(event_ts, '%Y-%m') = {{close_period}} GROUP BY item_id
),
cur_bom AS (   -- the recipe lines this part is on today
  SELECT component_item_id AS item_id,
         GROUP_CONCAT(DISTINCT parent_item_id ORDER BY parent_item_id) AS parents,
         GROUP_CONCAT(CONCAT(parent_item_id, ' x', TRIM(TRAILING '.' FROM TRIM(TRAILING '0' FROM qty_per)))
                      ORDER BY parent_item_id SEPARATOR ', ') AS qty_txt,
         MAX(qty_per) AS qty_per
  FROM raw_warp_bom
  WHERE effective_from <= LAST_DAY(STR_TO_DATE(CONCAT({{close_period}}, '-01'), '%Y-%m-%d'))
    AND (effective_to IS NULL
         OR effective_to >= LAST_DAY(STR_TO_DATE(CONCAT({{close_period}}, '-01'), '%Y-%m-%d')))
  GROUP BY component_item_id
),
all_parents AS (
  SELECT component_item_id AS item_id, GROUP_CONCAT(DISTINCT parent_item_id ORDER BY parent_item_id) AS parents
  FROM raw_warp_bom GROUP BY component_item_id
),
scored AS (
  SELECT a.*, COALESCE(u.q, 0) AS use_q, COALESCE(s.q, 0) AS scrap_q, p.std_cost,
         CASE WHEN a.ratio_avg > 0 THEN a.ratio_sd / a.ratio_avg END AS cv,
         CASE
           WHEN a.events >= 3 AND a.ratio_sd / a.ratio_avg < 0.25 THEN 'BOM_QTY'
           WHEN a.events >= 2 THEN 'UNRECORDED_SCRAP'
           ELSE 'UNKNOWN'
         END AS cause
  FROM agg a
  LEFT JOIN used u ON u.item_id = a.item_id
  LEFT JOIN scrapped s ON s.item_id = a.item_id
  JOIN stg_item_std p ON p.item_id = a.item_id AND p.cost_version = {{close_period}}
  WHERE a.shortage > 0
)
SELECT {{close_period}}, sc.item_id, sc.location_id, COALESCE(cb.parents, ap.parents),
       sc.use_q, sc.scrap_q, sc.shortage, sc.overage, sc.events,
       ROUND(CASE WHEN sc.use_q > 0 THEN sc.shortage / sc.use_q ELSE 0 END, 4),
       ROUND(sc.shortage * sc.std_cost, 2),
       ROUND(sc.ratio_avg, 4), ROUND(sc.cv, 4), sc.cause,
       cb.qty_txt,
       CASE WHEN sc.cause = 'BOM_QTY' AND cb.qty_per IS NOT NULL
            THEN CONCAT('about ', ROUND(cb.qty_per * (1 + sc.ratio_avg), 1), ' per unit') END,
       ROW_NUMBER() OVER (ORDER BY sc.shortage * sc.std_cost DESC, sc.item_id)
FROM scored sc
LEFT JOIN cur_bom cb ON cb.item_id = sc.item_id
LEFT JOIN all_parents ap ON ap.item_id = sc.item_id;
