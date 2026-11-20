"""
hydrocouple.platform manifests -> ontology rows (JSON Lines, one file per table).

Reads what the platform publishes for a run and writes the rows that say where
its results came from:

  analysis.revision, .input_artifact    the packaged revision, from its revision manifest
  analysis.run, .attempt                each attempt, from attempt.json and its provenance
  analysis.result_artifact              every artifact the attempt lists, by checksum
  reference.agent                       the person, the engine, and the AI agent where there is one
  analysis.agent_session, .tool_call    the agent session and the tool call that submitted the run
  analysis.tool                         each tool a journal names, as the server registers it
  analysis.scenario, .scenario_override a swmm.simulation@2 run's revision and field overrides
  analysis.study, .candidate            a study manifest (local or platform) and its evaluations
  analysis.schedule                     a digital twin's schedule; its runs name it and their cycle
  observation.observation (FORECAST)    a twin cycle's forecast (forecast.json beside attempt.json)
  observation.setpoint                  the setpoints proposed for a schedule and who decided them
  observation.observation (MEASUREMENT) telemetry, with its sensors and datastreams

An attempt is a folder holding attempt.json as the platform wrote it. Where the
folder also holds the run summary (summary.json), the file is checked against
the checksum in the manifest and copied to artifacts/<sha256>.json, so a
finding can cite a number in it. Nothing is read from the control plane, and
no other artifact is copied.

A journal is the openswmm.mcp tool-call journal (one JSON line per call). Each
call becomes a ToolCall row with its tool, effect, position and checksums, in
the agent session the line names: the platform's, or for a local session the
journal itself, acting for the operating-system login. The call that submitted
or started a run is the same row its attempt names; an attempt of a local run
(written by openswmm.mcp under <session>/local/) loads like a platform one. A
line with no agent session, from a journal written before local sessions were
named, is refused.

Identifiers are built from what the manifests state, so the same manifests
always give the same rows:

  wsys:revision/<utility>/<system>/<sha256 of the revision manifest>
  wsys:run/<run id>            wsys:attempt/<run id>/<attempt number>
  <attempt>/artifact/<path>    <revision>/file/<path>
  wsys:agent/person/<subject>  wsys:agent/ai/<agent session id>
  wsys:session/<agent session id>            <session>/call/<tool call id>
  wsys:tool/<server>/<server version>/<tool name>
  wsys:scenario/<sha256 of revision and overrides>   <scenario>/override/<n>
  wsys:study/<study id>        <study>/candidate/<evaluation>
  wsys:schedule/<schedule id>  wsys:setpoint/<proposal id>
  <attempt>/forecast/<path>/<element>/<n>    wsys:datastream/<utility>/<system>/<column>
  <study>/event_set            <study>/event/<event name>
  <study>/parameter_set/<sha256 of its acceptance rule, 16 hex digits>

A revision whose manifest has derived_from names its base revision (by its
manifest hash, in the same scope) in was_derived_from, and the study and
evaluation that chose it.

A study's kind and search algorithm, acceptance rule and event set come from
its manifest's definition; each candidate carries its metrics. A parameter set
is a behavioural set (openswmm_gymnasium.optimize.behavioural_set, as JSON)
loaded with the manifest of its study.

An attempt that resumed from an earlier one's saved state names that artifact in resumed_from;
the earlier attempt must be loaded first. A forecast's values and telemetry are normalised to SI
(unit, native unit and factor kept), at the model's UTC wall clock written with Z, so a forecast
and a measurement of the same element, property and time share entity_iri, property_iri and
phenomenon_time. Telemetry is described by a JSON file naming its CSV (beside it) and, per
column, the element and property it measures and its unit.

Usage:
    python -m loaders.platform_to_rows --utility demo --system site-drainage \\
        --revision package/revision-manifest.json --attempt downloads/att_1 \\
        --journal mcp/journal/jr_1.jsonl --study study/study-manifest.json \\
        --parameter-set study/behavioural-set.json study/study-manifest.json \\
        --schedule schedule.json proposals.json --telemetry levels.json -o rows
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

from generators.profile import annotation
from loaders.rows import Rows
from loaders.source_records import file_digest

SOURCE = "hydrocouple.platform"

#: A study's kind from its search algorithm; any other algorithm is a Platypus MOEA.
STUDY_KINDS = {"lhs": "SAMPLING", "sobol": "SAMPLING", "random_search": "SAMPLING",
               "grid_search": "SAMPLING", "alternatives": "ALTERNATIVES"}


def acceptance(rule: dict | None) -> dict | None:
    """An openswmm_gymnasium AcceptanceRule as an AcceptanceRule row value."""
    if rule is None:
        return None
    row = {"thresholds": [{k: t[k] for k in ("metric", "at_least", "at_most") if t.get(k) is not None}
                          for t in rule.get("thresholds") or []] or None,
           "best_fraction": rule.get("best_fraction"), "ranked_by": rule.get("by")}
    return {k: v for k, v in row.items() if v is not None}
SHA256 = "http://spdx.org/rdf/terms#checksumAlgorithm_sha256"
MEDIA_TYPES = {"json": "application/json", "swmm_rpt": "text/plain", "swmm_out": "application/octet-stream",
               "openswmm_restart_hdf5": "application/x-hdf5"}

#: Native units of model values and telemetry -> (QUDT unit, factor to it).
UNITS = {"ft": ("unit:M", 0.3048), "m": ("unit:M", 1.0),
         "CFS": ("unit:M3-PER-SEC", 0.028316846592), "GPM": ("unit:M3-PER-SEC", 6.30901964e-05),
         "MGD": ("unit:M3-PER-SEC", 0.0438126364), "CMS": ("unit:M3-PER-SEC", 1.0),
         "LPS": ("unit:M3-PER-SEC", 0.001), "MLD": ("unit:M3-PER-SEC", 1 / 86.4),
         "in/hr": ("unit:MilliM-PER-HR", 25.4), "mm/hr": ("unit:MilliM-PER-HR", 1.0)}
#: Engine catalog paths a forecast or a datastream may carry -> (property, its kind, asset kind).
PROPERTIES = {"node.depth": ("depth", "DEPTH", "node"), "link.flow": ("flow", "FLOW", "link"),
              "gage.rainfall": ("rainfall_intensity", "RAINFALL_INTENSITY", "gage")}
SENSOR_KINDS = {"DEPTH": "LEVEL_SENSOR", "FLOW": "FLOW_METER", "RAINFALL_INTENSITY": "RAIN_GAGE"}
SETTING = "wsys:prop/setting"


def part(value: str) -> str:
    return quote(str(value), safe="")


def wall_clock(value: str) -> str:
    """A model time (the engine's UTC wall clock, written without a zone) as ISO 8601 in UTC."""
    at = datetime.fromisoformat(value)
    if at.tzinfo is not None:
        raise ValueError(f"{value} names a zone; model times are the UTC wall clock without one")
    return at.isoformat(timespec="seconds") + "Z"


def duration(delta: timedelta) -> str:
    """A non-negative timedelta as an ISO 8601 duration in whole seconds (PT600S)."""
    seconds = round(delta.total_seconds())
    if seconds < 0:
        raise ValueError(f"negative lead time {delta}")
    return f"PT{seconds}S"


class PlatformLoader(Rows):
    def __init__(self, utility: str, system: str):
        super().__init__()
        self.scope = f"{part(utility)}/{part(system)}"
        self.revisions: dict[str, str] = {}    # sha256 of a revision manifest -> its IRI
        self.attempts: list[str] = []
        self.copies: dict[str, Path] = {}      # file name under artifacts/ -> source
        self.seen: set[str] = set()
        self.attempt_iris: dict[str, str] = {}  # platform attempt id -> its IRI
        self.run_attempts: dict[str, str] = {}  # run id -> the IRI of its loaded attempt

    def once(self, cls: str, row: dict) -> str:
        """Add a row unless one with its IRI is there already; return the IRI."""
        if row["iri"] not in self.seen:
            self.seen.add(row["iri"])
            self.add(cls, row | {"source_system": SOURCE})
        return row["iri"]

    def embed(self, cls: str, document: dict) -> dict:
        """A platform JSON document as the generated Platform* class that mirrors its schema."""
        slots = {annotation(s, "json_property"): s for s in self.sv.class_induced_slots(cls)}
        out = {}
        for key, value in document.items():
            slot = slots.get(key)
            if slot is None:
                raise ValueError(f"{cls} has no property '{key}': the vendored platform schemas are older than this manifest")
            if value is None:
                continue
            if slot.range in self.sv.all_classes():
                value = [self.embed(slot.range, v) for v in value] if slot.multivalued else self.embed(slot.range, value)
            elif slot.range in self.sv.all_enums():
                value = str(value).upper().replace("-", "_").replace(" ", "_")
            elif annotation(slot, "json_object") or annotation(slot, "json_schema"):
                value = json.dumps(value, sort_keys=True)
            out[slot.name] = value
        return out

    def add_revision(self, manifest_path: Path) -> str:
        digest = file_digest(manifest_path)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        iri = self.revisions[digest] = f"wsys:revision/{self.scope}/{digest}"
        models = [f["sha256"] for f in manifest["files"] if f["role"] == "model"]
        lineage = manifest.get("derived_from") or {}
        study = lineage.get("study_id")
        self.once("Revision", {"iri": iri, "label": digest[:12], "revision_id": digest, "manifest_sha256": digest,
                               "input_sha256": models[0] if len(models) == 1 else None, "dc_created": manifest["created_at"],
                               "revision_manifest": self.embed("PlatformRevisionManifest", manifest),
                               "was_derived_from": [f"wsys:revision/{self.scope}/{lineage['revision_manifest_sha256']}"] if lineage else None,
                               "derived_in_study": f"wsys:study/{part(study)}" if study else None,
                               "chosen_evaluation": lineage.get("evaluation")})
        for f in manifest["files"]:
            self.once("InputArtifact", {"iri": f"{iri}/file/{part(f['path'])}", "of_revision": iri, "source_path": f["path"],
                                        "file_role": f["role"], "input_sha256": f["sha256"]})
        return iri

    def add_attempt(self, folder: Path) -> str:
        manifest_path = folder / "attempt.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        p, actor = manifest["provenance"], manifest["provenance"]["actor"]
        revision = self.revisions.get(p["revision_manifest_sha256"])
        if revision is None:
            raise ValueError(f"{manifest_path}: ran revision manifest {p['revision_manifest_sha256']}; give that manifest with --revision")
        # The platform's own id for the revision is only known from an attempt that ran it.
        for row in self.rows[self.table("Revision")]:
            if row["iri"] == revision:
                row["revision_id"] = row["label"] = p["revision_id"]

        person = self.once("Agent", {"iri": f"wsys:agent/person/{part(actor['human_subject'])}", "label": actor["human_subject"],
                                     "agent_kind": "PERSON", "external_identity": actor["human_subject"]})
        asked_by, generated_by = person, f"wsys:job/{part(p['job_id'])}"
        if actor.get("agent_session_id"):
            session_id, client = actor["agent_session_id"], actor.get("agent_client")
            version = actor.get("agent_client_version")
            asked_by = self.once("Agent", {"iri": f"wsys:agent/ai/{part(session_id)}", "label": client or session_id, "agent_kind": "AI_AGENT",
                                           "external_identity": f"{client}@{version}" if client and version else client or session_id,
                                           "acts_on_behalf_of": person})
            session = self.once("AgentSession", {"iri": f"wsys:session/{part(session_id)}", "label": session_id, "session_id": session_id,
                                                 "was_associated_with": asked_by, "client": client, "client_version": version,
                                                 "model": actor.get("model")})
            if actor.get("tool_call_id"):
                generated_by = self.once("ToolCall", {"iri": f"{session}/call/{part(actor['tool_call_id'])}", "label": actor["tool_call_id"],
                                                      "tool_call_id": actor["tool_call_id"], "in_session": session,
                                                      "was_associated_with": asked_by})
        engine = p["runtime"].get("engine")
        name, version = ("openswmm", engine) if engine else (SOURCE, p["runtime"]["platform"])
        software = self.once("Agent", {"iri": f"wsys:agent/software/{name}/{part(version)}", "label": f"{name} {version}",
                                       "agent_kind": "SOFTWARE", "external_identity": f"{name}=={version}"})

        scenario = self.add_scenario(revision, p, manifest.get("quality") or {})
        cycle_end = p["parameters"].get("cycle_end") if p["workflow"] == "twin.cycle" else None
        run = self.once("Run", {"iri": f"wsys:run/{part(p['run_id'])}", "label": p["run_id"], "run_id": p["run_id"], "of_revision": revision,
                                "of_scenario": scenario, "workflow": f"{p['workflow']}@{p['workflow_version']}", "was_generated_by": generated_by,
                                "was_attributed_to": asked_by, "generated_at": p["started_at"],
                                "session_id": actor.get("agent_session_id"), "tool_call_id": actor.get("tool_call_id"),
                                "cycle_end": wall_clock(cycle_end) if cycle_end else None})
        resumed = p.get("resumed_from")
        if resumed and resumed["attempt_id"] not in self.attempt_iris:
            raise ValueError(f"{manifest_path}: resumed from attempt {resumed['attempt_id']} of run {resumed['run_id']}; "
                             "load that attempt first")
        attempt = self.once("Attempt", {"iri": f"{run.replace('wsys:run/', 'wsys:attempt/')}/{p['attempt_number']}", "label": p["attempt_id"],
                                        "attempt_id": p["attempt_id"], "of_run": run, "manifest_sha256": file_digest(manifest_path),
                                        "attempt_manifest": self.embed("PlatformAttemptManifest", manifest),
                                        "was_associated_with": software, "used": [revision], "started_at": p["started_at"],
                                        "ended_at": p["finished_at"], "generated_at": p["finished_at"],
                                        "dc_conforms_to": [f"openswmm=={engine}"] if engine else None,
                                        "resumed_from": f"{self.attempt_iris[resumed['attempt_id']]}/artifact/{part(resumed['path'])}"
                                        if resumed else None})
        self.attempt_iris[p["attempt_id"]] = attempt
        self.run_attempts[p["run_id"]] = attempt
        for a in manifest["artifacts"]:
            where, source = a["path"], folder / a["path"]
            if a["kind"] == "summary" and source.is_file():
                if file_digest(source) != a["sha256"]:
                    raise ValueError(f"{source} is not the file the attempt manifest lists (checksum differs)")
                where = f"artifacts/{a['sha256']}.json"
                self.copies[Path(where).name] = source
            self.once("ResultArtifact", {
                "iri": f"{attempt}/artifact/{part(a['path'])}", "label": a["path"], "artifact_sha256": a["sha256"],
                "was_generated_by": attempt, "was_derived_from": [revision], "generated_at": p["finished_at"],
                "distributions": [{"access_url": where, "byte_size": a["size"], "media_type": MEDIA_TYPES.get(a["format"]),
                                   "integrity_checksum": {"algorithm": SHA256, "digest": a["sha256"]}}]})
            if a["kind"] == "forecast" and source.is_file():
                if file_digest(source) != a["sha256"]:
                    raise ValueError(f"{source} is not the file the attempt manifest lists (checksum differs)")
                self.add_forecast(attempt, json.loads(source.read_text(encoding="utf-8")), p["finished_at"])
        self.attempts.append(attempt)
        return attempt

    def asset(self, kind: str, element: str) -> str:
        """The asset IRI loaders.swmm_to_rows gives a model element of this system."""
        return f"wsys:asset/{self.scope}/{kind}/{part(element)}"

    def observed(self, path: str, unit: str | None) -> tuple[str, str, str, str, float]:
        """(asset kind, property IRI, native unit, QUDT unit, factor) for a catalog path's value."""
        if path not in PROPERTIES:
            raise ValueError(f"no observed property for {path}; known: {', '.join(PROPERTIES)}")
        if unit not in UNITS:
            raise ValueError(f"{path} is in {unit!r}, which has no SI conversion; known: {', '.join(UNITS)}")
        name, kind, asset_kind = PROPERTIES[path]
        qudt, factor = UNITS[unit]
        prop = self.once("ObservedProperty", {"iri": f"wsys:prop/{name}", "label": name, "property_kind": kind,
                                              "default_unit": qudt})
        return asset_kind, prop, unit, qudt, factor

    def add_forecast(self, attempt: str, forecast: dict, generated: str) -> None:
        """A twin cycle's forecast (forecast.json) as FORECAST observations of the attempt."""
        issued = datetime.fromisoformat(forecast["issued_at"])
        for s in forecast["series"]:
            asset_kind, prop, native, qudt, factor = self.observed(s["path"], s.get("unit"))
            entity = self.asset(asset_kind, s["element_id"])
            for n, (when, value) in enumerate(zip(s["times"], s["values"], strict=True)):
                at = datetime.fromisoformat(when)
                self.add("Observation", {
                    "iri": f"{attempt}/forecast/{part(s['path'])}/{part(s['element_id'])}/{n}", "entity_iri": entity,
                    "property_iri": prop, "phenomenon_time": wall_clock(when), "result_time": wall_clock(forecast["issued_at"]),
                    "issued_at": wall_clock(forecast["issued_at"]), "horizon": duration(at - issued),
                    "value": float(value) * factor, "unit": qudt, "native_unit": native, "conversion_factor": factor,
                    "quality_flag": "GOOD", "result_kind": "FORECAST", "was_generated_by": attempt,
                    "generated_at": generated, "source_system": SOURCE})

    def add_schedule(self, view_path: Path, proposals_path: Path | None = None) -> str:
        """A schedule view as a Schedule, its loaded runs named as its cycles, and each setpoint
        proposal (the proposals listing) as a Setpoint with who proposed and who decided it."""
        view = json.loads(view_path.read_text(encoding="utf-8"))
        revision = next((iri for iri in self.revisions.values()
                         if any(r["iri"] == iri and r.get("revision_id") == view["revision_id"]
                                for r in self.rows[self.table("Revision")])), None)
        iri = self.once("Schedule", {
            "iri": f"wsys:schedule/{part(view['schedule_id'])}", "label": view["schedule_id"],
            "schedule_id": view["schedule_id"], "of_revision": revision,
            "workflow": f"{view['workflow']}@{view['workflow_version']}", "schedule_state": view["state"],
            "schedule_pace": view["pace"].upper(), "cycle_minutes": float(view["cycle_minutes"]),
            "schedule_view": self.embed("PlatformScheduleView", view), "dc_created": view["created_at"]})
        runs = {r["iri"]: r for r in self.rows[self.table("Run")]}
        for c in view["history"]:
            run = runs.get(f"wsys:run/{part(c['run_id'])}")
            if run is not None:
                run["in_schedule"], run["cycle_number"] = iri, c["number"]
        if proposals_path is not None:
            for p in json.loads(proposals_path.read_text(encoding="utf-8")):
                self.add_setpoint(iri, p)
        return iri

    def add_setpoint(self, schedule: str, p: dict) -> str:
        by = p["proposed_by"]
        if by["kind"] == "forecast":
            proposer = self.once("Agent", {"iri": "wsys:agent/software/hydrocouple.platform/twin.cycle", "label": "twin.cycle control policy",
                                           "agent_kind": "SOFTWARE", "external_identity": "hydrocouple.platform twin.cycle@1"})
            generated_by = self.run_attempts.get(p["run_id"])
            if generated_by is None:
                raise ValueError(f"setpoint {p['proposal_id']} was proposed by run {p['run_id']}; load its attempt first")
        else:
            person = self.once("Agent", {"iri": f"wsys:agent/person/{part(by['human_subject'])}", "label": by["human_subject"],
                                         "agent_kind": "PERSON", "external_identity": by["human_subject"]})
            proposer, generated_by = person, f"wsys:proposal/{part(p['proposal_id'])}"
            if by["kind"] == "agent":
                proposer = self.once("Agent", {"iri": f"wsys:agent/ai/{part(by['agent_session_id'])}", "label": by["agent_session_id"],
                                               "agent_kind": "AI_AGENT", "external_identity": by["agent_session_id"],
                                               "acts_on_behalf_of": person})
                session = f"wsys:session/{part(by['agent_session_id'])}"
                generated_by = f"{session}/call/{part(by['tool_call_id'])}" if by.get("tool_call_id") else session
        decider = None
        if p.get("decided_by"):
            decider = self.once("Agent", {"iri": f"wsys:agent/person/{part(p['decided_by'])}", "label": p["decided_by"],
                                          "agent_kind": "PERSON", "external_identity": p["decided_by"]})
        applied = p.get("applied_run_id")
        if applied and applied not in self.run_attempts:
            raise ValueError(f"setpoint {p['proposal_id']} was applied by run {applied}; load its attempt first")
        self.once("ObservedProperty", {"iri": SETTING, "label": "setting", "property_kind": "STATE", "default_unit": "unit:UNITLESS"})
        return self.once("Setpoint", {
            "iri": f"wsys:setpoint/{part(p['proposal_id'])}", "label": f"{p['link']} {p['value']:g}", "proposal_id": p["proposal_id"],
            "for_schedule": schedule, "target_entity": self.asset("link", p["link"]), "target_property": SETTING,
            "value": float(p["value"]), "unit": "unit:UNITLESS", "effective_from": wall_clock(p["effective_from"]),
            "set_by": proposer, "setpoint_state": p["state"], "decided_by": decider,
            "approved_by": decider if p["state"] in ("APPROVED", "APPLIED") else None,
            "decided_at": p.get("decided_at"), "applied_in": self.run_attempts.get(applied) if applied else None,
            "applied_at": wall_clock(p["applied_at"]) if p.get("applied_at") else None,
            "was_generated_by": generated_by, "generated_at": p["created_at"], "description": p.get("note")})

    def add_telemetry(self, path: Path) -> str:
        """Telemetry (a JSON description and the CSV beside it) as MEASUREMENT observations, one
        datastream and one sensor per column. Each sensor is ATTACHED_TO the asset it measures and
        PART_OF_SYSTEM this system, so the graph needs that system's assets too (load the model
        with loaders.swmm_to_rows for the same utility and system)."""
        desc = json.loads(path.read_text(encoding="utf-8"))
        data = path.parent / desc["file"]
        digest = file_digest(data)
        activity = self.once("IngestionActivity", {"iri": f"wsys:ingestion/{self.scope}/telemetry/{digest}",
                                                   "label": f"telemetry {data.name}", "started_at": desc.get("loaded_at")})
        with data.open(newline="", encoding="utf-8") as stream:
            rows = list(csv.DictReader(stream))
        time_column = desc.get("time_column", "time")
        for s in desc["streams"]:
            asset_kind, prop, native, qudt, factor = self.observed(s["path"], s["unit"])
            entity = self.asset(asset_kind, s["id"])
            kind = PROPERTIES[s["path"]][1]
            sensor_iri = f"wsys:sensor/{self.scope}/{part(s['column'])}"
            if sensor_iri not in self.seen:
                since = wall_clock(rows[0][time_column]) if rows else None
                for relation, target in (("ATTACHED_TO", entity), ("PART_OF_SYSTEM", f"wsys:system/{self.scope}")):
                    self.rows["topology.edge"].append({"from_iri": sensor_iri, "to_iri": target, "relation": relation,
                                                       "valid_from": since, "source_system": SOURCE,
                                                       "ingestion_activity": activity})
            sensor = self.once("Sensor", {"iri": sensor_iri, "label": s["column"],
                                          "sensor_kind": desc.get("sensor_kind", SENSOR_KINDS[kind]),
                                          "lifecycle_status": "IN_SERVICE", "datasource_tag": s["column"]})
            stream_iri = self.once("Datastream", {"iri": f"wsys:datastream/{self.scope}/{part(s['column'])}", "label": s["column"],
                                                  "sensor": sensor, "observed_property": prop, "feature_of_interest": entity,
                                                  "datasource_tag": s["column"]})
            for n, r in enumerate(rows):
                if not (r.get(s["column"]) or "").strip():
                    continue
                self.add("Observation", {
                    "iri": f"{stream_iri}/{n}", "datastream": stream_iri, "entity_iri": entity, "property_iri": prop,
                    "phenomenon_time": wall_clock(r[time_column]), "result_time": wall_clock(r[time_column]),
                    "value": float(r[s["column"]]) * factor, "unit": qudt, "native_unit": native, "conversion_factor": factor,
                    "quality_flag": "GOOD", "result_kind": "MEASUREMENT", "was_generated_by": activity,
                    "source_system": desc.get("source", "telemetry")})
        return activity

    def add_scenario(self, revision: str, provenance: dict, quality: dict) -> str | None:
        """The scenario a run with overrides executes, or None for a run of the revision as it is."""
        requested = provenance["parameters"].get("overrides") or []
        if not requested:
            return None
        applied = quality.get("overrides") or requested  # the runner adds base values and units
        key = json.dumps([revision, [[o["path"], o["id"], o["value"]] for o in requested]], sort_keys=True)
        iri = f"wsys:scenario/{hashlib.sha256(key.encode()).hexdigest()}"
        if iri in self.seen:
            return iri
        self.once("Scenario", {"iri": iri, "label": f"{len(requested)} override(s)", "of_revision": revision})
        for n, o in enumerate(applied, start=1):
            base = o.get("base_value")
            self.once("ScenarioOverride", {
                "iri": f"{iri}/override/{n}", "label": f"{o['path']} on {o['id']}", "of_scenario": iri,
                "catalog_path": o["path"], "override_element_id": o["id"], "override_value": float(o["value"]),
                "base_value": None if base is None else float(base), "override_unit": o.get("unit")})
        return iri

    def add_study(self, path: Path) -> str:
        """A study manifest, local or platform, as a Study, one Candidate per evaluation and,
        when its env config scores events, an EventSet of ForcingEvents."""
        manifest = json.loads(path.read_text(encoding="utf-8"))
        definition, runtime = manifest["definition"], manifest["runtime"]
        comparable = json.dumps({k: v for k, v in manifest.items() if k != "runtime"}, sort_keys=True)
        study_id = runtime.get("study_id") or f"local_{hashlib.sha256(comparable.encode()).hexdigest()[:20]}"
        iri = f"wsys:study/{part(study_id)}"
        job = runtime.get("run_id") or study_id
        revision = self.revisions.get(runtime.get("revision_manifest_sha256"))
        optimization = definition["optimization"]
        algorithm = optimization["algorithm"]
        self.once("Study", {"iri": iri, "label": study_id, "study_id": study_id, "of_revision": revision,
                            "workflow": f"{definition['workflow']}@{definition['workflow_version']}",
                            "was_generated_by": f"wsys:job/{part(job)}",
                            "study_manifest": self.embed("PlatformStudyManifest", manifest),
                            "study_kind": STUDY_KINDS.get(algorithm, "OPTIMIZATION"), "search_algorithm": algorithm,
                            "acceptance_rule": acceptance(optimization.get("acceptance")),
                            "scored_over": self.add_event_set(iri, definition["env_config"], revision)})
        directions = {o["name"]: o["direction"] for o in definition["objectives"]}
        pareto = {p["evaluation"] for p in manifest["result"].get("pareto") or []}
        for e in manifest["evaluations"]:
            # decisions[key] lists the key's components in dimension order; JSON keeps no other.
            values = {key: iter(v) for key, v in e["decisions"].items()}
            flat = [(d["key"], d["label"], next(values[d["key"]])) for d in definition["dimensions"]]
            self.once("Candidate", {
                "iri": f"{iri}/candidate/{e['evaluation']}", "label": f"{study_id} #{e['evaluation']}", "of_study": iri,
                "candidate_index": e["evaluation"], "was_generated_by": iri, "is_pareto": e["evaluation"] in pareto,
                "decision_values": [{"dimension": key, "label": label, "value": float(v)} for key, label, v in flat],
                "objective_values": [{"name": n, "direction": directions[n], "value": float(v)}
                                     for n, v in e["objectives"].items()],
                "metric_values": [{"name": n} if v is None else {"name": n, "value": float(v)}
                                  for n, v in sorted((e.get("metrics") or {}).items())] or None})
        return iri

    def add_event_set(self, study: str, env_config: dict, revision: str | None) -> str | None:
        """The events a study's env config scores each candidate on, as an EventSet; None without events.
        An event's rain series is its revision's input artifact when the revision is loaded."""
        events = env_config.get("events") or []
        if not events:
            return None
        model_dir = env_config["inp_path"].rpartition("/")[0]
        iris = []
        for e in events:
            series = f"{model_dir}/{e['series']}" if model_dir else e["series"]
            iris.append(self.once("ForcingEvent", {
                "iri": f"{study}/event/{part(e['name'])}", "label": e["name"],
                "return_period_years": e.get("return_period"), "event_weight": e.get("weight", 1.0),
                "started_at": e["start"], "ended_at": e["end"],
                "used": [f"{revision}/file/{part(series)}"] if revision else None}))
        return self.once("EventSet", {"iri": f"{study}/event_set", "label": f"events of {study}", "events": iris,
                                      "event_aggregation": env_config.get("event_aggregation", "mean").upper()})

    def add_parameter_set(self, path: Path, study_manifest: Path) -> str:
        """A behavioural set (openswmm_gymnasium.optimize.behavioural_set) of the study in study_manifest."""
        accepted = json.loads(path.read_text(encoding="utf-8"))
        study = self.add_study(study_manifest)
        rule = json.dumps(accepted["rule"], sort_keys=True)
        return self.once("ParameterSet", {
            "iri": f"{study}/parameter_set/{hashlib.sha256(rule.encode()).hexdigest()[:16]}",
            "label": f"behavioural set of {study}", "of_study": study, "was_derived_from": [study],
            "was_generated_by": study,
            "members": [f"{study}/candidate/{m['evaluation']}" for m in accepted["members"]],
            "acceptance_rule": acceptance(accepted["rule"])})

    def merge(self, cls: str, row: dict) -> str:
        """Add a row, or fill the fields an earlier row with its IRI left empty."""
        if row["iri"] not in self.seen:
            return self.once(cls, row)
        existing = next(r for r in self.rows[self.table(cls)] if r["iri"] == row["iri"])
        for key, value in row.items():
            if value is not None:
                existing.setdefault(key, value)
        return row["iri"]

    def add_journal(self, path: Path) -> list[str]:
        """The tool calls of an openswmm.mcp journal as ToolCall rows, with the tools they call."""
        calls = []
        for number, text in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            c = json.loads(text)
            session_id = c.get("agent_session_id")
            if not session_id:
                raise ValueError(f"{path}:{number}: the call names no agent session; a journal "
                                 "written before local sessions were named cannot be loaded")
            person = self.once("Agent", {"iri": f"wsys:agent/person/{part(c['acting_for'])}", "label": c["acting_for"],
                                         "agent_kind": "PERSON", "external_identity": c["acting_for"]})
            ai = self.once("Agent", {"iri": f"wsys:agent/ai/{part(session_id)}", "label": c["server"], "agent_kind": "AI_AGENT",
                                     "external_identity": f"{c['server']}@{c['server_version']}", "acts_on_behalf_of": person})
            session = self.once("AgentSession", {"iri": f"wsys:session/{part(session_id)}", "label": session_id,
                                                 "session_id": session_id, "was_associated_with": ai})
            tool = self.once("Tool", {"iri": f"wsys:tool/{part(c['server'])}/{part(c['server_version'])}/{part(c['tool'])}",
                                      "label": c["tool"], "tool_name": c["tool"], "tool_version": c["server_version"],
                                      "read_only": c["read_only"]})
            calls.append(self.merge("ToolCall", {
                "iri": f"{session}/call/{part(c['tool_call_id'])}", "label": c["tool_call_id"], "tool_call_id": c["tool_call_id"],
                "in_session": session, "tool": tool, "effect": c["effect"].upper(), "sequence": c["sequence"],
                "arguments_sha256": c["arguments_sha256"], "result_sha256": c["result_sha256"],
                "was_associated_with": ai, "started_at": c["started_at"], "ended_at": c["ended_at"]}))
        return calls

    def write(self, out: Path) -> None:
        out = Path(out)
        if out.exists() and any(out.glob("*.jsonl")):
            raise FileExistsError(f"{out} already holds rows; write to a new folder")
        (out / "artifacts").mkdir(parents=True, exist_ok=True)
        for name, source in self.copies.items():
            shutil.copyfile(source, out / "artifacts" / name)
        summary = {"source": SOURCE, "system": f"wsys:system/{self.scope}", "revisions": sorted(self.revisions.values()),
                   "attempts": self.attempts, "row_sha256": self.write_tables(out), "artifacts": sorted(self.copies),
                   "rows": {t: len(r) for t, r in sorted(self.rows.items())}}
        (out / "SUMMARY.json").write_text(json.dumps(summary, indent=2) + "\n")
        print(json.dumps({"tables": len(self.rows), "rows": sum(summary["rows"].values()), "artifacts": len(self.copies)}))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--utility", default="demo")
    ap.add_argument("--system", default="system")
    ap.add_argument("--revision", type=Path, action="append", required=True, help="a revision manifest; repeat for each revision")
    ap.add_argument("--attempt", type=Path, action="append", default=[], help="a folder holding attempt.json; repeat for each attempt")
    ap.add_argument("--journal", type=Path, action="append", default=[], help="an openswmm.mcp tool-call journal; repeat for each")
    ap.add_argument("--study", type=Path, action="append", default=[], help="a study-manifest.json, local or platform; repeat for each")
    ap.add_argument("--parameter-set", type=Path, nargs=2, action="append", default=[],
                    metavar=("SET", "STUDY_MANIFEST"), help="a behavioural set and its study's manifest; repeat for each")
    ap.add_argument("--schedule", type=Path, nargs=2, action="append", default=[], metavar=("VIEW", "PROPOSALS"),
                    help="a schedule view and its proposals listing; repeat for each")
    ap.add_argument("--telemetry", type=Path, action="append", default=[], help="a telemetry description; repeat for each")
    ap.add_argument("-o", "--out", type=Path, default=Path("rows"))
    a = ap.parse_args()
    ld = PlatformLoader(a.utility, a.system)
    for path in a.revision:
        ld.add_revision(path)
    for folder in a.attempt:
        ld.add_attempt(folder)
    for path in a.journal:
        ld.add_journal(path)
    for path in a.study:
        ld.add_study(path)
    for path, study in a.parameter_set:
        ld.add_parameter_set(path, study)
    for view, proposals in a.schedule:
        ld.add_schedule(view, proposals)
    for path in a.telemetry:
        ld.add_telemetry(path)
    ld.write(a.out)


if __name__ == "__main__":
    main()
