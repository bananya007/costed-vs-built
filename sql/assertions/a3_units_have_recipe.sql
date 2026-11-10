-- Every unit MOS built must be on a revision that exists in the ERP BOM.
SELECT c.unit_serial, c.item_id, c.bom_rev_built
FROM raw_mos_completion c
LEFT JOIN raw_warp_bom b ON b.parent_item_id = c.item_id AND b.bom_rev = c.bom_rev_built
WHERE DATE_FORMAT(c.completed_ts, '%Y-%m') = {{close_period}} AND b.bom_line_id IS NULL;
