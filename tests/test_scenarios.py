"""Phase 2 vocabulary: derived revisions, scenarios, studies with typed candidates, migration.

The study fixture is a real platform study manifest (hydrocouple.platform's alternatives test
on the twin-tank model); the derived revision and the override attempt are the Phase 1 fixture
manifests with a lineage or overrides added. Files are kept under dist/test/scenarios.
"""
import json
import shutil
from pathlib import Path

import pytest

pytest.importorskip("duckdb")
from harness.duckdb_harness import Harness  # noqa: E402
from loaders import findings  # noqa: E402
from loaders.platform_to_rows import PlatformLoader  # noqa: E402
from migrations.v0_3 import migrate  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures" / "platform"
OUT = ROOT / "dist" / "test" / "scenarios"
STUDY = FIX / "study" / "study-manifest.json"
STUDY_ID = json.loads(STUDY.read_text())["runtime"]["study_id"]


def folder(name: str) -> Path:
    path = OUT / name
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True)
    return path


def rules(rows: Path) -> set[str]:
    h = Harness()
    h.load(rows)
    return {rid for rid, _, _, _ in h.violations()}


def table(rows: Path, name: str) -> list[dict]:
    return findings.read_rows(rows / f"{name}.jsonl")


def derived_manifest(where: Path) -> Path:
    base = FIX / "gate_open" / "revision-manifest.json"
    manifest = json.loads((FIX / "gate_closed" / "revision-manifest.json").read_text())
    manifest["derived_from"] = {"revision_id": "rev_gate_open", "revision_manifest_sha256": findings.file_digest(base),
                                "study_id": STUDY_ID, "evaluation": 3,
                                "decisions": {"node_max_depth": [4.0, 2.0], "link_diameter": [0.5]}}
    path = where / "derived-manifest.json"
    path.write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")))
    return path


def test_a_derived_revision_names_its_base_and_the_study_that_chose_it():
    out = folder("derived")
    loader = PlatformLoader("demo", "site-drainage")
    base = loader.add_revision(FIX / "gate_open" / "revision-manifest.json")
    derived = loader.add_revision(derived_manifest(out))
    loader.write(out / "rows")
    row = next(r for r in table(out / "rows", "analysis.revision") if r["iri"] == derived)
    assert row["was_derived_from"] == [base]
    assert row["derived_in_study"] == f"wsys:study/{STUDY_ID}" and row["chosen_evaluation"] == 3
    assert rules(out / "rows") == set()

    alone = PlatformLoader("demo", "site-drainage")
    alone.add_revision(derived_manifest(out))
    alone.write(out / "without_base")
    assert rules(out / "without_base") == {"derived_revision_base_known"}


def override_attempt(where: Path, run_id: str, path: str) -> Path:
    attempt = where / run_id
    shutil.copytree(FIX / "gate_closed" / "attempt", attempt)
    manifest = json.loads((attempt / "attempt.json").read_text())
    p = manifest["provenance"]
    p.update(run_id=run_id, attempt_id=f"att_{run_id}", workflow_version="2")
    requested = [{"path": path, "id": "S1", "value": 300}]
    p["parameters"] = {"overrides": requested}
    manifest["quality"]["overrides"] = [requested[0] | {"base_value": 1587.0, "unit": "ft"}]
    (attempt / "attempt.json").write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")))
    return attempt


def test_an_override_run_executes_a_scenario_of_its_revision():
    out = folder("scenario")
    loader = PlatformLoader("demo", "site-drainage")
    revision = loader.add_revision(FIX / "gate_closed" / "revision-manifest.json")
    loader.add_attempt(override_attempt(out, "run_scenario", "subcatchment.width"))
    loader.write(out / "rows")
    (scenario,) = table(out / "rows", "analysis.scenario")
    (override,) = table(out / "rows", "analysis.scenario_override")
    assert scenario["of_revision"] == revision
    assert (override["catalog_path"], override["override_element_id"], override["override_value"],
            override["base_value"], override["override_unit"]) == ("subcatchment.width", "S1", 300.0, 1587.0, "ft")
    (run,) = table(out / "rows", "analysis.run")
    assert run["of_scenario"] == scenario["iri"] and run["workflow"] == "swmm.simulation@2"
    assert rules(out / "rows") == set()

    bad = PlatformLoader("demo", "site-drainage")
    bad.add_revision(FIX / "gate_closed" / "revision-manifest.json")
    bad.add_attempt(override_attempt(out, "run_read_only", "link.stats.max_flow"))
    bad.write(out / "read_only")
    assert rules(out / "read_only") == {"scenario_override_writable"}


def test_a_study_manifest_is_a_study_with_typed_candidates():
    out = folder("study")
    loader = PlatformLoader("demo", "twin-tank")
    study = loader.add_study(STUDY)
    loader.write(out / "rows")
    (row,) = table(out / "rows", "analysis.study")
    assert row["iri"] == study and row["study_id"] == STUDY_ID
    assert row["workflow"] == "gym.evaluate@1" and row["was_generated_by"].startswith("wsys:job/run_")
    candidates = {c["candidate_index"]: c for c in table(out / "rows", "analysis.candidate")}
    assert sorted(candidates) == [1, 2, 3]
    assert [c["is_pareto"] for _, c in sorted(candidates.items())] == [True, False, True]
    third = candidates[3]
    assert third["decision_values"] == [
        {"dimension": "node_max_depth", "label": "node_max_depth:T1", "value": 4.0},
        {"dimension": "node_max_depth", "label": "node_max_depth:T2", "value": 2.0},
        {"dimension": "link_diameter", "label": "link_diameter:OUT", "value": 0.5}]
    assert [(o["name"], o["direction"]) for o in third["objective_values"]] == [
        ("flooding_volume", "minimize"), ("peak_outflow", "minimize")]
    assert rules(out / "rows") == set()


def test_0_2_candidates_migrate_to_typed_lists():
    old, new = folder("migration_old"), OUT / "migration_new"
    shutil.rmtree(new, ignore_errors=True)
    (old / "analysis.candidate.jsonl").write_text(json.dumps({
        "iri": "wsys:study/s/candidate/1", "class_iri": "https://w3id.org/wsys/Candidate", "candidate_index": 1,
        "parameters": json.dumps({"w": [300.0, 2500.0], "n": 0.013}),
        "objective_values": json.dumps({"flooding": 2.5})}) + "\n")
    (old / "analysis.study.jsonl").write_text('{"iri": "wsys:study/s"}\n')
    assert migrate(old, new) == {"candidates": 1}
    (row,) = table(new, "analysis.candidate")
    assert "parameters" not in row
    assert row["decision_values"] == [{"dimension": "w", "value": 300.0}, {"dimension": "w", "value": 2500.0},
                                      {"dimension": "n", "value": 0.013}]
    assert row["objective_values"] == [{"name": "flooding", "value": 2.5}]
    assert (new / "analysis.study.jsonl").read_text() == '{"iri": "wsys:study/s"}\n'
    with pytest.raises(FileExistsError):
        migrate(old, new)
