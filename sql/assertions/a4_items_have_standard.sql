-- Every item that moved this period needs a standard for this cost version. The gate
-- and the checks join to the standard, so a missing one would silently drop rows.
-- Sub-assemblies also need the BOM revision that was frozen into their standard.
SELECT u.item_id, 'no standard' AS problem
FROM (
  SELECT item_id FROM stg_mes_events WHERE mes_period = {{close_period}}
  UNION SELECT item_id FROM stg_inventory_txn WHERE fiscal_period = {{close_period}}
) u
LEFT JOIN raw_warp_std_cost s ON s.item_id = u.item_id AND s.cost_version = {{close_period}}
WHERE s.item_id IS NULL
UNION ALL
SELECT DISTINCT c.item_id, 'no frozen BOM revision'
FROM raw_mos_completion c
JOIN raw_warp_std_cost s ON s.item_id = c.item_id AND s.cost_version = {{close_period}}
WHERE DATE_FORMAT(c.completed_ts, '%Y-%m') = {{close_period}} AND s.bom_rev_used IS NULL;
