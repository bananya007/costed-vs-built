"""Small MySQL helpers shared by the generator, the pipeline and the app.

Connection settings come from environment variables so the same code runs
locally, inside Docker Compose and in CI.
"""
import os
import re
from pathlib import Path

import pymysql

ROOT = Path(__file__).resolve().parent.parent


def connect(database=None, prefix="MYSQL"):
    """Open a connection. Sources and warehouse can point at different servers
    (SOURCE_* and DW_* variables); in the demo both fall back to MYSQL_*,
    one MySQL server holding four databases."""
    return pymysql.connect(
        host=os.getenv(f"{prefix}_HOST", os.getenv("MYSQL_HOST", "127.0.0.1")),
        port=int(os.getenv(f"{prefix}_PORT", os.getenv("MYSQL_PORT", "3306"))),
        user=os.getenv(f"{prefix}_USER", os.getenv("MYSQL_USER", "root")),
        password=os.getenv(f"{prefix}_PASSWORD", os.getenv("MYSQL_PASSWORD", "")),
        database=database,
        autocommit=False,
        cursorclass=pymysql.cursors.DictCursor,
        charset="utf8mb4",
    )


def split_sql(text):
    """Split a .sql file into statements. Strips '--' comment lines; statements
    end with ';' at the end of a line (none of our SQL has ';' inside strings)."""
    lines = [ln for ln in text.splitlines() if not ln.strip().startswith("--")]
    body = "\n".join(lines)
    return [s.strip() for s in re.split(r";\s*\n", body + "\n") if s.strip()]


PERIOD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def render(sql, params):
    """Fill {{name}} placeholders. Only validated values are allowed, so this
    cannot inject SQL: periods must look like 2026-10, numbers must be numbers."""
    for key, value in params.items():
        if key.endswith("period"):
            if not PERIOD_RE.match(str(value)):
                raise ValueError(f"bad period {value!r}")
            value = f"'{value}'"
        elif isinstance(value, (int, float)):
            value = str(value)
        else:
            raise ValueError(f"unsupported parameter {key}={value!r}")
        sql = sql.replace("{{" + key + "}}", value)
    if "{{" in sql:
        raise ValueError("unfilled placeholder in SQL")
    return sql


def run_sql_file(conn, path, params=None):
    text = Path(path).read_text()
    with conn.cursor() as cur:
        for stmt in split_sql(text):
            cur.execute(render(stmt, params or {}))
    conn.commit()


def query(conn, sql, args=None):
    with conn.cursor() as cur:
        cur.execute(sql, args)
        return cur.fetchall()
