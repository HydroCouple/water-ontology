"""The deploy script's pure logic: statement splitting agrees with a real SQL parser, and column planning is additive only."""
import importlib.util
import json
from pathlib import Path

import pytest
import sqlglot

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("apply", ROOT / "deploy" / "apply.py")
apply = importlib.util.module_from_spec(spec); spec.loader.exec_module(apply)


def test_splitter_handles_quotes_and_comments():
    sql = "-- a; comment\nCREATE TABLE t (a STRING COMMENT 'x; y\\'s; z');\nINSERT INTO t VALUES ('a;b');\n"
    assert len(apply.split_statements(sql)) == 2


def test_splitter_agrees_with_parser_on_every_generated_file():
    files = list((ROOT / "dist" / "delta").glob("*.sql")) + list((ROOT / "dist" / "assertions").glob("*.sql"))
    if not files:
        pytest.skip("run `make delta assertions` first")
    for f in files:
        text = f.read_text()
        assert len(apply.split_statements(text)) == len([s for s in sqlglot.parse(text, read="databricks") if s is not None]), f.name


def test_column_plan_adds_new_and_reports_removed():
    expected = [{"name": "iri", "type": "STRING", "comment": ""}, {"name": "new_col", "type": "DOUBLE", "comment": "it's new"}]
    add, extra = apply.plan_columns(expected, ["iri", "old_col"])
    assert [c["name"] for c in add] == ["new_col"] and extra == ["old_col"]
    assert apply.add_columns_sql("c.s.t", add) == "ALTER TABLE c.s.t ADD COLUMNS (new_col DOUBLE COMMENT 'it\\'s new')"


def test_retarget_and_manifest():
    assert apply.retarget("SELECT * FROM water_core.topology.edge", "water_core_dev") == "SELECT * FROM water_core_dev.topology.edge"
    m = ROOT / "dist" / "delta" / "manifest.json"
    if m.exists():
        tables = json.loads(m.read_text())["tables"]
        assert "water_core.topology.edge" in tables and any(c["name"] == "relation" for c in tables["water_core.topology.edge"])
