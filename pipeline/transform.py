"""Transform steps, in the order the Airflow DAG runs them. Each is a plain
function so the same code runs from Airflow, the command line and CI."""
from datetime import datetime
from pathlib import Path

from pipeline.db import ROOT, connect, query, render, run_sql_file, split_sql

SQL = ROOT / "sql"
MATERIALITY = 25.00   # $ : open breaks at or above this block the close
CHECK_FILES = ["30_material_variance.sql", "32_bom_drift.sql", "33_orphaned_stock.sql"]
CHECK_TABLES = ["out_wo_material_variance", "out_eco_impact", "out_wo_labor_variance", "out_wc_variance",
                "out_ppv", "out_bom_drift", "out_orphaned_stock"]


def _dw():
    return connect("mfg_dw", prefix="DW")


def ensure_warehouse():
    """Create the warehouse database and output tables (idempotent)."""
    conn = connect(prefix="DW")
    with conn.cursor() as cur:
        cur.execute("CREATE DATABASE IF NOT EXISTS mfg_dw")
    conn.commit()
    conn.close()
    conn = _dw()
    run_sql_file(conn, SQL / "warehouse/00_schema.sql")
    conn.close()


def build_staging():
    conn = _dw()
    run_sql_file(conn, SQL / "warehouse/10_staging.sql")
    conn.close()


def reconcile(close_period):
    conn = _dw()
    run_sql_file(conn, SQL / "warehouse/20_reconcile.sql", {"close_period": close_period})
    conn.close()


def gate(close_period):
    """True when no OPEN break at or above materiality remains."""
    conn = _dw()
    rows = query(conn, "SELECT COUNT(*) AS n FROM out_recon_breaks WHERE fiscal_period = %s "
                       "AND status = 'OPEN' AND ABS(diff_value) >= %s", (close_period, MATERIALITY))
    conn.close()
    return rows[0]["n"] == 0


def run_checks(close_period):
    conn = _dw()
    for f in CHECK_FILES:
        run_sql_file(conn, SQL / "warehouse" / f, {"close_period": close_period})
    conn.close()


def clear_checks(close_period):
    """When the gate fails, remove this period's check outputs so nobody reads stale numbers."""
    conn = _dw()
    with conn.cursor() as cur:
        for t in CHECK_TABLES:
            cur.execute(f"DELETE FROM {t} WHERE fiscal_period = %s", (close_period,))
    conn.commit()
    conn.close()


def close_summary(close_period, gate_passed, run_id):
    conn = _dw()
    run_sql_file(conn, SQL / "warehouse/40_close_summary.sql",
                 {"close_period": close_period, "gate_passed": int(gate_passed), "materiality": MATERIALITY})
    with conn.cursor() as cur:
        cur.execute("UPDATE out_close_summary SET run_id = %s WHERE fiscal_period = %s", (run_id, close_period))
        cur.execute("INSERT INTO etl_run_log (run_id, step, periods, finished_at, detail) VALUES (%s,'close',%s,%s,%s)",
                    (run_id, close_period, datetime.now(), "READY" if gate_passed else "BLOCKED"))
    conn.commit()
    conn.close()


class AssertionFailed(Exception):
    pass


def assertions(close_period, gate_passed):
    """Run every SQL assertion; each returns the rows that violate it. Any row fails the run.
    ready_* assertions only apply to a READY close."""
    conn = _dw()
    failures = {}
    for path in sorted((SQL / "assertions").glob("*.sql")):
        if path.name.startswith("ready_") and not gate_passed:
            continue
        stmt = split_sql(path.read_text())[0]
        rows = query(conn, render(stmt, {"close_period": close_period, "materiality": MATERIALITY}))
        if rows:
            failures[path.stem] = rows[:5]
    conn.close()
    if failures:
        raise AssertionFailed(failures)
    return True
