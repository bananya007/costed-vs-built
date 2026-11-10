-- Only checked when the close is READY. Standard + every variance piece must add
-- back, to the cent, to the actual usage as booked in the ERP. If MOS-side and
-- ERP-side actuals disagree, the numbers are not trustworthy.
SELECT work_order_id, component_item_id,
       ROUND(std_qty * std_price + eng_change_var + scrap_var + usage_var, 2) AS mos_side,
       erp_actual_value AS erp_side
FROM out_wo_material_variance
WHERE fiscal_period = {{close_period}}
  AND ABS(ROUND(std_qty * std_price + eng_change_var + scrap_var + usage_var, 2) - erp_actual_value) > 0.01;
