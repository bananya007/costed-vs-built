-- =====================================================================
-- Check 1 foundation: material variance per work order x component, split by cause.
--   std_qty   = units x qty_per of the revision frozen in the standard (costed_rev)
--   built_qty = units x qty_per of the revision each unit was actually built on
--   eng_change_var = (built - std) x price   -> noise: the recipe changed, not waste
--   scrap_var      = logged scrap x price    -> explained waste
--   usage_var      = (consumed - built) x price -> other real over/under use
-- erp_actual_value is the same usage as booked in the ERP; an assertion checks
-- that std + all variances add back to it exactly.
-- =====================================================================
DELETE FROM out_wo_material_variance WHERE fiscal_period = {{close_period}};

INSERT INTO out_wo_material_variance
WITH units AS (
  SELECT work_order_id, item_id AS parent, bom_rev_built AS rev, COUNT(*) AS n
  FROM raw_mos_completion
  WHERE DATE_FORMAT(completed_ts, '%Y-%m') = {{close_period}}
  GROUP BY work_order_id, item_id, bom_rev_built
),
wo AS (
  SELECT work_order_id, parent, SUM(n) AS units,
         GROUP_CONCAT(DISTINCT rev ORDER BY rev) AS built_revs
  FROM units GROUP BY work_order_id, parent
),
costed AS (
  SELECT item_id AS parent, bom_rev_used FROM stg_item_std
  WHERE cost_version = {{close_period}} AND bom_rev_used IS NOT NULL
),
std AS (
  SELECT w.work_order_id, w.parent, b.component_item_id AS comp, SUM(w.units * b.qty_per) AS q
  FROM wo w
  JOIN costed c ON c.parent = w.parent
  JOIN raw_warp_bom b ON b.parent_item_id = w.parent AND b.bom_rev = c.bom_rev_used
  GROUP BY w.work_order_id, w.parent, b.component_item_id
),
built AS (
  SELECT u.work_order_id, u.parent, b.component_item_id AS comp, SUM(u.n * b.qty_per) AS q
  FROM units u
  JOIN raw_warp_bom b ON b.parent_item_id = u.parent AND b.bom_rev = u.rev
  GROUP BY u.work_order_id, u.parent, b.component_item_id
),
cons AS (
  SELECT work_order_id, component_item_id AS comp, SUM(qty) AS q
  FROM raw_mos_consumption WHERE DATE_FORMAT(event_ts, '%Y-%m') = {{close_period}}
  GROUP BY work_order_id, component_item_id
),
scr AS (
  SELECT work_order_id, item_id AS comp, SUM(qty) AS q
  FROM raw_mos_scrap WHERE DATE_FORMAT(event_ts, '%Y-%m') = {{close_period}}
  GROUP BY work_order_id, item_id
),
erp AS (   -- ERP rows for this period's consumption and scrap events, whatever period they were booked in
  SELECT m.work_order_id, m.item_id AS comp, SUM(e.mes_qty) AS q
  FROM stg_mes_events m
  JOIN stg_erp_mes_txns e ON e.source_event_id = m.source_event_id
  WHERE m.mes_period = {{close_period}} AND m.event_type IN ('CONSUMPTION', 'SCRAP')
  GROUP BY m.work_order_id, m.item_id
),
k AS (
  SELECT work_order_id, parent, comp FROM std
  UNION SELECT work_order_id, parent, comp FROM built
  UNION SELECT s.work_order_id, w.parent, s.comp FROM scr s JOIN wo w ON w.work_order_id = s.work_order_id
)
SELECT {{close_period}}, k.work_order_id, k.parent, k.comp, c.bom_rev_used, w.built_revs, w.units,
       p.std_cost,
       COALESCE(std.q, 0), COALESCE(built.q, 0), COALESCE(cons.q, 0), COALESCE(scr.q, 0),
       ROUND((COALESCE(built.q, 0) - COALESCE(std.q, 0)) * p.std_cost, 2),
       ROUND(COALESCE(scr.q, 0) * p.std_cost, 2),
       ROUND((COALESCE(cons.q, 0) - COALESCE(built.q, 0)) * p.std_cost, 2),
       ROUND(COALESCE(erp.q, 0) * p.std_cost, 2)
FROM k
JOIN wo w ON w.work_order_id = k.work_order_id
JOIN costed c ON c.parent = k.parent
JOIN stg_item_std p ON p.item_id = k.comp AND p.cost_version = {{close_period}}
LEFT JOIN std   ON std.work_order_id = k.work_order_id AND std.comp = k.comp
LEFT JOIN built ON built.work_order_id = k.work_order_id AND built.comp = k.comp
LEFT JOIN cons  ON cons.work_order_id = k.work_order_id AND cons.comp = k.comp
LEFT JOIN scr   ON scr.work_order_id = k.work_order_id AND scr.comp = k.comp
LEFT JOIN erp   ON erp.work_order_id = k.work_order_id AND erp.comp = k.comp;

-- =====================================================================
-- Check 1, known change: work orders with units built on a revision that a
-- change order introduced while the standard is still on an older revision.
-- =====================================================================
DELETE FROM out_eco_impact WHERE fiscal_period = {{close_period}};

INSERT INTO out_eco_impact
WITH new_rev_units AS (
  SELECT work_order_id, bom_rev_built AS rev, COUNT(*) AS n
  FROM raw_mos_completion
  WHERE DATE_FORMAT(completed_ts, '%Y-%m') = {{close_period}}
  GROUP BY work_order_id, bom_rev_built
)
SELECT {{close_period}}, e.eco_id, v.work_order_id, v.component_item_id, nu.n,
       v.std_qty, v.built_qty, v.consumed_qty + v.scrap_qty, v.std_price,
       v.eng_change_var, v.scrap_var + v.usage_var,
       CASE WHEN v.costed_rev <> e.to_rev THEN 1 ELSE 0 END
FROM out_wo_material_variance v
JOIN raw_plm_eco e ON e.parent_item_id = v.parent_item_id
JOIN new_rev_units nu ON nu.work_order_id = v.work_order_id AND nu.rev = e.to_rev
WHERE v.fiscal_period = {{close_period}}
  AND e.effective_date <= LAST_DAY(STR_TO_DATE(CONCAT({{close_period}}, '-01'), '%Y-%m-%d'))
  AND (v.eng_change_var <> 0 OR v.scrap_var + v.usage_var <> 0);

-- =====================================================================
-- Labor and overhead (completes the variance picture for the close summary).
-- =====================================================================
DELETE FROM out_wo_labor_variance WHERE fiscal_period = {{close_period}};

INSERT INTO out_wo_labor_variance
SELECT {{close_period}}, w.work_order_id, w.work_center_id, u.units,
       ROUND(u.units * r.std_labor_hrs, 2), COALESCE(l.hours, 0), wc.std_labor_rate,
       ROUND((COALESCE(l.hours, 0) - u.units * r.std_labor_hrs) * wc.std_labor_rate, 2)
FROM raw_warp_work_order w
JOIN (SELECT work_order_id, COUNT(*) AS units FROM raw_mos_completion
      WHERE DATE_FORMAT(completed_ts, '%Y-%m') = {{close_period}} GROUP BY work_order_id) u
  ON u.work_order_id = w.work_order_id
JOIN raw_warp_routing r ON r.item_id = w.item_id AND r.op_seq = 10
JOIN raw_warp_work_center wc ON wc.work_center_id = w.work_center_id
LEFT JOIN (SELECT work_order_id, SUM(hours) AS hours FROM raw_mos_labor
           WHERE DATE_FORMAT(shift_date, '%Y-%m') = {{close_period}} GROUP BY work_order_id) l
  ON l.work_order_id = w.work_order_id;

DELETE FROM out_wc_variance WHERE fiscal_period = {{close_period}};

INSERT INTO out_wc_variance
SELECT {{close_period}}, wc.work_center_id, lab.actual_hours, lab.actual_amount, wc.std_labor_rate,
       ROUND(lab.actual_amount - lab.actual_hours * wc.std_labor_rate, 2),
       ROUND(sh.std_hours * wc.std_overhead_rate, 2), oh.actual_amount,
       ROUND(oh.actual_amount - sh.std_hours * wc.std_overhead_rate, 2)
FROM raw_warp_work_center wc
JOIN raw_warp_actual_cost_pool lab ON lab.work_center_id = wc.work_center_id
     AND lab.cost_type = 'LABOR' AND lab.fiscal_period = {{close_period}}
JOIN raw_warp_actual_cost_pool oh ON oh.work_center_id = wc.work_center_id
     AND oh.cost_type = 'OVERHEAD' AND oh.fiscal_period = {{close_period}}
JOIN (SELECT work_center_id, SUM(std_hours) AS std_hours FROM out_wo_labor_variance
      WHERE fiscal_period = {{close_period}} GROUP BY work_center_id) sh
  ON sh.work_center_id = wc.work_center_id;

DELETE FROM out_ppv WHERE fiscal_period = {{close_period}};

INSERT INTO out_ppv
SELECT {{close_period}}, r.receipt_id, r.item_id, r.supplier_id, DATE(r.receipt_ts), r.qty,
       r.actual_unit_price, r.std_unit_price,
       ROUND((r.actual_unit_price - r.std_unit_price) * r.qty, 2),
       CASE WHEN EXISTS (
              SELECT 1 FROM raw_warp_bom old
              WHERE old.component_item_id = r.item_id AND old.effective_to IS NOT NULL
                AND old.effective_to < DATE(r.receipt_ts))
            AND NOT EXISTS (
              SELECT 1 FROM raw_warp_bom cur
              WHERE cur.component_item_id = r.item_id AND cur.effective_from <= DATE(r.receipt_ts)
                AND (cur.effective_to IS NULL OR cur.effective_to >= DATE(r.receipt_ts)))
       THEN 1 ELSE 0 END
FROM raw_warp_po_receipt r
WHERE r.fiscal_period = {{close_period}};
