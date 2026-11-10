-- One recipe line per parent x component x revision. A duplicate here fans out
-- every join to the BOM and silently inflates quantities (the classic fanout).
SELECT parent_item_id, component_item_id, bom_rev, COUNT(*) AS n
FROM raw_warp_bom GROUP BY parent_item_id, component_item_id, bom_rev HAVING COUNT(*) > 1;
