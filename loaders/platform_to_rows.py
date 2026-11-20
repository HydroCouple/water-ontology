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

A revision whose manifest has derived_from names its base revision (by its
manifest hash, in the same scope) in was_derived_from, and the study and
evaluation that chose it.

Usage:
    python -m loaders.platform_to_rows --utility demo --system site-drainage \\
        --revision package/revision-manifest.json --attempt downloads/att_1 \\
        --journal mcp/journal/jr_1.jsonl --study study/study-manifest.json -o rows
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path
from urllib.parse import quote

from generators.profile import annotation
from loaders.rows import Rows
from loaders.source_records import file_digest

SOURCE = "hydrocouple.platform"
SHA256 = "http://spdx.org/rdf/terms#checksumAlgorithm_sha256"
MEDIA_TYPES = {"json": "application/json", "swmm_rpt": "text/plain", "swmm_out": "application/octet-stream"}


def part(value: str) -> str:
    return quote(str(value), safe="")


class PlatformLoader(Rows):
    def __init__(self, utility: str, system: str):
        super().__init__()
        self.scope = f"{part(utility)}/{part(system)}"
        self.revisions: dict[str, str] = {}    # sha256 of a revision manifest -> its IRI
        self.attempts: list[str] = []
        self.copies: dict[str, Path] = {}      # file name under artifacts/ -> source
        self.seen: set[str] = set()

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
        run = self.once("Run", {"iri": f"wsys:run/{part(p['run_id'])}", "label": p["run_id"], "run_id": p["run_id"], "of_revision": revision,
                                "of_scenario": scenario, "workflow": f"{p['workflow']}@{p['workflow_version']}", "was_generated_by": generated_by,
                                "was_attributed_to": asked_by, "generated_at": p["started_at"],
                                "session_id": actor.get("agent_session_id"), "tool_call_id": actor.get("tool_call_id")})
        attempt = self.once("Attempt", {"iri": f"{run.replace('wsys:run/', 'wsys:attempt/')}/{p['attempt_number']}", "label": p["attempt_id"],
                                        "attempt_id": p["attempt_id"], "of_run": run, "manifest_sha256": file_digest(manifest_path),
                                        "attempt_manifest": self.embed("PlatformAttemptManifest", manifest),
                                        "was_associated_with": software, "used": [revision], "started_at": p["started_at"],
                                        "ended_at": p["finished_at"], "generated_at": p["finished_at"],
                                        "dc_conforms_to": [f"openswmm=={engine}"] if engine else None})
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
        self.attempts.append(attempt)
        return attempt

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
        """A study manifest, local or platform, as a Study and one Candidate per evaluation."""
        manifest = json.loads(path.read_text(encoding="utf-8"))
        definition, runtime = manifest["definition"], manifest["runtime"]
        comparable = json.dumps({k: v for k, v in manifest.items() if k != "runtime"}, sort_keys=True)
        study_id = runtime.get("study_id") or f"local_{hashlib.sha256(comparable.encode()).hexdigest()[:20]}"
        iri = f"wsys:study/{part(study_id)}"
        job = runtime.get("run_id") or study_id
        self.once("Study", {"iri": iri, "label": study_id, "study_id": study_id,
                            "of_revision": self.revisions.get(runtime.get("revision_manifest_sha256")),
                            "workflow": f"{definition['workflow']}@{definition['workflow_version']}",
                            "was_generated_by": f"wsys:job/{part(job)}",
                            "study_manifest": self.embed("PlatformStudyManifest", manifest)})
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
                                     for n, v in e["objectives"].items()]})
        return iri

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
    ld.write(a.out)


if __name__ == "__main__":
    main()
