-- =====================================================================
-- Close summary: one row per fiscal period. READY only when the
-- reconciliation gate passed; when BLOCKED the check outputs for the period
-- were cleared, so every variance column is NULL (no confident wrong answers).
-- =====================================================================
DELETE FROM out_close_summary WHERE fiscal_period = {{close_period}};

INSERT INTO out_close_summary
SELECT {{close_period}},
       CASE WHEN {{gate_passed}} = 1 THEN 'READY' ELSE 'BLOCKED' END,
       NULL, NOW(), {{materiality}},
       (SELECT COUNT(*) FROM out_recon_breaks WHERE fiscal_period = {{close_period}}
          AND status = 'OPEN' AND ABS(diff_value) >= {{materiality}}),
       (SELECT COALESCE(SUM(ABS(diff_value)), 0) FROM out_recon_breaks WHERE fiscal_period = {{close_period}}
          AND status = 'OPEN' AND ABS(diff_value) >= {{materiality}}),
       (SELECT COUNT(*) FROM out_recon_breaks WHERE fiscal_period = {{close_period}} AND break_type = 'TIMING'),
       (SELECT SUM(eng_change_var) FROM out_wo_material_variance WHERE fiscal_period = {{close_period}}),
       (SELECT SUM(scrap_var) FROM out_wo_material_variance WHERE fiscal_period = {{close_period}}),
       (SELECT SUM(usage_var) FROM out_wo_material_variance WHERE fiscal_period = {{close_period}}),
       (SELECT SUM(labor_eff_var) FROM out_wo_labor_variance WHERE fiscal_period = {{close_period}}),
       (SELECT SUM(labor_rate_var) FROM out_wc_variance WHERE fiscal_period = {{close_period}}),
       (SELECT SUM(overhead_var) FROM out_wc_variance WHERE fiscal_period = {{close_period}}),
       (SELECT SUM(ppv) FROM out_ppv WHERE fiscal_period = {{close_period}}),
       (SELECT SUM(drift_value) FROM out_bom_drift WHERE fiscal_period = {{close_period}}),
       (SELECT SUM(on_hand_value) FROM out_orphaned_stock WHERE fiscal_period = {{close_period}}),
       (SELECT SUM(qty * std_unit_price) FROM out_ppv WHERE fiscal_period = {{close_period}} AND part_phased_out = 1);
