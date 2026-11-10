"""Command line runner: the same steps, in the same order, as the Airflow DAG.

  python -m pipeline.run --close-period 2026-10 --full     # first run: load all history
  python -m pipeline.run --close-period 2026-10            # routine run: reload open periods
  python -m pipeline.run --close-period 2026-10 --export docs/data --label after
"""
import argparse
import json
import uuid

from pipeline import transform
from pipeline.export import export
from pipeline.extract import extract


def run(close_period, full=False, export_dir=None, label=None):
    run_id = f"cli-{uuid.uuid4().hex[:8]}"
    transform.ensure_warehouse()
    loaded = extract(close_period, run_id, full=full)
    transform.build_staging()
    transform.reconcile(close_period)
    passed = transform.gate(close_period)
    if passed:
        transform.run_checks(close_period)
    else:
        transform.clear_checks(close_period)
    transform.close_summary(close_period, passed, run_id)
    transform.assertions(close_period, passed)
    if export_dir:
        export(close_period, export_dir, label or ("after" if passed else "before"))
    return {"run_id": run_id, "status": "READY" if passed else "BLOCKED",
            "rows_loaded": sum(loaded.values())}


def main():
    ap = argparse.ArgumentParser(description="Costed vs built: close pipeline")
    ap.add_argument("--close-period", required=True, help="fiscal period to close, e.g. 2026-10")
    ap.add_argument("--full", action="store_true", help="load all history (first run or rebuild)")
    ap.add_argument("--export", dest="export_dir", help="write JSON outputs to this folder")
    ap.add_argument("--label", help="file name for the export (default: before/after)")
    a = ap.parse_args()
    print(json.dumps(run(a.close_period, a.full, a.export_dir, a.label)))


if __name__ == "__main__":
    main()
