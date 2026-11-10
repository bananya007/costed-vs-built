-- Referential integrity: every part ID used anywhere must exist in the item master.
-- A part missing from item means a typo, a bad interface message or a broken master.
SELECT u.used_in, u.item_id
FROM (
  SELECT 'bom parent' AS used_in, parent_item_id AS item_id FROM raw_warp_bom
  UNION SELECT 'bom component', component_item_id FROM raw_warp_bom
  UNION SELECT 'std_cost', item_id FROM raw_warp_std_cost
  UNION SELECT 'inventory_txn', item_id FROM raw_warp_inventory_txn
  UNION SELECT 'mos consumption', component_item_id FROM raw_mos_consumption
  UNION SELECT 'mos completion', item_id FROM raw_mos_completion
) u
LEFT JOIN raw_warp_item i ON i.item_id = u.item_id
WHERE i.item_id IS NULL;
