"""Airflow DAG: the monthly-close pipeline, run daily during close.

Flow:
  ensure_warehouse -> extract_open_periods -> build_staging -> reconcile -> gate
      gate passes -> run_checks      (known change, unknown drift, orphaned stock)
      gate fails  -> clear_checks    (publish no numbers while MOS and ERP disagree)
  -> close_summary -> assertions -> export_demo_json

Every task calls a plain function in the pipeline package, so the same logic runs
from the command line and in CI. Params:
  close_period  the month being closed, e.g. 2026-10. Blank = the month before the run date.
  full          load all history (first run or a rebuild) instead of the open periods.
"""
from datetime import datetime, timedelta

from airflow import DAG
from airflow.models.param import Param
from airflow.operators.python import BranchPythonOperator, PythonOperator

from pipeline import transform
from pipeline.export import export
from pipeline.extract import extract


def _period(context):
    """The month being closed: the param if given, otherwise the month before the run date."""
    p = context["params"].get("close_period")
    if p:
        return p
    run_date = context["data_interval_end"]
    first = run_date.replace(day=1)
    prev = first - timedelta(days=1)
    return prev.strftime("%Y-%m")


def _extract(**context):
    loaded = extract(_period(context), context["run_id"], full=context["params"]["full"])
    return sum(loaded.values())


def _reconcile(**context):
    transform.reconcile(_period(context))


def _gate(**context):
    passed = transform.gate(_period(context))
    context["ti"].xcom_push(key="gate_passed", value=passed)
    return "run_checks" if passed else "clear_checks"


def _passed(context):
    return context["ti"].xcom_pull(task_ids="gate", key="gate_passed")


def _run_checks(**context):
    transform.run_checks(_period(context))


def _clear_checks(**context):
    transform.clear_checks(_period(context))


def _close_summary(**context):
    transform.close_summary(_period(context), _passed(context), context["run_id"])


def _assertions(**context):
    transform.assertions(_period(context), _passed(context))


def _export(**context):
    passed = _passed(context)
    export(_period(context), "/opt/airflow/project/docs/data", "after" if passed else "before")


with DAG(
    dag_id="costed_vs_built",
    description="Reconcile MOS vs Warp, then price known change, unknown drift and orphaned stock",
    schedule="0 6 * * *",              # daily 06:00 during close
    start_date=datetime(2026, 10, 1),
    catchup=False,
    max_active_runs=1,                 # never two closes writing the same period at once
    default_args={"retries": 1, "retry_delay": timedelta(minutes=5)},
    params={
        "close_period": Param("", type="string", description="YYYY-MM; blank = previous month"),
        "full": Param(False, type="boolean", description="load all history instead of open periods"),
    },
    tags=["manufacturing-accounting", "close"],
) as dag:
    ensure = PythonOperator(task_id="ensure_warehouse", python_callable=transform.ensure_warehouse)
    ext = PythonOperator(task_id="extract_open_periods", python_callable=_extract)
    staging = PythonOperator(task_id="build_staging", python_callable=transform.build_staging)
    recon = PythonOperator(task_id="reconcile", python_callable=_reconcile)
    gate = BranchPythonOperator(task_id="gate", python_callable=_gate)
    checks = PythonOperator(task_id="run_checks", python_callable=_run_checks)
    clear = PythonOperator(task_id="clear_checks", python_callable=_clear_checks)
    summary = PythonOperator(task_id="close_summary", python_callable=_close_summary,
                             trigger_rule="none_failed_min_one_success")
    asserts = PythonOperator(task_id="assertions", python_callable=_assertions)
    exp = PythonOperator(task_id="export_demo_json", python_callable=_export)

    ensure >> ext >> staging >> recon >> gate >> [checks, clear] >> summary >> asserts >> exp
