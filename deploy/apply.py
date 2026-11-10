"""
Apply the generated ontology DDL (and optionally the assertions) to a Unity Catalog catalog.

Runs as a Databricks job task (spark_python_task) or locally with Databricks Connect.
Idempotent: CREATE ... IF NOT EXISTS, MERGE for enum tables, CHECK constraints that
already exist are skipped, and columns that the ontology has gained since the last
deploy are added with ALTER TABLE ... ADD COLUMNS (from dist/delta/manifest.json).
Renamed or removed columns are never handled here: they are reported and the run
fails, because they need a reviewed migration.

    python deploy/apply.py --dist dist --catalog water_core_dev [--assertions] [--dry-run]

NOT YET RUN AGAINST A WORKSPACE. The statement splitter and the column planner are
unit-tested; the Spark calls are not.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

GENERATED_CATALOG = "water_core"


def split_statements(sql: str) -> list[str]:
    """Split on ';' outside single-quoted strings (backslash escapes) and outside -- comments."""
    out, buf, i, n, in_str = [], [], 0, len(sql), False
    while i < n:
        ch = sql[i]
        if in_str:
            buf.append(ch)
            if ch == "\\" and i + 1 < n:
                buf.append(sql[i + 1]); i += 1
            elif ch == "'":
                in_str = False
        elif ch == "'":
            in_str = True; buf.append(ch)
        elif ch == "-" and sql[i:i + 2] == "--":
            j = sql.find("\n", i)
            i = n if j < 0 else j
            continue
        elif ch == ";":
            stmt = "".join(buf).strip()
            if stmt:
                out.append(stmt)
            buf = []
        else:
            buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return out


def retarget(sql: str, catalog: str) -> str:
    """Generated SQL is fully qualified with water_core; point it at another catalog."""
    return sql if catalog == GENERATED_CATALOG else sql.replace(f"{GENERATED_CATALOG}.", f"{catalog}.")


def plan_columns(expected: list[dict], existing: list[str]) -> tuple[list[dict], list[str]]:
    """Return (columns to add, columns present in the table but no longer in the ontology)."""
    have = {c.lower() for c in existing}
    want = {c["name"].lower() for c in expected}
    return [c for c in expected if c["name"].lower() not in have], sorted(have - want)


def add_columns_sql(table: str, cols: list[dict]) -> str:
    def lit(s: str) -> str:
        return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"
    return f"ALTER TABLE {table} ADD COLUMNS (" + ", ".join(f"{c['name']} {c['type']} COMMENT {lit(c['comment'])}" for c in cols) + ")"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dist", default="dist")
    ap.add_argument("--catalog", default=GENERATED_CATALOG)
    ap.add_argument("--assertions", action="store_true", help="also create the rule views and refresh reference.ontology_violation")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    dist = Path(a.dist)

    from pyspark.sql import SparkSession
    spark = SparkSession.builder.getOrCreate()

    def run(stmt: str) -> None:
        stmt = retarget(stmt, a.catalog)
        if a.dry_run:
            print(stmt[:160].replace("\n", " ")); return
        try:
            spark.sql(stmt)
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if "ADD CONSTRAINT" in stmt and ("already exists" in msg.lower() or "CONSTRAINT_ALREADY_EXISTS" in msg):
                return
            raise

    manifest = json.loads((dist / "delta" / "manifest.json").read_text())["tables"]
    drift = []
    # 1. evolve tables that already exist, before the CREATE IF NOT EXISTS pass leaves them untouched
    for table, cols in manifest.items():
        t = retarget(table, a.catalog)
        if spark.catalog.tableExists(t):
            add, extra = plan_columns(cols, [f.name for f in spark.table(t).schema.fields])
            if extra:
                drift.append(f"{t}: columns no longer in the ontology: {extra}")
            if add:
                run(add_columns_sql(table, add))
    if drift:
        print("Refusing to continue; these need a reviewed migration:\n  " + "\n  ".join(drift)); sys.exit(2)
    # 2. apply every generated file in order
    files = sorted((dist / "delta").glob("*.sql"))
    if a.assertions:
        files += sorted((dist / "assertions").glob("*.sql"))
    for f in files:
        for stmt in split_statements(f.read_text()):
            run(stmt)
        print(f"applied {f.name}")


if __name__ == "__main__":
    main()
