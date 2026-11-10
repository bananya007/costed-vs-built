-- A READY close cannot carry open breaks above materiality.
SELECT source_event_id, break_type, diff_value
FROM out_recon_breaks
WHERE fiscal_period = {{close_period}} AND status = 'OPEN' AND ABS(diff_value) >= {{materiality}};
