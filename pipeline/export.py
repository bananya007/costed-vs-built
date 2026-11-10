"""Export the close outputs as JSON: the FastAPI app serves the same queries
live, and the static demo page (GitHub Pages) reads these files."""
import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from pipeline.db import connect, query

QUERIES = {
    "close_summary": "SELECT * FROM out_close_summary WHERE fiscal_period = %s",
    "breaks": "SELECT * FROM out_recon_breaks WHERE fiscal_period = %s ORDER BY status DESC, ABS(diff_value) DESC",
    "eco_impact": "SELECT * FROM out_eco_impact WHERE fiscal_period = %s ORDER BY eco_id, work_order_id, component_item_id",
    "wo_material": "SELECT * FROM out_wo_material_variance WHERE fiscal_period = %s ORDER BY work_order_id, component_item_id",
    "drift": "SELECT * FROM out_bom_drift WHERE fiscal_period = %s ORDER BY count_priority",
    "orphaned": "SELECT * FROM out_orphaned_stock WHERE fiscal_period = %s ORDER BY on_hand_value DESC",
    "ppv": "SELECT * FROM out_ppv WHERE fiscal_period = %s ORDER BY receipt_date",
    "labor": "SELECT * FROM out_wo_labor_variance WHERE fiscal_period = %s ORDER BY work_order_id",
    "work_centers": "SELECT * FROM out_wc_variance WHERE fiscal_period = %s ORDER BY work_center_id",
    "ecos": "SELECT e.*, s.bom_rev_used AS costed_rev FROM raw_plm_eco e "
            "LEFT JOIN raw_warp_std_cost s ON s.item_id = e.parent_item_id AND s.cost_version = %s",
}


def _clean(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (datetime, date)):
        return v.isoformat(sep=" ") if isinstance(v, datetime) else v.isoformat()
    return v


def rows_to_json(rows):
    return [{k: _clean(v) for k, v in r.items()} for r in rows]


def fetch(close_period):
    conn = connect("mfg_dw", prefix="DW")
    out = {name: rows_to_json(query(conn, sql, (close_period,))) for name, sql in QUERIES.items()}
    conn.close()
    return out


def trace(work_order_id, item_id):
    """Drill-down: the MOS events and ERP rows behind one work order x part."""
    conn = connect("mfg_dw", prefix="DW")
    mes = query(conn, "SELECT source_event_id, event_type, qty, event_ts FROM stg_mes_events "
                      "WHERE work_order_id = %s AND item_id = %s ORDER BY event_ts", (work_order_id, item_id))
    erp = query(conn, "SELECT txn_id, txn_type, qty, source_event_id, fiscal_period, txn_ts, is_void, note "
                      "FROM raw_warp_inventory_txn WHERE work_order_id = %s AND item_id = %s ORDER BY txn_ts",
                (work_order_id, item_id))
    conn.close()
    return {"mes": rows_to_json(mes), "erp": rows_to_json(erp)}


def trace_all(close_period):
    """Every work order x part that appears on the page, for the static demo."""
    data = fetch(close_period)
    keys = {(r["work_order_id"], r["component_item_id"]) for r in data["eco_impact"] + data["wo_material"]}
    keys |= {(r["work_order_id"], r["item_id"]) for r in data["breaks"] if r["work_order_id"]}
    return {f"{wo}|{item}": trace(wo, item) for wo, item in sorted(keys)}


def export(close_period, out_dir, label, with_trace=True):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    data = fetch(close_period)
    data["meta"] = {"close_period": close_period, "label": label, "exported_at": datetime.now().isoformat(sep=" ")}
    (out / f"{label}.json").write_text(json.dumps(data, indent=1))
    if with_trace:
        (out / f"{label}_trace.json").write_text(json.dumps(trace_all(close_period)))
    return out / f"{label}.json"
