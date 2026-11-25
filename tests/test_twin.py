"""Phase 4 vocabulary: schedules, resumed attempts, forecasts beside measurements, setpoint decisions.

A real hydrocouple.platform schedule of four 40-minute twin.cycle cycles on the twin-tank model
(its tests/integration/test_twin_cycles.py, the approved-proposals test): each cycle resumed from
the state.h5 the cycle before it saved and forecast the first tank's depth an hour ahead; the
control curve proposed orifice settings, an agent proposed one more, and a person decided each.
The telemetry is the rain the cycles were fed and the tank's depth from a truth run with noise;
model_rows/ are the revision's model as loaders.swmm_to_rows writes it (its assets, which the
sensors are attached to, and the system)
(hydrocouple.platform test_artifacts/_phase4/ontology_fixture/make_twin_fixture.py). Files are
kept under dist/test/twin.
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
FIX = ROOT / "tests" / "fixtures" / "platform" / "twin"
OUT = ROOT / "dist" / "test" / "twin"
CYCLES = sorted((FIX / "attempts").iterdir(), key=lambda p: int(p.name))
VIEW = json.loads((FIX / "schedule.json").read_text())
PROPOSALS = json.loads((FIX / "proposals.json").read_text())


def folder(name: str) -> Path:
    path = OUT / name
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True)
    return path


def load(out: Path) -> PlatformLoader:
    loader = PlatformLoader("demo", "twin-tanks")
    loader.add_revision(FIX / "revision-manifest.json")
    for cycle in CYCLES:
        loader.add_attempt(cycle)
    loader.add_schedule(FIX / "schedule.json", FIX / "proposals.json")
    for description in ("rain.json", "levels.json"):
        loader.add_telemetry(FIX / "telemetry" / description)
    loader.write(out / "rows")
    return loader


def rules(rows: Path) -> set[str]:
    h = Harness()
    h.load(FIX / "model_rows")
    h.load(rows)
    return {rid for rid, _, _, _ in h.violations()}


def table(rows: Path, name: str) -> list[dict]:
    return findings.read_rows(rows / f"{name}.jsonl")


def test_each_cycle_resumes_from_the_state_the_cycle_before_saved():
    out = folder("cycles")
    loader = load(out)
    attempts = {a["iri"]: a for a in table(out / "rows", "analysis.attempt")}
    assert list(attempts) == loader.attempts and len(attempts) == len(VIEW["history"]) == 4
    previous = None
    for iri in loader.attempts:
        expected = f"{previous}/artifact/state.h5" if previous else None
        assert attempts[iri].get("resumed_from") == expected
        previous = iri
    (schedule,) = table(out / "rows", "analysis.schedule")
    assert (schedule["schedule_state"], schedule["schedule_pace"], schedule["cycle_minutes"]) == (
        "COMPLETED", "REPLAY", 40.0)
    runs = {r["run_id"]: r for r in table(out / "rows", "analysis.run")}
    for c in VIEW["history"]:
        run = runs[c["run_id"]]
        assert (run["in_schedule"], run["cycle_number"]) == (schedule["iri"], c["number"])
        assert run["cycle_end"] == c["cycle_end"] + "Z"
    assert rules(out / "rows") == set()


def test_forecasts_sit_beside_measurements_of_the_same_element_property_and_time():
    out = folder("forecasts")
    load(out)
    rows = table(out / "rows", "observation.observation")
    forecasts = [o for o in rows if o["result_kind"] == "FORECAST"]
    measured = {(o["entity_iri"], o["property_iri"], o["phenomenon_time"]): o
                for o in rows if o["result_kind"] == "MEASUREMENT"}
    assert len(forecasts) == 4 * 6  # each cycle: an hour ahead, every 10 minutes
    for f in forecasts:
        assert f["entity_iri"] == "wsys:asset/demo/twin-tanks/node/T1"
        assert (f["property_iri"], f["unit"], f["native_unit"]) == ("wsys:prop/depth", "unit:M", "ft")
        assert f["horizon"] in {f"PT{600 * k}S" for k in range(1, 7)}
        m = measured[(f["entity_iri"], f["property_iri"], f["phenomenon_time"])]
        assert m["unit"] == f["unit"] and m["datastream"].endswith("/T1")
    issued = sorted({f["issued_at"] for f in forecasts})
    assert issued == [c["cycle_end"] + "Z" for c in VIEW["history"]]
    rain = [o for o in measured.values() if o["property_iri"] == "wsys:prop/rainfall_intensity"]
    assert rain and all(o["unit"] == "unit:MilliM-PER-HR" for o in rain)


def test_setpoints_record_who_proposed_and_who_decided_them():
    out = folder("setpoints")
    loader = load(out)
    setpoints = {s["proposal_id"]: s for s in table(out / "rows", "observation.setpoint")}
    assert len(setpoints) == len(PROPOSALS)
    attempts = dict(zip([c["run_id"] for c in VIEW["history"]], loader.attempts, strict=True))
    for p in PROPOSALS:
        s = setpoints[p["proposal_id"]]
        assert s["setpoint_state"] == p["state"]
        assert s["target_entity"] == "wsys:asset/demo/twin-tanks/link/ORIF"
        if p["state"] in ("APPROVED", "APPLIED", "REJECTED"):
            assert s["decided_by"] == "wsys:agent/person/alice"
        assert s.get("approved_by") == ("wsys:agent/person/alice"
                                        if p["state"] in ("APPROVED", "APPLIED") else None)
        assert s.get("applied_in") == (attempts[p["applied_run_id"]] if p["applied_run_id"] else None)
        if p["proposed_by"]["kind"] == "forecast":
            assert s["was_generated_by"] == attempts[p["run_id"]]
    (agent,) = [s for s in setpoints.values() if s["set_by"].startswith("wsys:agent/ai/")]
    assert agent["setpoint_state"] == "APPLIED" and agent["approved_by"] == "wsys:agent/person/alice"


def test_the_rules_refuse_an_ai_decision_an_unapproved_change_and_a_forecast_without_horizon():
    out = folder("violations")
    load(out)
    rows = out / "rows"
    setpoints = table(rows, "observation.setpoint")
    agents = {a["iri"]: a for a in table(rows, "reference.agent")}
    ai = next(iri for iri, a in agents.items() if a["agent_kind"] == "AI_AGENT")
    applied = next(s for s in setpoints if s["setpoint_state"] == "APPLIED")
    rejected = next(s for s in setpoints if s["setpoint_state"] == "REJECTED")
    applied["approved_by"] = applied["decided_by"] = ai  # an AI agent approved it
    rejected["applied_in"] = applied["applied_in"]  # applied though a person rejected it
    write(rows, "observation.setpoint", setpoints)
    attempts = table(rows, "analysis.attempt")
    attempts[1]["resumed_from"] = attempts[1]["iri"] + "/artifact/state.h5"  # its own output
    write(rows, "analysis.attempt", attempts)
    observations = table(rows, "observation.observation")
    next(o for o in observations if o["result_kind"] == "FORECAST").pop("horizon")
    write(rows, "observation.observation", observations)
    assert rules(rows) == {"setpoint_decided_by_a_person", "setpoint_applied_after_approval",
                           "attempt_resumed_from_a_saved_state", "forecast_issued_with_a_horizon"}


def write(rows: Path, name: str, records: list[dict]) -> None:
    with (rows / f"{name}.jsonl").open("w", encoding="utf-8") as stream:
        for r in records:
            stream.write(json.dumps(r, sort_keys=True) + "\n")


def test_the_loader_needs_the_attempt_a_cycle_resumed_from_and_known_units():
    out = folder("refusals")
    loader = PlatformLoader("demo", "twin-tanks")
    loader.add_revision(FIX / "revision-manifest.json")
    with pytest.raises(ValueError, match="load that attempt first"):
        loader.add_attempt(CYCLES[1])
    description = json.loads((FIX / "telemetry" / "levels.json").read_text())
    description["streams"][0]["unit"] = "furlong"
    shutil.copy(FIX / "telemetry" / description["file"], out / description["file"])
    (out / "levels.json").write_text(json.dumps(description))
    with pytest.raises(ValueError, match="no SI conversion"):
        loader.add_telemetry(out / "levels.json")
