"""Phase 5 vocabulary: compositions of OpenSWMM engine components run on the platform.

Real platform runs (hydrocouple.platform tests/integration/test_compositions.py, end to end on
a composition host): the engine's split-water and split-material examples, each a surface and a
network component exchanging one phase port, packaged as an authored composition and run by
composition.run@1 (hydrocouple.platform
test_artifacts/_phase5/ontology_fixture/make_composition_fixture.py). Files are kept under
dist/test/compositions.
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
FIX = ROOT / "tests" / "fixtures" / "platform" / "compositions"
OUT = ROOT / "dist" / "test" / "compositions"


def load(name: str) -> tuple[Path, PlatformLoader]:
    out = OUT / name
    shutil.rmtree(out, ignore_errors=True)
    loader = PlatformLoader("demo", name)
    loader.add_revision(FIX / name / "revision-manifest.json")
    loader.add_attempt(FIX / name / "attempt")
    loader.write(out / "rows")
    return out / "rows", loader


def table(rows: Path, name: str) -> list[dict]:
    path = rows / f"{name}.jsonl"
    return findings.read_rows(path) if path.exists() else []


def rules(rows: Path) -> set[str]:
    h = Harness()
    h.load(rows)
    return {rid for rid, _, _, _ in h.violations()}


@pytest.mark.parametrize("name, roles, port", [
    ("split-water", ["integrated2d", "split_network"], "openswmm.node-surface-water.v1"),
    ("split-material", ["split_material_surface", "split_material_network"],
     "openswmm.node-surface-material.v10"),
])
def test_a_composition_run_names_its_components_and_their_connection(name, roles, port):
    rows, loader = load(name)
    (revision,) = table(rows, "analysis.revision")
    (composition,) = table(rows, "coupling.composition")
    assert revision["of_composition"] == composition["iri"]
    components = {c["label"]: c for c in table(rows, "coupling.model_component")}
    assert set(components) == {"surface", "network"}
    assert composition["components"] == [components["surface"]["iri"], components["network"]["iri"]]
    assert [{a["argument_name"]: a["argument_value"] for a in components[c]["arguments"]}["role"]
            for c in ("surface", "network")] == roles
    assert all(c["component_kind"] == "SWMM" for c in components.values())
    (connection,) = table(rows, "coupling.component_connection")
    (output,) = table(rows, "coupling.output")
    (inp,) = table(rows, "coupling.input")
    assert (output["item_id"], output["component"]) == (port, components["surface"]["iri"])
    assert (inp["item_id"], inp["component"]) == (port, components["network"]["iri"])
    assert (connection["provider"], connection["consumer"]) == (output["iri"], inp["iri"])
    edges = [e for e in table(rows, "topology.edge") if e["relation"] == "COUPLED_TO"]
    assert [(e["from_iri"], e["to_iri"]) for e in edges] == [
        (components["surface"]["iri"], components["network"]["iri"])]
    (attempt,) = table(rows, "analysis.attempt")
    assert attempt["used"] == [revision["iri"], components["surface"]["iri"],
                               components["network"]["iri"]]
    assert rules(rows) == set()


def test_a_connection_outside_its_composition_fails_a_rule():
    rows, _ = load("split-water")
    connections = table(rows, "coupling.component_connection")
    connections[0]["composition"] = "wsys:revision/demo/other/composition"
    (rows / "coupling.component_connection.jsonl").write_text(
        "".join(json.dumps(c, sort_keys=True) + "\n" for c in connections))
    assert rules(rows) == {"connection_within_its_composition"}


def test_a_composition_that_is_not_the_one_the_revision_lists_is_refused():
    out = OUT / "tampered"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    shutil.copy(FIX / "split-water" / "revision-manifest.json", out / "revision-manifest.json")
    doc = json.loads((FIX / "split-water" / "composition.json").read_text())
    doc["connections"] = []
    (out / "composition.json").write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="checksum differs"):
        PlatformLoader("demo", "tampered").add_revision(out / "revision-manifest.json")
