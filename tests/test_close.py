"""End-to-end test: generate the month, run the close twice, and check every
planted number against generator/expected.json.

  Run 1 (data as of Nov 1)  -> BLOCKED: a duplicate and a lost interface message
  Nov 2 ERP fixes           -> the duplicate is voided in place, the lost row is posted late
  Run 2 (period reload)     -> READY, and the three checks produce the planted answers
Needs a MySQL server (MYSQL_HOST / MYSQL_PASSWORD).
"""
import json

import pytest
from fastapi.testclient import TestClient

from generator import generate
from app.main import app
from pipeline.db import connect, query
from pipeline.run import run

PERIOD = "2026-10"


def dw(sql, args=None):
    conn = connect("mfg_dw", prefix="DW")
    rows = query(conn, sql, args)
    conn.close()
    return rows


@pytest.fixture(scope="module")
def expected():
    rows, planted = generate.build()
    generate.load(rows)
    exp = generate.expected(planted)
    generate.EXPECTED_PATH.write_text(json.dumps(exp, indent=2, default=str))
    conn = connect(prefix="DW")
    with conn.cursor() as cur:
        cur.execute("DROP DATABASE IF EXISTS mfg_dw")
    conn.commit()
    conn.close()
    return exp


@pytest.fixture(scope="module")
def before(expected):
    return run(PERIOD, full=True)


@pytest.fixture(scope="module")
def after(before):
    generate.apply_corrections()
    return run(PERIOD)            # routine run: period reload only


def test_run_before_corrections_is_blocked(expected, before):
    exp = expected["run_before_corrections"]
    assert before["status"] == exp["status"]
    breaks = dw("SELECT break_type, COUNT(*) AS n, SUM(ABS(diff_value)) AS v FROM out_recon_breaks "
                "WHERE fiscal_period=%s AND status='OPEN' GROUP BY break_type", (PERIOD,))
    assert {b["break_type"]: b["n"] for b in breaks} == exp["open_breaks"]
    assert float(sum(b["v"] for b in breaks)) == pytest.approx(exp["open_break_value"])
    timing = dw("SELECT COUNT(*) AS n FROM out_recon_breaks WHERE fiscal_period=%s AND break_type='TIMING'", (PERIOD,))
    assert timing[0]["n"] == exp["timing_breaks"]
    # A blocked close publishes no variance numbers at all.
    assert dw("SELECT COUNT(*) AS n FROM out_eco_impact WHERE fiscal_period=%s", (PERIOD,))[0]["n"] == 0


def test_period_reload_picks_up_void_and_late_posting(expected, after):
    exp = expected["run_after_corrections"]
    assert after["status"] == exp["status"]
    s = dw("SELECT * FROM out_close_summary WHERE fiscal_period=%s", (PERIOD,))[0]
    assert s["open_break_count"] == exp["open_breaks"]
    assert s["timing_break_count"] == exp["timing_breaks"]


def test_known_change(expected, after):
    rows = dw("SELECT eco_id, SUM(eng_change_var) AS eng, SUM(true_usage_var) AS tru FROM out_eco_impact "
              "WHERE fiscal_period=%s GROUP BY eco_id", (PERIOD,))
    got = {r["eco_id"]: (float(r["eng"]), float(r["tru"])) for r in rows}
    for eco, e in expected["known_change"].items():
        assert got[eco] == pytest.approx((e["eng_change_var"], e["true_usage_var"]))


def test_unknown_drift(expected, after):
    rows = {r["component_item_id"]: r for r in dw("SELECT * FROM out_bom_drift WHERE fiscal_period=%s", (PERIOD,))}
    assert set(rows) == set(expected["drift"])
    for item, e in expected["drift"].items():
        r = rows[item]
        assert float(r["count_shortage_qty"]) == e["count_shortage_qty"]
        assert r["shortage_events"] == e["shortage_events"]
        assert r["likely_cause"] == e["likely_cause"]
        assert float(r["drift_value"]) == pytest.approx(e["drift_value"])
        assert r["count_priority"] == e["count_priority"]
    assert rows["CON-710"]["suggested_qty_per"] == "about 5.0 per unit"


def test_orphaned_stock(expected, after):
    rows = {r["item_id"]: r for r in dw("SELECT * FROM out_orphaned_stock WHERE fiscal_period=%s", (PERIOD,))}
    assert set(rows) == set(expected["orphaned"])
    for item, e in expected["orphaned"].items():
        r = rows[item]
        assert float(r["on_hand_qty"]) == e["on_hand_qty"]
        assert float(r["on_hand_value"]) == pytest.approx(e["on_hand_value"])
        assert float(r["receipts_after_change_qty"]) == e["receipts_after_change_qty"]
        assert r["superseded_by_eco"] == e["superseded_by_eco"]
        assert r["status"] == e["status"]


def test_wo_1001_scrap_and_labor(expected, after):
    mat = dw("SELECT SUM(scrap_var) AS s FROM out_wo_material_variance WHERE fiscal_period=%s "
             "AND work_order_id='WO-1001'", (PERIOD,))[0]
    lab = dw("SELECT labor_eff_var FROM out_wo_labor_variance WHERE fiscal_period=%s AND work_order_id='WO-1001'",
             (PERIOD,))[0]
    assert float(mat["s"]) == pytest.approx(expected["wo_1001"]["scrap_var"])
    assert float(lab["labor_eff_var"]) == pytest.approx(expected["wo_1001"]["labor_eff_var"])


def test_ppv(expected, after):
    s = dw("SELECT ppv FROM out_close_summary WHERE fiscal_period=%s", (PERIOD,))[0]
    assert float(s["ppv"]) == pytest.approx(expected["ppv_total"])


def test_api_serves_the_close(after):
    client = TestClient(app)
    r = client.get(f"/api/close/{PERIOD}")
    assert r.status_code == 200
    assert r.json()["close_summary"][0]["status"] == "READY"
    assert client.get("/api/close/2026-13").status_code == 400      # period is validated
    trace = client.get("/api/trace", params={"wo": "WO-1010", "item": "BRK-INT"}).json()
    voided = [row for row in trace["erp"] if row["is_void"] == 1]
    assert len(voided) == 1                                         # the drill-down shows the void
