"""Phase 3 vocabulary: study kinds and acceptance rules, candidate metrics, parameter sets, event sets.

Two real study manifests. calibration/ is a local Latin hypercube over a subcatchment width of
openswmm.gymnasium's site drainage example, scored against observations a reference run wrote,
with its acceptance rule and the behavioural set openswmm_gymnasium.optimize.behavioural_set
returned (hydrocouple.platform test_artifacts/_phase3/ontology_fixture/make_calibration_fixture.py).
events/ is hydrocouple.platform's event study on the twin-tank model: three storms per candidate on
the platform, their rain uploaded as sidecars of the revision whose manifest is beside it. Files are
kept under dist/test/experiments.
"""
import json
import shutil
from pathlib import Path

import pytest

pytest.importorskip("duckdb")
from harness.duckdb_harness import Harness  # noqa: E402
from loaders import findings  # noqa: E402
from loaders.platform_to_rows import PlatformLoader  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures" / "platform"
OUT = ROOT / "dist" / "test" / "experiments"
CALIBRATION = FIX / "calibration" / "study-manifest.json"
BEHAVIOURAL = FIX / "calibration" / "behavioural-set.json"
EVENTS = FIX / "events" / "study-manifest.json"


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


def test_a_calibration_is_a_sampling_study_with_its_rule_and_metrics():
    out = folder("calibration")
    loader = PlatformLoader("demo", "site-drainage")
    study = loader.add_study(CALIBRATION)
    loader.write(out / "rows")
    (row,) = table(out / "rows", "analysis.study")
    assert row["iri"] == study and row["study_id"].startswith("local_")
    assert (row["study_kind"], row["search_algorithm"]) == ("SAMPLING", "lhs")
    assert row["acceptance_rule"] == {"thresholds": [{"metric": "nse_C11", "at_least": 0.99},
                                                     {"metric": "rmse_C11", "at_most": 0.2}]}
    assert row.get("scored_over") is None  # the model's own rain
    candidates = sorted(table(out / "rows", "analysis.candidate"), key=lambda c: c["candidate_index"])
    assert len(candidates) == 12
    names = [m["name"] for m in candidates[0]["metric_values"]]
    assert names == ["kge_C11", "nse_C11", "rmse_C11"]
    assert [o["name"] for o in candidates[0]["objective_values"]] == ["nse_C11"]
    assert rules(out / "rows") == set()


def test_a_behavioural_set_is_a_parameter_set_of_its_study():
    out = folder("parameter_set")
    loader = PlatformLoader("demo", "site-drainage")
    parameter_set = loader.add_parameter_set(BEHAVIOURAL, CALIBRATION)
    loader.write(out / "rows")
    (row,) = table(out / "rows", "analysis.parameter_set")
    (study,) = table(out / "rows", "analysis.study")
    accepted = [m["evaluation"] for m in json.loads(BEHAVIOURAL.read_text())["members"]]
    assert row["iri"] == parameter_set and row["of_study"] == study["iri"]
    assert row["was_derived_from"] == [study["iri"]] and row["was_generated_by"] == study["iri"]
    assert row["members"] == [f"{study['iri']}/candidate/{n}" for n in accepted]
    assert row["acceptance_rule"] == study["acceptance_rule"]
    assert rules(out / "rows") == set()

    stray = folder("stray_member")
    bad = json.loads(BEHAVIOURAL.read_text())
    bad["members"].append({"evaluation": 99})  # not an evaluation of the study
    (stray / "behavioural-set.json").write_text(json.dumps(bad))
    loader = PlatformLoader("demo", "site-drainage")
    loader.add_parameter_set(stray / "behavioural-set.json", CALIBRATION)
    loader.write(stray / "rows")
    assert rules(stray / "rows") == {"parameter_set_members_in_study"}


def test_an_event_study_scores_its_candidates_over_an_event_set():
    out = folder("events")
    loader = PlatformLoader("demo", "twin-tank")
    revision = loader.add_revision(FIX / "events" / "revision-manifest.json")
    study = loader.add_study(EVENTS)
    loader.write(out / "rows")
    (row,) = table(out / "rows", "analysis.study")
    assert (row["study_kind"], row["search_algorithm"], row["of_revision"]) == ("SAMPLING", "random_search", revision)
    (event_set,) = table(out / "rows", "analysis.event_set")
    assert row["scored_over"] == event_set["iri"] == f"{study}/event_set"
    assert event_set["event_aggregation"] == "WEIGHTED_MEAN"
    events = {e["label"]: e for e in table(out / "rows", "analysis.forcing_event")}
    assert event_set["events"] == [events[n]["iri"] for n in ("short", "long", "burst")]
    short = events["short"]
    assert (short["event_weight"], short["started_at"], short["ended_at"]) == (
        0.6, "2024-01-02T00:00:00", "2024-01-02T03:00:00")
    inputs = {a["iri"] for a in table(out / "rows", "analysis.input_artifact")}
    assert short["used"] == [f"{revision}/file/model%2Frain%2Fshort.csv"] and set(short["used"]) <= inputs
    metrics = {m["name"] for m in table(out / "rows", "analysis.candidate")[0]["metric_values"]}
    assert {"flooding_volume@short", "peak_outflow@burst", "flooding_volume"} <= metrics
    assert rules(out / "rows") == set()

    rows = out / "rows"
    broken = folder("missing_event") / "rows"
    shutil.copytree(rows, broken)
    kept = [e for e in table(rows, "analysis.forcing_event") if e["label"] != "long"]
    (broken / "analysis.forcing_event.jsonl").write_text("".join(json.dumps(e) + "\n" for e in kept))
    assert rules(broken) == {"event_set_events_exist"}
