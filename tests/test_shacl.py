"""Instance fixtures against the generated SHACL shapes (dist/wsys.shacl.ttl; run `make shacl` first)."""
from pathlib import Path

import pytest
import rdflib
from pyshacl import validate

ROOT = Path(__file__).resolve().parent.parent
SHAPES = ROOT / "dist" / "wsys.shacl.ttl"
FIX = ROOT / "tests" / "fixtures"


@pytest.fixture(scope="module")
def shapes():
    if not SHAPES.exists():
        pytest.skip("dist/wsys.shacl.ttl missing; run `make shacl`")
    return rdflib.Graph().parse(SHAPES)


def run(shapes, name):
    data = rdflib.Graph().parse(FIX / name)
    conforms, _, text = validate(data, shacl_graph=shapes, inference="none", advanced=False)
    return conforms, text


@pytest.mark.parametrize("name", ["valid_network.ttl", "valid_agent_trace.ttl", "valid_scenarios.ttl",
                                  "valid_experiments.ttl"])
def test_valid_fixtures_conform(shapes, name):
    ok, text = run(shapes, name)
    assert ok, text


@pytest.mark.parametrize("name, expect", [
    ("invalid_conduit_missing_length.ttl", "length"),
    ("invalid_enum_value.ttl", "lifecycle_status"),
    ("invalid_unknown_property.ttl", "from_node"),
    ("invalid_event_missing_detected_at.ttl", "detected_at"),
    ("invalid_tool_call_without_session.ttl", "in_session"),
    ("invalid_tool_call_effect.ttl", "effect"),
    ("invalid_agent_session_without_agent.ttl", "wasAssociatedWith"),
    ("invalid_tool_without_name.ttl", "tool_name"),
    ("invalid_override_without_path.ttl", "catalog_path"),
    ("invalid_approval_without_state.ttl", "approval_state"),
    ("invalid_event_set_aggregation.ttl", "event_aggregation"),
    ("invalid_scenario_without_revision.ttl", "of_revision"),
    ("invalid_parameter_set_without_study.ttl", "of_study"),
    ("invalid_study_kind.ttl", "study_kind"),
    ("invalid_threshold_without_metric.ttl", "metric"),
])
def test_invalid_fixtures_fail_for_the_right_reason(shapes, name, expect):
    ok, text = run(shapes, name)
    assert not ok, f"{name} should not conform"
    assert expect in text, f"{name} failed, but not on {expect}:\n{text[:600]}"
