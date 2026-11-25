"""Platform manifests become rows with their lineage, and a finding's numbers are checked against the run summary.

The fixture in tests/fixtures/platform is built with the platform's contracts; its numbers are written by hand.
Everything these tests write is kept under dist/test/platform for review.
"""
import json
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

duckdb = pytest.importorskip("duckdb")
from generators.linkml_to_sqlite import build  # noqa: E402
from harness.duckdb_harness import CAT, Harness  # noqa: E402
from loaders import findings  # noqa: E402
from loaders.platform_to_rows import PlatformLoader  # noqa: E402
from loaders.rows import Rows  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures" / "platform"
OUT = ROOT / "dist" / "test" / "platform"
SCHEMAS = ROOT / "vendor" / "hydrocouple.platform" / "schemas"
CLOSED = "wsys:attempt/run_gate_closed/1"
SUMMARY = CLOSED + "/artifact/summary.json"
AGENT, CALL = "wsys:agent/ai/ags_fixture", "wsys:session/ags_fixture/call/call_gate_closed"
STATEMENT = "With the gate closed, J10 floods 2,310 ft3 over 1260 s and C2 peaks at 12.6 CFS."
CLAIMS = [
    {"quoted": "2,310", "unit": "ft3", "evidence": SUMMARY, "locator": "node_flooding/J10/total_flood_volume"},
    {"quoted": "1260", "unit": "s", "evidence": SUMMARY, "locator": "node_flooding/J10/time_flooded"},
    {"quoted": "12.6", "unit": "CFS", "evidence": SUMMARY, "locator": "link_flow_summary/C2/max_flow"},
]


def export(name: str) -> Path:
    out = OUT / name
    shutil.rmtree(out, ignore_errors=True)
    loader = PlatformLoader("demo", "site-drainage")
    for case in ("gate_open", "gate_closed"):
        loader.add_revision(FIX / case / "revision-manifest.json")
        loader.add_attempt(FIX / case / "attempt")
    loader.write(out)
    return out


def record(rows: Path, statement: str = STATEMENT, claims=CLAIMS, **more) -> dict:
    return findings.record_finding(rows, statement=statement, claims=claims, trace_id="journal-1", attributed_to=AGENT,
                                   tool_calls=[CALL], question="What floods when the gate is closed?",
                                   now="2026-11-20T15:05:00+00:00", **more)


def table(rows: Path, name: str) -> list[dict]:
    return findings.read_rows(rows / f"{name}.jsonl")


def rules(rows: Path) -> set[str]:
    h = Harness()
    h.load(rows)
    return {rid for rid, _, _, _ in h.violations()}


def edit(rows: Path, name: str, change) -> None:
    lines = table(rows, name)
    change(lines)
    (rows / f"{name}.jsonl").write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in lines))


@pytest.fixture(scope="module")
def rows():
    return export("rows")


def test_fixture_documents_match_the_vendored_schemas():
    jsonschema = pytest.importorskip("jsonschema")
    for case in ("gate_open", "gate_closed"):
        for path, schema in ((FIX / case / "revision-manifest.json", "revision-manifest-1.json"),
                             (FIX / case / "attempt" / "attempt.json", "attempt-manifest-1.json"),
                             (FIX / case / "attempt" / "summary.json", "run-summary-1.json")):
            jsonschema.validate(json.loads(path.read_text()), json.loads((SCHEMAS / schema).read_text()))


def test_rows_pass_every_rule_and_the_same_manifests_give_the_same_rows(rows):
    assert rules(rows) == set()
    assert json.loads((export("rows_again") / "SUMMARY.json").read_text())["row_sha256"] == \
        json.loads((rows / "SUMMARY.json").read_text())["row_sha256"]
    counts = json.loads((rows / "SUMMARY.json").read_text())["rows"]
    # one person, one AI agent and one engine across two attempts; one session; a tool call per run
    assert counts["reference.agent"] == 3 and counts["analysis.agent_session"] == 1 and counts["analysis.tool_call"] == 2
    assert counts["analysis.revision"] == counts["analysis.run"] == counts["analysis.attempt"] == 2
    revision = table(rows, "analysis.revision")[1]
    assert revision["revision_id"] == "rev_gate_closed" and revision["revision_manifest"]["files"][1]["role"] == "MODEL"
    attempt = table(rows, "analysis.attempt")[1]
    assert attempt["attempt_manifest"]["provenance"]["actor"]["agent_session_id"] == "ags_fixture"
    assert json.loads(attempt["attempt_manifest"]["quality"]) == {"continuity_errors": {"routing": 0.02, "runoff": -0.05}}


def test_a_summary_artifact_has_its_lineage(rows):
    h = Harness()
    h.load(rows)
    got = h.con.execute(f"""SELECT attempt_id, revision_id, revision_manifest_sha256, requested_by, session_id, tool_call_id
                            FROM {CAT}.reference.artifact_lineage WHERE artifact_iri = ?""", [SUMMARY]).fetchall()
    manifest = FIX / "gate_closed" / "revision-manifest.json"
    assert got == [("att_gate_closed", "rev_gate_closed", findings.file_digest(manifest), AGENT, "ags_fixture", "call_gate_closed")]
    person = h.con.execute(f"SELECT acts_on_behalf_of FROM {CAT}.reference.agent WHERE iri = ?", [AGENT]).fetchone()
    assert person == ("wsys:agent/person/alice",)


def test_an_attempt_needs_its_revision_manifest_and_an_unknown_property_is_refused():
    loader = PlatformLoader("demo", "site-drainage")
    with pytest.raises(ValueError, match="--revision"):
        loader.add_attempt(FIX / "gate_open" / "attempt")
    with pytest.raises(ValueError, match="no property 'surprise'"):
        loader.embed("PlatformRevisionManifest", {"surprise": 1})


def test_a_finding_verifies_and_reaches_the_snapshot_with_its_lineage():
    rows = export("finding")
    row = record(rows)
    assert row["verifier_passed"] and [c["found"] for c in row["verifier_report"]["claims"]] == [2310.5, 1260.0, 12.6]
    assert row["evidence"] == [SUMMARY] and row["was_generated_by"] == CALL
    assert "uncited" not in row["verifier_report"]          # J10 and C2 are names, not numbers
    assert rules(rows) == set()
    with pytest.raises(ValueError, match="already recorded"):
        record(rows)
    snapshot = OUT / "finding.sqlite"
    snapshot.unlink(missing_ok=True)
    build(rows, snapshot)
    with closing(sqlite3.connect(snapshot)) as db:
        got = db.execute("""SELECT attempt_id, revision_id, session_id, tool_call_id, verifier_passed
                            FROM reference__finding_lineage WHERE finding_iri = ?""", (row["iri"],)).fetchall()
    assert got == [("att_gate_closed", "rev_gate_closed", "ags_fixture", "call_gate_closed", 1)]


@pytest.mark.parametrize("change, reason", [
    (lambda s, c: (s.replace("2,310", "3,310"), c), "does not contain '2,310'"),                 # the statement alone was edited
    (lambda s, c: (s.replace("2,310", "3,310"), [c[0] | {"quoted": "3,310"}] + c[1:]), "the artifact has 2310.5"),
    (lambda s, c: (s, [c[0] | {"unit": "m3"}] + c[1:]), "the artifact's unit is ft3"),
    (lambda s, c: (s, [c[0] | {"locator": "node_flooding/J99/total_flood_volume"}] + c[1:]), "0 rows match"),
    (lambda s, c: (s, [c[0] | {"evidence": CLOSED + "/artifact/model.rpt"}] + c[1:]), "file is not in these rows"),
])
def test_a_changed_number_fails_and_the_report_names_it(rows, change, reason):
    statement, claims = change(STATEMENT, CLAIMS)
    report = findings.verify(statement, claims, rows)
    failed = [c for c in report["claims"] if not c["passed"]]
    assert not findings.passed(report) and len(failed) == 1
    assert reason in failed[0]["reason"] and failed[0]["locator"].startswith("node_flooding/J")


def test_rounding_follows_the_digits_the_statement_writes(rows):
    def one(quoted):
        return findings.verify(f"J10 floods {quoted} ft3.", [CLAIMS[0] | {"quoted": quoted}], rows)["claims"][0]["passed"]
    assert [one(q) for q in ("2310.5", "2,310", "2311", "2,310.50", "2.3e3")] == [True] * 5
    assert [one(q) for q in ("2310.4", "2312", "2,309", "2.4e3")] == [False] * 4


def test_a_number_without_a_claim_is_listed_and_a_changed_artifact_is_caught():
    rows = export("tampered")
    report = findings.verify(STATEMENT + " That is 4 times the open case.", CLAIMS, rows)
    assert findings.passed(report) and report["uncited"] == ["4"]
    summary = next(p for p in (rows / "artifacts").glob("*.json") if "2310.5" in p.read_text())
    summary.write_text(summary.read_text().replace("2310.5", "9.5"))
    report = findings.verify(STATEMENT, CLAIMS, rows)
    assert {c["reason"] for c in report["claims"]} == {"the artifact's file does not have the checksum its row states"}


def test_findings_and_sessions_break_the_rules_written_for_them():
    rows = export("broken")
    record(rows)
    edit(rows, "analysis.agent_finding", lambda f: f[0].update(evidence=[SUMMARY, "wsys:nothing"], reviewed_by=AGENT))
    edit(rows, "analysis.tool_call", lambda c: c[0].update(in_session="wsys:session/unknown"))
    edit(rows, "reference.agent", lambda a: [r.pop("acts_on_behalf_of", None) for r in a])
    assert rules(rows) == {"finding_evidence_exists", "finding_reviewed_by_a_person", "tool_call_in_a_session",
                           "agent_session_acts_for_someone"}


def test_rows_conform_to_the_shapes():
    shapes = ROOT / "dist" / "wsys.shacl.ttl"
    if not shapes.exists():
        pytest.skip("dist/wsys.shacl.ttl missing; run `make shacl`")
    from pyshacl import validate
    from loaders.rows_to_rdf import graph_from_rows
    rows = export("shapes")
    record(rows)
    conforms, _, text = validate(graph_from_rows(rows), shacl_graph=str(shapes), inference="none", advanced=False)
    assert conforms, text[:2000]


def test_the_shared_writer_reproduces_the_committed_example():
    """swmm_to_rows and platform_to_rows write through loaders.rows; the committed example pins its bytes."""
    example = ROOT / "examples" / "site_drainage" / "rows"
    writer = Rows()
    for path in sorted(example.glob("*.jsonl")):
        writer.rows[path.stem] = findings.read_rows(path)
    out = OUT / "shared_writer"
    out.mkdir(parents=True, exist_ok=True)
    assert writer.write_tables(out) == json.loads((example / "SUMMARY.json").read_text())["row_sha256"]


def test_a_journal_gives_each_call_its_tool_effect_and_position():
    """The hand-written journal in the fixture names the agent session of both attempts."""
    out = OUT / "with_journal"
    shutil.rmtree(out, ignore_errors=True)
    loader = PlatformLoader("demo", "site-drainage")
    for case in ("gate_open", "gate_closed"):
        loader.add_revision(FIX / case / "revision-manifest.json")
        loader.add_attempt(FIX / case / "attempt")
    calls = loader.add_journal(FIX / "journal.jsonl")
    loader.write(out)
    assert rules(out) == set()
    assert calls[2] == CALL  # the journal's submitting call is the call the attempt names
    rows = {r["iri"]: r for r in table(out, "analysis.tool_call")}
    assert len(rows) == 4
    submitted = rows[CALL]
    assert (submitted["sequence"], submitted["effect"]) == (3, "WRITE")
    assert submitted["tool"] == "wsys:tool/openswmm.mcp/0.4.0/platform_run"
    assert len(submitted["arguments_sha256"]) == len(submitted["result_sha256"]) == 64
    tools = {r["tool_name"]: r["read_only"] for r in table(out, "analysis.tool")}
    assert tools == {"platform_targets": True, "platform_run": False, "platform_results": True}
    counts = json.loads((out / "SUMMARY.json").read_text())["rows"]
    assert counts["analysis.agent_session"] == 1 and counts["reference.agent"] == 3


def test_a_journal_line_without_a_session_is_refused():
    path = OUT / "local_journal.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.loads((FIX / "journal.jsonl").read_text().splitlines()[0])
    path.write_text(json.dumps({k: v for k, v in line.items() if k != "agent_session_id"}) + "\n")
    with pytest.raises(ValueError, match="names no agent session"):
        PlatformLoader("demo", "site-drainage").add_journal(path)
