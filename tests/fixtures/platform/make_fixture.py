"""Write the platform fixture in this folder. Run it with hydrocouple.platform installed:

    python tests/fixtures/platform/make_fixture.py

Every document is built with the platform's own contracts and the engine's own
report dataclasses and unit labels, so it has the shape and units a real run
publishes. THE NUMBERS ARE WRITTEN BY HAND: no engine ran. Two revisions of one
model, a gate open and the gate closed, each run once by the same agent session.
"""
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

from openswmm.engine import _report as r
from openswmm.engine import catalog

from hydrocouple_platform.contracts.jobs import Actor
from hydrocouple_platform.contracts.manifests import (ArtifactEntry, AttemptManifest, ManifestFile, Provenance,
                                                      RevisionManifest, RuntimeVersions, canonical_json)
from hydrocouple_platform.runner.summary import build_summary

HERE = Path(__file__).resolve().parent
WHEN = datetime(2026, 11, 20, 15, 0, tzinfo=timezone.utc)
ENGINE = "6.0.0a4.dev1"


def digest(data: bytes) -> str:
    return sha256(data).hexdigest()


def unit_of(path):
    return catalog.unit_label(catalog.lookup(path).get("units"), "US", "CFS")


def snapshot(flooded):
    return r.ReportSnapshot(
        routing_diagnostics=r.RoutingDiagnostics(4.5, 0.5, 5.0, 4800, 0.0, 0, 2.0, 0.8),
        runoff_continuity=r.RunoffContinuity(-0.05, 3.2, 0.0, 1.1, 2.1, 0.0, 0.0, 0.0),
        routing_continuity=r.RoutingContinuity(0.02, 0.0, 6.6, 0.0, 0.0, 0.0, sum(f[1] for f in flooded) / 43560, 6.1,
                                               0.0, 0.0, 0.0, 0.01),
        quality_continuity=[],
        node_flooding=[r.NodeFloodingEntry(n, "JUNCTION", 4.75, rate, volume, seconds) for n, volume, rate, seconds in flooded],
        storage_summary=[],
        link_flow_summary=[r.LinkFlowEntry("C1", "CONDUIT", 4.17, 2.31, 0.35, 15100.0, 0.0),
                           r.LinkFlowEntry("C2", "CONDUIT", 12.6 if flooded else 9.84, 5.02, 1.0 if flooded else 0.71,
                                           48200.0, 0.4 if flooded else 0.0)],
        pump_summary=[],
        subcatchment_summary=[r.SubcatchmentEntry("S1", 13940.0, 9150.0, 4.17, 0.656)])


def write(folder: Path, name: str, data: bytes) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(data)


def main():
    for case, gate, flooded in (("gate_open", "OPEN", []), ("gate_closed", "CLOSED", [("J10", 2310.5, 3.42, 1260.0)])):
        model = f";; site drainage, gate {gate}\n".encode()
        composition = b'{"components": ["org.hydrocouple.openswmm.engine"]}'
        revision = RevisionManifest(
            files=[ManifestFile(path="composition.json", sha256=digest(composition), size=len(composition), role="composition"),
                   ManifestFile(path="model/model.inp", sha256=digest(model), size=len(model), role="model", source="model.inp")],
            components=["org.hydrocouple.openswmm.engine"], total_bytes=len(model) + len(composition), created_at=WHEN,
            packaged_by="tests/fixtures/platform/make_fixture.py")
        revision_bytes = canonical_json(revision.model_dump(mode="json", by_alias=True))
        write(HERE / case, "revision-manifest.json", revision_bytes)

        attempt_dir = HERE / case / "attempt"
        summary = canonical_json(build_summary(snapshot(flooded), unit_of, engine=ENGINE, unit_system="US",
                                               flow_units="CFS").model_dump(mode="json", by_alias=True))
        write(attempt_dir, "summary.json", summary)
        run = canonical_json({"schema": "hydrocouple/run/1.0", "status": "completed"})
        artifacts = [ArtifactEntry(path="run.json", sha256=digest(run), size=len(run), kind="run_manifest", format="json"),
                     ArtifactEntry(path="model.rpt", sha256=digest(b"report " + model), size=7 + len(model), kind="report", format="swmm_rpt"),
                     ArtifactEntry(path="summary.json", sha256=digest(summary), size=len(summary), kind="summary", format="json")]
        attempt = AttemptManifest(
            run_manifest_sha256=digest(run), status="completed", artifacts=artifacts,
            quality={"continuity_errors": {"runoff": -0.05, "routing": 0.02}},
            provenance=Provenance(
                run_id=f"run_{case}", job_id=f"job_{case}", attempt_id=f"att_{case}", attempt_number=1,
                revision_id=f"rev_{case}", revision_manifest_sha256=digest(revision_bytes),
                composition_sha256=digest(composition), inputs={f.path: f.sha256 for f in revision.files},
                workflow="swmm.simulation", workflow_version="1", parameters={"threads": 1}, threads=1,
                runtime=RuntimeVersions(platform="0.1.0.dev0", python="3.12", engine=ENGINE),
                actor=Actor(human_subject="alice", agent_session_id="ags_fixture", agent_client="openswmm.mcp",
                            agent_client_version="0.4.0", model="example-model", tool_call_id=f"call_{case}"),
                started_at=WHEN, finished_at=WHEN.replace(minute=2)))
        write(attempt_dir, "attempt.json", canonical_json(attempt.model_dump(mode="json", by_alias=True)))


if __name__ == "__main__":
    main()
