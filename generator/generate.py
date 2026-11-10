"""Synthetic month of factory data for one Giga Texas-like line (October 2026).

Writes three source databases, shaped like Tesla's public picture:
  warp = ERP books (Warp is MySQL-backed), mos = floor events (MOS), plm = design changes.

Everything is deterministic (fixed seed), and every case the pipeline must catch
is PLANTED on purpose, with its exact expected answer written to expected.json:

  Reconciliation breaks   duplicate interface message, lost message, month-end cutoff
  Check 1, known change   ECO-0519 (SUB-200 Rev A -> B) and ECO-0533 (SUB-300 Rev C -> D)
                          go live mid-month while the standard is still on the old revision
  Check 2, unknown drift  CON-710: floor uses 5 per unit, BOM says 4 (steady shortage)
                          FOM-510: foam thrown away without a scrap log on two days (lumpy)
                          BRK-100: 10 brackets missing once (one-off)
  Check 3, orphaned stock BRK-100 and FRM-500 left over after the changes; BRK-100 still received

Usage:
  python -m generator.generate            # create schemas, load the month as of Nov 1
  python -m generator.generate --corrections   # apply the Nov 2 ERP fixes
"""
import argparse
import json
import random
from collections import defaultdict
from itertools import count
from datetime import date, datetime, timedelta
from pathlib import Path

from pipeline.db import ROOT, connect, run_sql_file

SEED = 7
PLANT = "GIGA_TX"
EXPECTED_PATH = Path(__file__).with_name("expected.json")

# ---------------------------------------------------------------- master data
# (item_id, description, std material cost per unit). All bought parts (item_type RAW).
PARTS = [
    ("BRK-100", "Steel bracket, LH", 10.00),
    ("BRK-INT", "Integrated bracket (replaces 4x BRK-100)", 36.00),
    ("TUB-300", "Cross tube", 25.00),
    ("BLT-010", "Flange bolt M8", 0.50),
    ("WSH-015", "Washer M8", 0.05),
    ("FRM-500", "Seat frame, stamped", 120.00),
    ("FRM-505", "Seat frame, cast (replaces FRM-500)", 112.00),
    ("FOM-510", "Seat foam pad", 15.00),
    ("CLP-020", "Trim clip", 0.80),
    ("SCR-030", "Self-tapping screw", 0.20),
    ("PAD-520", "Anti-squeak pad", 4.00),
    ("HRN-700", "Wiring harness", 55.00),
    ("CON-710", "Connector, 12-pin", 2.50),
    ("GRM-730", "Grommet", 0.60),
    ("TIE-720", "Cable tie", 0.05),
    ("LBL-740", "Traceability label", 0.10),
]
STD_PRICE = {p[0]: p[2] for p in PARTS}

SUBS = {  # sub-assemblies built on the line: (description, work center, line-side location, std labor hrs per unit)
    "SUB-200": ("Bracket assembly", "WC-WELD-3", "LS-WELD-3", 0.5),
    "SUB-300": ("Seat frame module", "WC-ASSY-1", "LS-ASSY-1", 0.8),
    "SUB-400": ("Harness module", "WC-ASSY-2", "LS-ASSY-2", 0.4),
}
WORK_CENTERS = {  # (line, area, std labor $/hr, std overhead $/labor hr, actual labor $/hr, actual overhead / applied overhead)
    "WC-WELD-3": ("Body line", "Weld", 40.00, 60.00, 42.00, 1.04),
    "WC-ASSY-1": ("Interior line", "Seats", 42.00, 55.00, 41.00, 1.10),
    "WC-ASSY-2": ("Interior line", "Harness", 38.00, 50.00, 38.00, 1.25),
}

# BOM revisions: parent -> rev -> [(component, qty_per, consume_method)]
BOM = {
    "SUB-200": {
        "A": [("BRK-100", 4, "BACKFLUSH"), ("TUB-300", 2, "SCAN"), ("BLT-010", 8, "BACKFLUSH"), ("WSH-015", 8, "BACKFLUSH")],
        "B": [("BRK-INT", 1, "BACKFLUSH"), ("TUB-300", 2, "SCAN"), ("BLT-010", 8, "BACKFLUSH"), ("WSH-015", 8, "BACKFLUSH")],
    },
    "SUB-300": {
        "C": [("FRM-500", 1, "BACKFLUSH"), ("FOM-510", 2, "BACKFLUSH"), ("CLP-020", 6, "BACKFLUSH"), ("SCR-030", 10, "BACKFLUSH"), ("PAD-520", 2, "BACKFLUSH")],
        "D": [("FRM-505", 1, "BACKFLUSH"), ("FOM-510", 2, "BACKFLUSH"), ("CLP-020", 6, "BACKFLUSH"), ("SCR-030", 10, "BACKFLUSH"), ("PAD-520", 2, "BACKFLUSH")],
    },
    "SUB-400": {
        "A": [("HRN-700", 1, "SCAN"), ("CON-710", 4, "BACKFLUSH"), ("GRM-730", 2, "BACKFLUSH"), ("TIE-720", 12, "BACKFLUSH"), ("LBL-740", 1, "BACKFLUSH")],
    },
}
# Revision dates and the change orders that created them.
REV_DATES = {  # (parent, rev) -> (effective_from, effective_to, eco_id)
    ("SUB-200", "A"): (date(2026, 6, 1), date(2026, 10, 9), "ECO-0412"),
    ("SUB-200", "B"): (date(2026, 10, 10), None, "ECO-0519"),
    ("SUB-300", "C"): (date(2026, 7, 1), date(2026, 10, 19), "ECO-0455"),
    ("SUB-300", "D"): (date(2026, 10, 20), None, "ECO-0533"),
    ("SUB-400", "A"): (date(2026, 5, 1), None, "ECO-0390"),
}
ECOS = [  # the two changes that go live in October (older ones are history)
    ("ECO-0519", "SUB-200", "A", "B", date(2026, 10, 6), date(2026, 10, 10),
     "Replace 4x BRK-100 with 1x integrated bracket BRK-INT", "USE_AS_IS"),
    ("ECO-0533", "SUB-300", "C", "D", date(2026, 10, 15), date(2026, 10, 20),
     "Cast seat frame FRM-505 replaces stamped FRM-500", "USE_AS_IS"),
]
# The October cost roll was done on the OLD revisions; the next roll is Nov 1.
COSTED_REV = {"SUB-200": "A", "SUB-300": "C", "SUB-400": "A"}

# Work orders: (id, item, list of build days, units). 100 units each unless noted.
WORK_ORDERS = [
    ("WO-1001", "SUB-200", [1, 2], 100), ("WO-1002", "SUB-200", [3, 4], 100),
    ("WO-1003", "SUB-200", [6], 100), ("WO-1004", "SUB-200", [8, 9], 100),
    ("WO-1005", "SUB-200", [12], 100), ("WO-1006", "SUB-200", [14], 100),
    ("WO-1007", "SUB-200", [16], 100), ("WO-1008", "SUB-200", [20], 100),
    ("WO-1009", "SUB-200", [23], 100), ("WO-1010", "SUB-200", [30], 100),
    ("WO-2001", "SUB-300", [5, 6], 100), ("WO-2002", "SUB-300", [14, 15], 100),
    ("WO-2003", "SUB-300", [26, 27], 100),
    ("WO-3001", "SUB-400", [6], 50), ("WO-3002", "SUB-400", [13], 50),
    ("WO-3003", "SUB-400", [20], 50), ("WO-3004", "SUB-400", [30, 31], 50),
]
COUNT_DAYS = [8, 15, 22, 29]          # weekly cycle counts at 22:30, every component
OPENING = {"BRK-100": 1200, "BRK-INT": 0, "TUB-300": 2500, "BLT-010": 9000, "WSH-015": 9000,
           "FRM-500": 150, "FRM-505": 0, "FOM-510": 700, "CLP-020": 2000, "SCR-030": 3500,
           "PAD-520": 700, "HRN-700": 250, "CON-710": 1100, "GRM-730": 500, "TIE-720": 3000,
           "LBL-740": 300}
RECEIPTS = [  # (day, hour, item, qty, actual price, supplier)
    (1, 7, "BRK-100", 430, 10.50, "SUP-77"),
    (3, 7, "FRM-500", 100, 121.00, "SUP-12"),
    (9, 7, "BRK-INT", 800, 36.50, "SUP-81"),
    (12, 7, "TUB-300", 500, 24.80, "SUP-77"),
    (15, 7, "BRK-100", 1000, 10.40, "SUP-77"),   # received AFTER ECO-0519 went live
    (18, 7, "FRM-505", 150, 112.00, "SUP-12"),
    (20, 7, "CON-710", 300, 2.50, "SUP-40"),
]


def part_location(item):
    for parent, revs in BOM.items():
        for lines in revs.values():
            if any(c == item for c, _, _ in lines):
                return SUBS[parent][2]
    raise KeyError(item)


def rev_built(parent, ts):
    for (p, rev), (start, end, _) in REV_DATES.items():
        if p == parent and start <= ts.date() and (end is None or ts.date() <= end):
            return rev
    raise ValueError(parent, ts)


def period_of(ts):
    return ts.strftime("%Y-%m")


def unit_times(days, units, wo_id):
    """Spread a work order's units evenly over its build days, 06:00-21:30.
    WO-3004's last three units finish just before midnight on Oct 31: a month-end overtime
    run to finish the order before close. This is the planted cutoff case."""
    per_day = [units // len(days) + (1 if i < units % len(days) else 0) for i in range(len(days))]
    times = []
    for d, n in zip(days, per_day):
        if n == 0:                     # more days than units: nothing built that day
            continue
        start = datetime(2026, 10, d, 6, 0)
        step = (15.5 * 60) / n
        times += [start + timedelta(minutes=round(i * step)) for i in range(n)]
    if wo_id == "WO-3004":
        times[-3:] = [datetime(2026, 10, 31, 23, 52), datetime(2026, 10, 31, 23, 55),
                      datetime(2026, 10, 31, 23, 57)]
    return times


def build():
    rng = random.Random(SEED)
    rows = defaultdict(list)          # table -> list of dicts
    txn_no, cons_no, scrap_no, count_no = count(1), count(1), count(1), count(1)   # id sequences
    phys = []                          # (ts, item, qty) true physical movements at line side

    def txn(txn_type, item, qty, location, ts, wo=None, src=None, period=None, note=None, cost=None):
        c = cost if cost is not None else STD_PRICE.get(item, 0.0)
        r = dict(txn_id=f"IT-{next(txn_no):07d}", txn_type=txn_type, item_id=item, qty=qty,
                 location_id=location, work_order_id=wo, std_unit_cost=c,
                 ext_value=round(qty * c, 2), source_event_id=src, txn_ts=ts,
                 fiscal_period=period or period_of(ts), is_void=0, note=note)
        rows["warp.inventory_txn"].append(r)
        return r

    # ---- masters
    for item, desc, price in PARTS:
        rows["warp.item"].append(dict(item_id=item, item_desc=desc, item_type="RAW", uom="EA",
                                      program="CYBERCAB", status="ACTIVE"))
    for sub, (desc, wc, _, _) in SUBS.items():
        rows["warp.item"].append(dict(item_id=sub, item_desc=desc, item_type="SUBASSY", uom="EA",
                                      program="CYBERCAB", status="ACTIVE"))
    for (parent, rev), (start, end, eco) in REV_DATES.items():
        for comp, qty, method in BOM[parent][rev]:
            rows["warp.bom"].append(dict(parent_item_id=parent, component_item_id=comp, bom_rev=rev,
                                         qty_per=qty, effective_from=start, effective_to=end,
                                         eco_id=eco, consume_method=method))
    for sub, (_, wc, _, hrs) in SUBS.items():
        rows["warp.routing"].append(dict(item_id=sub, op_seq=10, work_center_id=wc, std_labor_hrs=hrs))
    for wc, (line, area, lr, ohr, _, _) in WORK_CENTERS.items():
        rows["warp.work_center"].append(dict(work_center_id=wc, line_name=line, area=area, plant=PLANT,
                                             std_labor_rate=lr, std_overhead_rate=ohr))
    for e in ECOS:
        rows["plm.eco"].append(dict(zip(["eco_id", "parent_item_id", "from_rev", "to_rev",
                                         "released_date", "effective_date", "reason", "disposition"], e)))

    # Standard costs, cost version 2026-10. Sub-assemblies were rolled on the OLD revisions:
    # material = sum(qty_per x part price) on COSTED_REV, plus std hours x labor and overhead rates.
    version, start, end = "2026-10", date(2026, 10, 1), date(2026, 10, 31)
    for item, _, price in PARTS:
        rows["warp.std_cost"].append(dict(item_id=item, cost_version=version, effective_from=start,
                                          effective_to=end, bom_rev_used=None, material_cost=price,
                                          labor_cost=0, overhead_cost=0, total_std_cost=price))
    for sub, (_, wc, _, hrs) in SUBS.items():
        rev = COSTED_REV[sub]
        mat = sum(q * STD_PRICE[c] for c, q, _ in BOM[sub][rev])
        lr, ohr = WORK_CENTERS[wc][2], WORK_CENTERS[wc][3]
        rows["warp.std_cost"].append(dict(item_id=sub, cost_version=version, effective_from=start,
                                          effective_to=end, bom_rev_used=rev, material_cost=round(mat, 4),
                                          labor_cost=hrs * lr, overhead_cost=hrs * ohr,
                                          total_std_cost=round(mat + hrs * lr + hrs * ohr, 4)))
    sub_std = {r["item_id"]: r["total_std_cost"] for r in rows["warp.std_cost"]}

    # ---- opening balances (Sept 30) and receipts
    for item, qty in OPENING.items():
        if qty:
            ts = datetime(2026, 9, 30, 23, 0)
            txn("OPENING_BAL", item, qty, part_location(item), ts)
            phys.append((ts, item, qty))
    for i, (d, h, item, qty, price, sup) in enumerate(RECEIPTS, 1):
        ts = datetime(2026, 10, d, h, 0)
        loc = part_location(item)
        rows["warp.po_receipt"].append(dict(receipt_id=f"RC-{30000 + i}", po_line_id=f"PO-{9900 + i}-1",
                                            supplier_id=sup, item_id=item, qty=qty, actual_unit_price=price,
                                            std_unit_price=STD_PRICE[item], location_id=loc, receipt_ts=ts,
                                            fiscal_period=period_of(ts)))
        txn("RECEIPT", item, qty, loc, ts, src=f"RC-{30000 + i}")
        phys.append((ts, item, qty))

    planted = {}
    # ---- production
    for wo_id, sub, days, units in WORK_ORDERS:
        _, wc, loc, std_hrs = SUBS[sub]
        first_ts = datetime(2026, 10, days[0], 5, 30)
        rows["warp.work_order"].append(dict(work_order_id=wo_id, item_id=sub, work_center_id=wc,
                                            bom_rev=rev_built(sub, first_ts), planned_qty=units,
                                            released_ts=first_ts, status="COMPLETE"))
        for n, ts in enumerate(unit_times(days, units, wo_id), 1):
            rev = rev_built(sub, ts)
            serial = f"{sub}-{wo_id[3:]}-{n:03d}"
            cutoff = ts.day == 31 and ts.hour == 23          # ERP posts these after midnight
            delay = timedelta(minutes=8 if cutoff else 1)
            rows["mos.completion"].append(dict(unit_serial=serial, work_order_id=wo_id, item_id=sub,
                                               bom_rev_built=rev, station_id=f"{wc[3:]}-07",
                                               first_pass=1, completed_ts=ts))
            txn("WO_COMPLETION", sub, 1, "WH-SUB", ts + delay, wo=wo_id, src=serial, cost=sub_std[sub])
            for comp, qty, method in BOM[sub][rev]:
                rec_qty = qty
                if wo_id == "WO-1002" and comp == "TUB-300" and n in (17, 58):
                    rec_qty = 3                                  # scanned an extra tube (real usage)
                eid = f"MC-{next(cons_no):07d}"
                rows["mos.consumption"].append(dict(event_id=eid, work_order_id=wo_id, unit_serial=serial,
                                                    component_item_id=comp, qty=rec_qty, consume_method=method,
                                                    bom_rev_built=rev, event_ts=ts))
                true_qty = rec_qty + (1 if comp == "CON-710" else 0)   # floor really uses 5, BOM says 4
                phys.append((ts, comp, -true_qty))
                if wo_id == "WO-3004" and comp == "HRN-700" and n == 5:
                    planted["lost_event_id"] = eid                     # never reaches the ERP
                    continue
                t = txn("ISSUE_TO_WO", comp, -rec_qty, loc, ts + delay, wo=wo_id, src=eid)
                if wo_id == "WO-1010" and comp == "BRK-INT" and n == 40:
                    dup = txn("ISSUE_TO_WO", comp, -rec_qty, loc, ts + delay + timedelta(seconds=30),
                              wo=wo_id, src=eid, note="interface retry")
                    planted["duplicate_txn_id"] = dup["txn_id"]
                    planted["duplicate_event_id"] = eid
        # labor: one booking per work order
        hours = round(units * std_hrs * (1.10 if wo_id == "WO-1001" else rng.uniform(0.97, 1.05)), 2)
        rows["mos.labor"].append(dict(labor_entry_id=f"LB-{wo_id[3:]}", work_order_id=wo_id, op_seq=10,
                                      hours=hours, shift_date=date(2026, 10, days[0])))

    # ---- logged scrap (appears in MOS and the ERP)
    for wo_id, item, qty, day, hh, station in [("WO-1001", "BRK-100", 20, 1, 14, "WELD-3-03"),
                                               ("WO-1007", "BRK-INT", 20, 16, 13, "WELD-3-05")]:
        sid = f"SC-{10440 + next(scrap_no)}"
        ts = datetime(2026, 10, day, hh, 0)
        rows["mos.scrap"].append(dict(scrap_event_id=sid, work_order_id=wo_id, item_id=item, qty=qty,
                                      reason_code="DMG-HANDLING", station_id=station, event_ts=ts))
        txn("SCRAP", item, -qty, part_location(item), ts + timedelta(minutes=1), wo=wo_id, src=sid)
        phys.append((ts, item, -qty))

    # ---- UNlogged losses: physical only, the ERP never hears about them
    phys += [(datetime(2026, 10, 2, 10, 0), "BRK-100", -10),     # one-off, cause unknown
             (datetime(2026, 10, 14, 11, 0), "FOM-510", -25),    # foam binned without a scrap log
             (datetime(2026, 10, 26, 15, 0), "FOM-510", -15)]

    # ---- weekly cycle counts: counted = physical, book = ERP; the gap is booked as CYCLE_ADJ
    for d in COUNT_DAYS:
        cts = datetime(2026, 10, d, 22, 30)
        for item, _, _ in PARTS:
            loc = part_location(item)
            book = sum(r["qty"] for r in rows["warp.inventory_txn"]
                       if r["item_id"] == item and r["location_id"] == loc and r["txn_ts"] <= cts)
            physical = sum(q for t, i, q in phys if i == item and t <= cts)
            if book == 0 and physical == 0:
                continue
            count_id = f"CC-{2200 + next(count_no)}"
            adj = physical - book
            rows["warp.cycle_count"].append(dict(count_id=count_id, item_id=item,
                                                 location_id=loc, count_ts=cts, fiscal_period=period_of(cts),
                                                 book_qty=book, counted_qty=physical, adj_qty=adj))
            if adj:
                txn("CYCLE_ADJ", item, adj, loc, cts, src=count_id)

    # ---- actual labor and overhead pools per work center
    for wc, (_, _, lr, ohr, actual_rate, oh_factor) in WORK_CENTERS.items():
        wos = [w for w in WORK_ORDERS if SUBS[w[1]][1] == wc]
        hours = sum(l["hours"] for l in rows["mos.labor"] if any(l["work_order_id"] == w[0] for w in wos))
        std_hours = sum(w[3] * SUBS[w[1]][3] for w in wos)
        rows["warp.actual_cost_pool"] += [
            dict(fiscal_period="2026-10", work_center_id=wc, cost_type="LABOR",
                 actual_amount=round(hours * actual_rate, 2), actual_hours=round(hours, 2)),
            dict(fiscal_period="2026-10", work_center_id=wc, cost_type="OVERHEAD",
                 actual_amount=round(std_hours * ohr * oh_factor, 2), actual_hours=None)]
    return rows, planted


def expected(planted):
    """The answers the pipeline must reproduce exactly (October 2026 close)."""
    return {
        "close_period": "2026-10",
        "planted": planted,
        "run_before_corrections": {
            "status": "BLOCKED",
            "open_breaks": {"DUPLICATE": 1, "MISSING_IN_ERP": 1},
            "open_break_value": 36.00 + 55.00,
            "timing_breaks": 3 * 6,     # 3 units after the cutoff x (5 components + 1 completion)
        },
        "run_after_corrections": {"status": "READY", "open_breaks": 0, "timing_breaks": 18},
        "known_change": {
            "ECO-0519": {"eng_change_var": 600 * 36.00 - 600 * 4 * 10.00, "true_usage_var": 20 * 36.00},
            "ECO-0533": {"eng_change_var": 100 * 112.00 - 100 * 120.00, "true_usage_var": 0.0},
        },
        "drift": {
            "FOM-510": {"count_shortage_qty": 40, "shortage_events": 2, "likely_cause": "UNRECORDED_SCRAP",
                        "drift_value": 600.00, "count_priority": 1},
            "CON-710": {"count_shortage_qty": 150, "shortage_events": 3, "likely_cause": "BOM_QTY",
                        "drift_value": 375.00, "count_priority": 2},
            "BRK-100": {"count_shortage_qty": 10, "shortage_events": 1, "likely_cause": "UNKNOWN",
                        "drift_value": 100.00, "count_priority": 3},
        },
        "orphaned": {
            "BRK-100": {"on_hand_qty": 1000, "on_hand_value": 10000.00, "receipts_after_change_qty": 1000,
                        "superseded_by_eco": "ECO-0519", "status": "NO_REMAINING_USE"},
            "FRM-500": {"on_hand_qty": 50, "on_hand_value": 6000.00, "receipts_after_change_qty": 0,
                        "superseded_by_eco": "ECO-0533", "status": "NO_REMAINING_USE"},
        },
        "wo_1001": {"scrap_var": 200.00, "labor_eff_var": 200.00},
        "ppv_total": 215 + 400 + 400 + 100 - 100,
    }


def load(rows):
    for db in ("warp", "mos", "plm"):
        conn = connect(prefix="SOURCE")
        run_sql_file(conn, ROOT / f"sql/sources/{db}.sql")
        conn.close()
    conn = connect(prefix="SOURCE")
    with conn.cursor() as cur:
        for table, data in rows.items():
            cols = list(data[0].keys())
            sql = f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join(['%s'] * len(cols))})"
            cur.executemany(sql, [tuple(r[c] for c in cols) for r in data])
    conn.commit()
    conn.close()


def apply_corrections():
    """What the ERP team does on Nov 2, while October is still open:
    1. VOIDS the duplicate row in place (an update: no new row, no new timestamp),
    2. posts the lost harness issue late, into fiscal period 2026-10.
    A row-level incremental load keyed on timestamps would miss fix 1; a period reload catches it."""
    planted = json.loads(EXPECTED_PATH.read_text())["planted"]
    conn = connect(prefix="SOURCE")
    with conn.cursor() as cur:
        cur.execute("UPDATE warp.inventory_txn SET is_void = 1, note = 'voided: duplicate interface message' "
                    "WHERE txn_id = %s", (planted["duplicate_txn_id"],))
        cur.execute("SELECT work_order_id, component_item_id, qty FROM mos.consumption WHERE event_id = %s",
                    (planted["lost_event_id"],))
        ev = cur.fetchone()
        item = ev["component_item_id"]
        cur.execute("SELECT COALESCE(MAX(CAST(SUBSTRING(txn_id, 4) AS UNSIGNED)), 0) AS n FROM warp.inventory_txn")
        n = int(cur.fetchone()["n"]) + 1
        cur.execute(
            "INSERT INTO warp.inventory_txn (txn_id, txn_type, item_id, qty, location_id, work_order_id, "
            "std_unit_cost, ext_value, source_event_id, txn_ts, fiscal_period, is_void, note) "
            "VALUES (%s,'ISSUE_TO_WO',%s,%s,%s,%s,%s,%s,%s,'2026-11-02 09:00:00','2026-10',0,"
            "'late posting of a lost interface message')",
            (f"IT-{n:07d}", item, -ev["qty"], part_location(item), ev["work_order_id"],
             STD_PRICE[item], round(-float(ev["qty"]) * STD_PRICE[item], 2), planted["lost_event_id"]))
    conn.commit()
    conn.close()
    print("Applied Nov 2 corrections: voided", planted["duplicate_txn_id"], "and posted", planted["lost_event_id"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corrections", action="store_true", help="apply the Nov 2 ERP fixes")
    args = ap.parse_args()
    if args.corrections:
        apply_corrections()
        return
    rows, planted = build()
    load(rows)
    EXPECTED_PATH.write_text(json.dumps(expected(planted), indent=2, default=str))
    counts = {t: len(v) for t, v in rows.items()}
    print("Loaded synthetic October 2026:", json.dumps(counts))


if __name__ == "__main__":
    main()
