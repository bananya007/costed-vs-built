"""FastAPI app: serves the close outputs live from the warehouse, plus the same
single-page UI that GitHub Pages serves from static JSON.

  uvicorn app.main:app --reload
  GET /api/health                 database reachable?
  GET /api/periods                closed periods available
  GET /api/close/{period}         everything the page shows for one period
  GET /api/trace?wo=..&item=..    drill-down: MOS events and ERP rows behind a number
"""
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from pipeline.db import PERIOD_RE, connect, query
from pipeline.export import fetch, trace

STATIC = Path(__file__).parent / "static"
app = FastAPI(title="Costed vs built",
              description="Is the recipe we're costing the recipe we're actually building?")


@app.get("/api/health")
def health():
    try:
        conn = connect("mfg_dw", prefix="DW")
        query(conn, "SELECT 1")
        conn.close()
        return {"status": "ok"}
    except Exception as exc:  # noqa: BLE001 - report any connection problem plainly
        raise HTTPException(503, f"warehouse not reachable: {exc}") from exc


@app.get("/api/periods")
def periods():
    conn = connect("mfg_dw", prefix="DW")
    rows = query(conn, "SELECT fiscal_period, status, run_at FROM out_close_summary ORDER BY fiscal_period DESC")
    conn.close()
    return rows


@app.get("/api/close/{period}")
def close(period: str):
    if not PERIOD_RE.match(period):
        raise HTTPException(400, "period must look like 2026-10")
    data = fetch(period)
    if not data["close_summary"]:
        raise HTTPException(404, f"no close has run for {period}")
    data["meta"] = {"close_period": period, "label": "live"}
    return data


@app.get("/api/trace")
def drill_down(wo: str = Query(..., max_length=20), item: str = Query(..., max_length=20)):
    return trace(wo, item)


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/", StaticFiles(directory=STATIC), name="static")
