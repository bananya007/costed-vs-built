"""Extract: copy source tables into the warehouse's raw_* tables.

Load pattern = PERIOD RELOAD, chosen because this is period-close work:
  - master data (items, BOMs, standards, change orders, work orders) is small: full reload every run;
  - event tables are reloaded for the OPEN fiscal periods only (delete the period, re-insert it).
Late corrections, voided rows and month-end cutoff postings inside an open period are
picked up on the next run. A timestamp watermark would miss a row voided in place.
The very first run (or a rebuild) uses full=True to load all history once.
"""
import re
from datetime import datetime

from pipeline.db import connect

# (source db, table, SQL expression giving the row's fiscal period; None = master data)
TABLES = [
    ("warp", "item", None), ("warp", "bom", None), ("warp", "routing", None),
    ("warp", "work_center", None),
    ("warp", "std_cost", None), ("warp", "work_order", None), ("plm", "eco", None),
    ("warp", "inventory_txn", "fiscal_period"),
    ("warp", "po_receipt", "fiscal_period"),
    ("warp", "actual_cost_pool", "fiscal_period"),
    ("warp", "cycle_count", "fiscal_period"),
    ("mos", "completion", "DATE_FORMAT(completed_ts, '%Y-%m')"),
    ("mos", "consumption", "DATE_FORMAT(event_ts, '%Y-%m')"),
    ("mos", "scrap", "DATE_FORMAT(event_ts, '%Y-%m')"),
    ("mos", "labor", "DATE_FORMAT(shift_date, '%Y-%m')"),
]
BATCH = 2000


def next_period(period):
    y, m = map(int, period.split("-"))
    return f"{y + (m == 12)}-{1 if m == 12 else m + 1:02d}"


def open_periods(close_period):
    """The month being closed plus the month after it, which is already open."""
    return [close_period, next_period(close_period)]


def _ensure_raw_table(src, dw, db, table, raw):
    with src.cursor() as cur:
        cur.execute(f"SHOW CREATE TABLE {db}.{table}")
        ddl = cur.fetchone()["Create Table"]
    ddl = ddl.replace(f"CREATE TABLE `{table}`", f"CREATE TABLE IF NOT EXISTS `{raw}`", 1)
    ddl = re.sub(r" AUTO_INCREMENT=\d+", "", ddl)
    with dw.cursor() as cur:
        cur.execute(ddl)


def extract(close_period, run_id, full=False):
    """Returns {raw table: rows loaded}."""
    periods = open_periods(close_period)
    src = connect(prefix="SOURCE")
    dw = connect("mfg_dw", prefix="DW")
    loaded = {}
    try:
        for db, table, period_expr in TABLES:
            raw = f"raw_{db}_{table}"
            _ensure_raw_table(src, dw, db, table, raw)
            if period_expr is None or full:
                where, args, scope = "", (), "all"
                delete = f"DELETE FROM {raw}"
            else:
                marks = ",".join(["%s"] * len(periods))
                expr = period_expr.replace("%", "%%")   # escape DATE_FORMAT's % for the driver
                where, args, scope = f" WHERE {expr} IN ({marks})", tuple(periods), ",".join(periods)
                delete = f"DELETE FROM {raw}{where}"
            with src.cursor() as cur:
                cur.execute(f"SELECT * FROM {db}.{table}{where}", args or None)
                rows = cur.fetchall()
            with dw.cursor() as cur:
                cur.execute(delete, args or None)      # replace the period: delete + insert, one transaction
                if rows:
                    cols = list(rows[0].keys())
                    sql = (f"INSERT INTO {raw} ({','.join('`' + c + '`' for c in cols)}) "
                           f"VALUES ({','.join(['%s'] * len(cols))})")
                    for i in range(0, len(rows), BATCH):
                        cur.executemany(sql, [tuple(r[c] for c in cols) for r in rows[i:i + BATCH]])
                cur.execute("INSERT INTO etl_run_log (run_id, step, table_name, periods, rows_loaded, finished_at) "
                            "VALUES (%s, 'extract', %s, %s, %s, %s)", (run_id, raw, scope, len(rows), datetime.now()))
            dw.commit()
            loaded[raw] = len(rows)
    finally:
        src.close()
        dw.close()
    return loaded
