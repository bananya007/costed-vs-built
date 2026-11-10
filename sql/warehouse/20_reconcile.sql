-- =====================================================================
-- Reconciliation gate: does the ERP tell the same story as MOS?
-- Event-level match on source_event_id, so every break names the exact
-- floor event behind it. Replaced one fiscal period at a time.
--   TIMING          the ERP booked it in another period (month-end cutoff) -> EXPLAINED
--   MISSING_IN_ERP  the interface message never arrived                   -> OPEN
--   DUPLICATE       the message arrived more than once                    -> OPEN
--   QTY_MISMATCH    one ERP row, wrong quantity                           -> OPEN
--   MISSING_IN_MES  an ERP row points at an event MOS never recorded      -> OPEN
-- =====================================================================
DELETE FROM out_recon_breaks WHERE fiscal_period = {{close_period}};

INSERT INTO out_recon_breaks
  (fiscal_period, source_event_id, event_type, work_order_id, item_id, mes_qty,
   erp_qty_in_period, erp_qty_other_period, erp_rows, diff_qty, diff_value, break_type, status, detail)
WITH erp AS (
  SELECT source_event_id,
         COUNT(*) AS erp_rows,
         SUM(CASE WHEN fiscal_period = {{close_period}} THEN mes_qty ELSE 0 END) AS q_in,
         SUM(CASE WHEN fiscal_period <> {{close_period}} THEN mes_qty ELSE 0 END) AS q_out,
         GROUP_CONCAT(DISTINCT fiscal_period ORDER BY fiscal_period) AS periods
  FROM stg_erp_mes_txns
  GROUP BY source_event_id
),
classified AS (
  SELECT m.source_event_id, m.event_type, m.work_order_id, m.item_id, m.qty,
         COALESCE(e.erp_rows, 0) AS erp_rows, COALESCE(e.q_in, 0) AS q_in,
         COALESCE(e.q_out, 0) AS q_out, e.periods,
         CASE
           WHEN COALESCE(e.q_in, 0) = m.qty THEN NULL
           WHEN COALESCE(e.erp_rows, 0) = 0 THEN 'MISSING_IN_ERP'
           WHEN COALESCE(e.q_in, 0) = 0 AND e.q_out = m.qty THEN 'TIMING'
           WHEN e.erp_rows > 1 THEN 'DUPLICATE'
           ELSE 'QTY_MISMATCH'
         END AS break_type
  FROM stg_mes_events m
  LEFT JOIN erp e ON e.source_event_id = m.source_event_id
  WHERE m.mes_period = {{close_period}}
)
SELECT {{close_period}}, c.source_event_id, c.event_type, c.work_order_id, c.item_id, c.qty,
       c.q_in, c.q_out, c.erp_rows, c.q_in - c.qty,
       ROUND((c.q_in - c.qty) * s.std_cost, 2),
       c.break_type,
       CASE WHEN c.break_type = 'TIMING' THEN 'EXPLAINED' ELSE 'OPEN' END,
       CASE c.break_type
         WHEN 'TIMING' THEN CONCAT('Booked by the ERP in ', c.periods, '; lands next period')
         WHEN 'MISSING_IN_ERP' THEN 'MOS recorded it; no ERP row exists'
         WHEN 'DUPLICATE' THEN CONCAT('ERP holds ', c.erp_rows, ' rows for one MOS event')
         ELSE 'ERP quantity differs from MOS'
       END
FROM classified c
JOIN stg_item_std s ON s.item_id = c.item_id AND s.cost_version = {{close_period}}
WHERE c.break_type IS NOT NULL
UNION ALL
-- ERP rows in this period that point at an event MOS never recorded at all
SELECT {{close_period}}, e.source_event_id, 'ERP_ONLY', MAX(e.work_order_id), MAX(e.item_id), 0,
       SUM(e.mes_qty), 0, COUNT(*), SUM(e.mes_qty), ROUND(SUM(e.mes_qty) * MAX(s.std_cost), 2),
       'MISSING_IN_MES', 'OPEN', 'ERP row with no matching MOS event'
FROM stg_erp_mes_txns e
LEFT JOIN stg_mes_events m ON m.source_event_id = e.source_event_id
JOIN stg_item_std s ON s.item_id = e.item_id AND s.cost_version = {{close_period}}
WHERE e.fiscal_period = {{close_period}} AND m.source_event_id IS NULL
GROUP BY e.source_event_id;
