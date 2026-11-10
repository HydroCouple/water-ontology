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


def test_valid_network_conforms(shapes):
    ok, text = run(shapes, "valid_network.ttl")
    assert ok, text


@pytest.mark.parametrize("name, expect", [
    ("invalid_conduit_missing_length.ttl", "length"),
    ("invalid_enum_value.ttl", "lifecycle_status"),
    ("invalid_unknown_property.ttl", "from_node"),
    ("invalid_event_missing_detected_at.ttl", "detected_at"),
])
def test_invalid_fixtures_fail_for_the_right_reason(shapes, name, expect):
    ok, text = run(shapes, name)
    assert not ok, f"{name} should not conform"
    assert expect in text, f"{name} failed, but not on {expect}:\n{text[:600]}"
