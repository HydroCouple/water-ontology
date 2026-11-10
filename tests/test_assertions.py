"""Generated SQL assertions parse as Databricks SQL and only reference objects the generated DDL creates."""
import re
from pathlib import Path

import pytest
import sqlglot
from sqlglot import exp

ROOT = Path(__file__).resolve().parent.parent
DELTA, ASSERT = ROOT / "dist" / "delta", ROOT / "dist" / "assertions"


@pytest.fixture(scope="module")
def created():
    if not DELTA.exists() or not ASSERT.exists():
        pytest.skip("run `make delta assertions` first")
    names = set()
    for f in list(DELTA.glob("*.sql")) + list(ASSERT.glob("*.sql")):
        names |= set(re.findall(r"CREATE (?:OR REPLACE )?(?:TABLE|VIEW)(?: IF NOT EXISTS)? ([\w.]+)", f.read_text()))
    return names


def test_assertions_parse_and_reference_existing_objects(created):
    files = sorted(ASSERT.glob("*.sql"))
    assert len(files) > 30
    for f in files:
        for stmt in sqlglot.parse(f.read_text(), read="databricks"):
            if stmt is None:
                continue
            for t in stmt.find_all(exp.Table):
                name = ".".join(p for p in (t.catalog, t.db, t.name) if p)
                if name.count(".") == 2:
                    assert name in created, f"{f.name} references {name}, which no generated DDL creates"


def test_every_edge_relation_has_a_domain_rule():
    import yaml
    from linkml_runtime.utils.schemaview import SchemaView
    sv = SchemaView(str(ROOT / "schema" / "wsys.yaml"))
    dom = yaml.safe_load((ROOT / "schema/mappings/edge_domains.yaml").read_text())
    for enum in ("EdgeRelation", "EventLinkRelation"):
        declared = set(sv.get_enum(enum).permissible_values)
        covered = set(dom[enum])
        assert declared - covered <= {"REPORTED_IN"}, f"{enum} values without a domain: {declared - covered}"
