-- =====================================================================
-- Check 3, orphaned stock: parts a change order removed from a recipe.
--   NO_REMAINING_USE  no current recipe uses the part at all -> E&O candidate
--   USING_UP          still on another current recipe; days of supply shows how long it lasts
-- Also flags receipts dated after the change went live: still buying a phased-out part.
-- On hand = ERP book quantity (the sum of non-void inventory movements) at period end.
-- =====================================================================
DELETE FROM out_orphaned_stock WHERE fiscal_period = {{close_period}};

INSERT INTO out_orphaned_stock
WITH pe AS (
  SELECT LAST_DAY(STR_TO_DATE(CONCAT({{close_period}}, '-01'), '%Y-%m-%d')) AS d
),
removed AS (   -- parts on a revision that ended, mapped to the change that ended it
  SELECT b.component_item_id AS item_id, e.eco_id, e.effective_date
  FROM raw_warp_bom b
  JOIN pe ON b.effective_to IS NOT NULL AND b.effective_to < pe.d
  JOIN raw_plm_eco e ON e.parent_item_id = b.parent_item_id AND e.from_rev = b.bom_rev
  WHERE NOT EXISTS (   -- ...and not carried over unchanged into the new revision
    SELECT 1 FROM raw_warp_bom nb
    WHERE nb.parent_item_id = b.parent_item_id AND nb.bom_rev = e.to_rev
      AND nb.component_item_id = b.component_item_id)
),
cand AS (
  SELECT item_id, MIN(eco_id) AS eco_id, MIN(effective_date) AS eff FROM removed GROUP BY item_id
),
still_used AS (
  SELECT DISTINCT b.component_item_id AS item_id
  FROM raw_warp_bom b JOIN pe
  WHERE b.effective_from <= pe.d AND (b.effective_to IS NULL OR b.effective_to >= pe.d)
),
on_hand AS (
  SELECT item_id, location_id, SUM(qty) AS q
  FROM stg_inventory_txn
  WHERE fiscal_period <= {{close_period}} AND txn_type <> 'WO_COMPLETION'
  GROUP BY item_id, location_id
),
recent_use AS (   -- last 30 days of recorded use, for parts still on a recipe
  SELECT m.component_item_id AS item_id, SUM(m.qty) / 30 AS per_day
  FROM raw_mos_consumption m JOIN pe
  WHERE m.event_ts > DATE_SUB(pe.d, INTERVAL 29 DAY) AND m.event_ts < DATE_ADD(pe.d, INTERVAL 1 DAY)
  GROUP BY m.component_item_id
),
rcpt AS (
  SELECT r.item_id, SUM(r.qty) AS q
  FROM raw_warp_po_receipt r JOIN cand c ON c.item_id = r.item_id
  WHERE DATE(r.receipt_ts) >= c.eff AND r.fiscal_period <= {{close_period}}
  GROUP BY r.item_id
)
SELECT {{close_period}}, c.item_id, o.location_id, c.eco_id, c.eff, o.q, p.std_cost,
       ROUND(o.q * p.std_cost, 2),
       CASE WHEN su.item_id IS NULL THEN 0 ELSE ROUND(COALESCE(ru.per_day, 0), 3) END,
       CASE WHEN su.item_id IS NOT NULL AND ru.per_day > 0 THEN ROUND(o.q / ru.per_day, 1) END,
       COALESCE(rc.q, 0),
       CASE WHEN su.item_id IS NULL THEN 'NO_REMAINING_USE' ELSE 'USING_UP' END
FROM cand c
JOIN on_hand o ON o.item_id = c.item_id AND o.q > 0
JOIN stg_item_std p ON p.item_id = c.item_id AND p.cost_version = {{close_period}}
LEFT JOIN still_used su ON su.item_id = c.item_id
LEFT JOIN recent_use ru ON ru.item_id = c.item_id
LEFT JOIN rcpt rc ON rc.item_id = c.item_id;
