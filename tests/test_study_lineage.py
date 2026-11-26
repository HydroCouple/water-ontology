"""
A platform study's lineage in the graph (the platform plan's M7, work package 7.7).

The fixture is a study an AI agent session ran on the platform through openswmm.mcp:
its manifest, its lineage (study-lineage.json, evaluation-lineage.jsonl) and the
chunk objects holding its evaluation records. Each candidate names the record it is
in, the attempt that evaluated it and the tool call that proposed it, and a finding
can cite an evaluation's numbers against the record's checksum.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

pytest.importorskip("duckdb")
from harness.duckdb_harness import CAT, Harness  # noqa: E402
from loaders import findings  # noqa: E402
from loaders.platform_to_rows import PlatformLoader  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures" / "platform" / "agent_study"
OUT = ROOT / "dist" / "test" / "study_lineage"


def study_copy(name: str) -> Path:
    """The fixture in a fresh folder of its own, so a test can change it."""
    path = OUT / name
    shutil.rmtree(path, ignore_errors=True)
    shutil.copytree(FIX, path / "study")
    return path


def load(path: Path) -> tuple[str, Path]:
    loader = PlatformLoader("demo", "site-drainage")
    study = loader.add_study(path / "study" / "study-manifest.json")
    loader.write(path / "rows")
    return study, path / "rows"


def table(rows: Path, name: str) -> list[dict]:
    return findings.read_rows(rows / f"{name}.jsonl")


def records() -> dict[int, dict]:
    lines = (FIX / "evaluation-lineage.jsonl").read_text(encoding="utf-8").splitlines()
    return {r["evaluation"]: r for r in map(json.loads, lines)}


def test_each_candidate_names_its_record_attempt_and_proposer():
    path = study_copy("linked")
    study, rows = load(path)
    lineage = json.loads((FIX / "study-lineage.json").read_text(encoding="utf-8"))
    actor = lineage["actor"]
    candidates = table(rows, "analysis.candidate")
    assert len(candidates) == len(records()) == 6
    artifacts = {a["iri"]: a for a in table(rows, "analysis.result_artifact")}
    attempts = {a["iri"]: a for a in table(rows, "analysis.attempt")}
    for c in candidates:
        rec = records()[c["candidate_index"]]
        assert c["recorded_in"] == f"{study}/chunk/{rec['chunk_id']}"
        assert artifacts[c["recorded_in"]]["artifact_sha256"] == rec["object_sha256"]
        assert attempts[c["evaluated_by"]]["attempt_id"] == rec["attempt_id"]
        assert c["proposed_by_call"].endswith("/call/" + rec["proposed_by"]["tool_call_id"])
    (row,) = table(rows, "analysis.study")
    assert row["tool_call_id"] == actor["tool_call_id"]
    assert row["in_session"] == f"wsys:session/{actor['agent_session_id']}"
    assert row["was_generated_by"] == f"{row['in_session']}/call/{actor['tool_call_id']}"
    for a in artifacts.values():  # every chunk object was copied by its checksum
        assert (rows / "artifacts" / f"{a['artifact_sha256']}.json").is_file()

    h = Harness()
    h.load(rows)
    assert h.violations() == []
    walked = h.con.execute(f"""SELECT candidate_index, record_sha256, attempt_id, tool_call_id, agent_iri, person_iri
                               FROM {CAT}.reference.candidate_lineage ORDER BY candidate_index""").fetchall()
    assert [w[0] for w in walked] == [1, 2, 3, 4, 5, 6]
    for index, sha, attempt, call, agent, person in walked:
        rec = records()[index]
        assert (sha, attempt, call) == (rec["object_sha256"], rec["attempt_id"], rec["proposed_by"]["tool_call_id"])
        assert agent == f"wsys:agent/ai/{actor['agent_session_id']}"
        assert person == f"wsys:agent/person/{actor['human_subject']}"


def test_a_finding_cites_an_evaluation_against_its_record():
    path = study_copy("finding")
    study, rows = load(path)
    rec = records()[4]
    chunk = (path / "study" / "chunks" / f"{rec['chunk_id']}.jsonl").read_text(encoding="utf-8")
    value = next(json.loads(line) for line in chunk.splitlines()
                 if json.loads(line)["evaluation"] == 4)["objectives"]["flooding_volume"]
    quoted = f"{value:.3f}"
    claim = {"quoted": quoted, "evidence": f"{study}/chunk/{rec['chunk_id']}",
             "locator": "evaluation/4/objectives/flooding_volume"}
    statement = f"Candidate 4 floods {quoted} of water."
    report = findings.verify(statement, [claim], rows)
    assert findings.passed(report), report

    wrong = str(round(value + 1, 3))
    report = findings.verify(statement.replace(quoted, wrong), [claim | {"quoted": wrong}], rows)
    assert not findings.passed(report)
    for locator in ("evaluation/99/objectives/flooding_volume", "evaluation/4/objectives/nothing",
                    "evaluation/4/decisions/subcatchment.width/9"):
        report = findings.verify(statement, [claim | {"locator": locator}], rows)
        assert "does not resolve" in report["claims"][0]["reason"], locator

    copied = rows / "artifacts" / f"{rec['object_sha256']}.json"
    copied.write_text(copied.read_text(encoding="utf-8").replace("4", "5", 1), encoding="utf-8")
    report = findings.verify(statement, [claim], rows)
    assert "checksum" in report["claims"][0]["reason"]


def test_a_chunk_that_is_not_the_lineages_object_is_refused():
    path = study_copy("tampered")
    chunk = next((path / "study" / "chunks").glob("*.jsonl"))
    chunk.write_text(chunk.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="checksum differs"):
        load(path)


def test_a_candidate_naming_an_unknown_record_breaks_a_rule():
    path = study_copy("broken")
    _, rows = load(path)
    candidates = rows / "analysis.candidate.jsonl"
    lines = candidates.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0]) | {"recorded_in": "wsys:study/nowhere/chunk/none"}
    candidates.write_text("\n".join([json.dumps(first), *lines[1:]]) + "\n", encoding="utf-8")
    h = Harness()
    h.load(rows)
    assert {rid for rid, _, _, _ in h.violations()} == {"candidate_record_known"}
