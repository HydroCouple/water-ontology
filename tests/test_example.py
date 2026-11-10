"""The committed site-drainage example loads cleanly, and deliberately broken copies trip the intended rules."""
import json
import shutil
from pathlib import Path

import pytest

duckdb = pytest.importorskip("duckdb")
from harness.duckdb_harness import CAT, Harness  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ROWS = ROOT / "examples" / "site_drainage" / "rows"


def load(rows_dir):
    h = Harness()
    h.load(rows_dir)
    return h


def rules(h):
    return {rid for rid, _, _, _ in h.violations()}


@pytest.fixture()
def broken(tmp_path):
    d = tmp_path / "rows"
    shutil.copytree(ROWS, d)

    def edit(table, fn):
        f = d / f"{table}.jsonl"
        rows = [json.loads(l) for l in f.read_text().splitlines()] if f.exists() else []
        f.write_text("".join(json.dumps(r) + "\n" for r in fn(rows)))
        return d
    return edit


def test_example_is_clean():
    h = load(ROWS)
    assert h.violations() == []
    n = h.con.execute(f"SELECT count(*) FROM {CAT}.reference.entity").fetchone()[0]
    assert n > 50


def test_example_answers_a_cross_layer_question():
    """Asset -> REPRESENTED_BY -> model element, and asset -> simulated max depth, in one query."""
    h = load(ROWS)
    rows = h.con.execute(f"""
        SELECT j.label, m.element_id, o.value
        FROM {CAT}.topology.junction_current j
        JOIN {CAT}.topology.edge_current e ON e.from_iri = j.iri AND e.relation = 'REPRESENTED_BY'
        JOIN {CAT}.model.swmm_element m ON m.iri = e.to_iri
        JOIN {CAT}.observation.observation o ON o.entity_iri = j.iri AND o.property_iri = 'wsys:prop/depth' AND o.result_kind = 'SIMULATION'
        ORDER BY o.value DESC""").fetchall()
    assert len(rows) == 11 and all(label == eid for label, eid, _ in rows) and rows[0][2] > 0


def test_missing_connection_is_caught(broken):
    seen = {"done": False}

    def drop_one(rows):
        out = []
        for r in rows:
            if r["relation"] == "CONNECTS_TO" and not seen["done"]:
                seen["done"] = True
                continue
            out.append(r)
        return out
    assert "link_two_endpoints" in rules(load(broken("topology.edge", drop_one)))


def test_wrong_edge_domain_is_caught(broken):
    def add(rows):
        conduit = next(r["from_iri"] for r in rows if r["relation"] == "CONNECTS_TO" and "/C" in r["from_iri"])
        gage = next(r["to_iri"] for r in rows if r["relation"] == "USES_GAGE")
        return rows + [{"from_iri": conduit, "to_iri": gage, "relation": "DRAINS_TO"}]
    assert "edge_domain_drains_to" in rules(load(broken("topology.edge", add)))


def test_dangling_edge_is_caught(broken):
    d = broken("topology.edge", lambda rows: rows + [{"from_iri": "wsys:asset/demo/J1", "to_iri": "wsys:asset/demo/NOPE", "relation": "ATTACHED_TO"}])
    assert "edge_endpoint_exists" in rules(load(d))


def test_subcatchment_without_outlet_is_caught(broken):
    d = broken("topology.edge", lambda rows: [r for r in rows if not (r["relation"] == "DRAINS_TO" and r["from_iri"].endswith("/S1"))])
    assert "subcatchment_drains_somewhere" in rules(load(d))


def test_resolved_event_without_resolution_and_ai_confirmation_are_caught(broken):
    broken("reference.agent", lambda rows: rows + [{"iri": "wsys:agent/ai/supervisor", "class_iri": "https://w3id.org/wsys/Agent", "agent_kind": "AI_AGENT"}])
    d = broken("event.event", lambda rows: rows + [{
        "iri": "wsys:event/demo/1", "class_iri": "https://w3id.org/wsys/Event", "event_type": "FLOODING", "event_source": "SIMULATION", "severity": "HIGH",
        "lifecycle_state": "RESOLVED", "detected_at": "2026-10-03T14:20:00+00:00", "confirmed_by": "wsys:agent/ai/supervisor"}])
    got = rules(load(d))
    assert {"event_resolved_has_resolution", "event_never_confirmed_by_ai", "event_has_location_or_impact"} <= got


def test_stale_model_link_is_flagged(broken):
    def repoint(rows):
        for r in rows:
            r["current_revision"] = "wsys:revision/some-newer-one"
        return rows
    got = rules(load(broken("topology.water_system", repoint)))
    assert {"represented_by_current_revision", "system_current_revision_exists"} <= got
