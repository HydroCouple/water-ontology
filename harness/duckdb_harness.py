"""
Local stand-in for the Databricks tables: load ontology rows into DuckDB and run the generated SQL assertions.

Tables are created from the LinkML schema (same names and columns as the Delta
DDL; STRUCT columns are stored as JSON). The views and assertions are the
generated Databricks SQL, transpiled to DuckDB with sqlglot. It checks the
rules' logic on real rows before anything reaches a workspace; it does not
replace running them on Databricks.

Usage:
    python -m harness.duckdb_harness examples/site_drainage/rows            # prints violations, exit 1 on any ERROR
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path

import duckdb
import sqlglot

from generators.linkml_to_assertions import Assertions
from generators.linkml_to_delta import DeltaGenerator, snake

HERE = Path(__file__).resolve().parent.parent
CAT = "water_core"


def duck_type(sql_t: str) -> str:
    if "STRUCT" in sql_t:
        return "JSON"
    m = re.fullmatch(r"ARRAY<(\w+(?:\(\d+,\d+\))?)>", sql_t)
    if m:
        return duck_type(m.group(1)) + "[]"
    return {"STRING": "VARCHAR"}.get(sql_t, sql_t)


def to_duck(sql: str) -> list[str]:
    sql = re.sub(r"\s+COMMENT '(?:[^']|'')*'", "", sql)
    sql = re.sub(r"INSERT OVERWRITE (\S+)", r"DELETE FROM \1; INSERT INTO \1", sql)
    return [s for s in sqlglot.transpile(sql, read="databricks", write="duckdb") if s.strip()]


class Harness:
    def __init__(self, schema: str = str(HERE / "schema" / "wsys.yaml")):
        self.d = DeltaGenerator(schema, CAT)
        self.sv = self.d.sv
        self.con = duckdb.connect()
        self.con.execute(f"ATTACH ':memory:' AS {CAT}")
        self.columns: dict[str, dict[str, str]] = {}
        self.rule_meta: dict[str, tuple[str, str]] = {}
        self._create_tables()
        self._create_views_and_rules(schema)

    def _create_tables(self) -> None:
        schemas = set()
        for c in self.d.concrete_classes():
            sch, tbl = self.d.module_of(c), snake(c)
            if sch not in schemas:
                self.con.execute(f"CREATE SCHEMA IF NOT EXISTS {CAT}.{sch}"); schemas.add(sch)
            cols = {s.name: duck_type(self.d.sql_type(s)[0]) for s in self.d.ordered_slots(c)}
            self.columns[f"{sch}.{tbl}"] = cols
            self.con.execute(f"CREATE TABLE {CAT}.{sch}.{tbl} (" + ", ".join(f'"{n}" {t}' for n, t in cols.items()) + ")")
            if "valid_to" in cols:
                self.con.execute(f"CREATE VIEW {CAT}.{sch}.{tbl}_current AS SELECT * FROM {CAT}.{sch}.{tbl} WHERE valid_to IS NULL")
        for root in ("Asset", "SwmmElement"):
            for stmt in to_duck(self.d.emit_union_view(root)):
                self.con.execute(stmt)

    def _create_views_and_rules(self, schema: str) -> None:
        a = Assertions(schema, CAT)
        with tempfile.TemporaryDirectory() as td:
            import contextlib, io
            with contextlib.redirect_stdout(io.StringIO()):
                a.run(Path(td))
            self.rule_meta = {rid: (sev, desc) for rid, sev, desc, _ in a.rules}
            for f in sorted(Path(td).glob("*.sql")):
                if f.name.startswith("99_"):
                    continue
                for stmt in to_duck(f.read_text()):
                    self.con.execute(stmt)

    def load(self, rows_dir: Path) -> dict[str, int]:
        counts = {}
        for f in sorted(rows_dir.glob("*.jsonl")):
            table = f.stem
            cols = self.columns.get(table)
            if cols is None:
                raise SystemExit(f"{f.name}: no table {table} in the ontology")
            rows = [json.loads(l) for l in f.read_text().splitlines() if l.strip()]
            for r in rows:
                unknown = set(r) - set(cols)
                if unknown:
                    raise SystemExit(f"{f.name}: columns not in the ontology: {sorted(unknown)}")
                names = list(r)
                vals = [json.dumps(r[n]) if cols[n] == "JSON" else r[n] for n in names]
                self.con.execute(f"INSERT INTO {CAT}.{table} (" + ", ".join(f'"{n}"' for n in names) + ") VALUES (" + ", ".join("?" for _ in names) + ")", vals)
            counts[table] = len(rows)
        return counts

    def violations(self) -> list[tuple[str, str, str, str]]:
        out = []
        for rid, (sev, _) in self.rule_meta.items():
            for focus, msg in self.con.execute(f"SELECT focus_iri, message FROM {CAT}.reference.v_violation_{rid}").fetchall():
                out.append((rid, sev, focus, msg))
        return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("rows")
    a = ap.parse_args()
    h = Harness()
    counts = h.load(Path(a.rows))
    v = h.violations()
    print(f"loaded {sum(counts.values())} rows into {len(counts)} tables; ran {len(h.rule_meta)} rules")
    by = {}
    for rid, sev, focus, msg in v:
        by.setdefault((sev, rid), []).append((focus, msg))
    for (sev, rid), items in sorted(by.items()):
        print(f"  {sev:<7} {rid}: {len(items)}   e.g. {items[0][0]} - {items[0][1]}")
    errors = sum(1 for x in v if x[1] == "ERROR")
    print(f"{errors} errors, {len(v) - errors} warnings")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
