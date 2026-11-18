from pathlib import Path

from loaders.source_records import read_records
from generators.profile import normalize_code

EXAMPLE = Path(__file__).resolve().parents[1] / "examples/site_drainage/model.inp"


def test_all_water_quality_input_records_are_preserved():
    from collections import Counter

    records = read_records(EXAMPLE)
    counts = Counter(row.section for row in records)
    assert {
        key: counts[key]
        for key in (
            "POLLUTANTS",
            "LANDUSES",
            "COVERAGES",
            "LOADINGS",
            "BUILDUP",
            "WASHOFF",
            "TIMESERIES",
        )
    } == {
        "POLLUTANTS": 1,
        "LANDUSES": 4,
        "COVERAGES": 10,
        "LOADINGS": 1,
        "BUILDUP": 4,
        "WASHOFF": 4,
        "TIMESERIES": 72,
    }
    loading = next(row for row in records if row.section == "LOADINGS")
    assert loading.tokens == ["S1", "TSS", "80"]
    assert EXAMPLE.read_text().splitlines()[loading.line - 1] == loading.raw


def test_modified_infiltration_aliases():
    assert normalize_code("InfiltrationModel", "MOD_HORTON") == "MODIFIED_HORTON"
    assert (
        normalize_code("InfiltrationModel", "MOD_GREEN_AMPT") == "MODIFIED_GREEN_AMPT"
    )


def test_approved_mapping_requires_traceable_review():
    import pytest
    from generators.semantic_registry import mapping_records

    assertion = ("urn:source", "skos:closeMatch", "urn:target", "urn:schema")
    decision = dict(zip(("subject", "predicate", "object"), assertion[:3]))
    governance = {
        "review_status": "proposed_for_release",
        "owner": "registry",
        "assertion_reviews": [decision],
    }
    decision.update(status="approved", reviewer="reviewer")
    with pytest.raises(ValueError, match="Missing decision evidence"):
        mapping_records([assertion], governance, "0.2.0")
    decision.update(
        decision_date="2026-11-17", evidence="urn:review:1", target_version="1"
    )
    assert (
        mapping_records([assertion], governance, "0.2.0")[0]["evidence"]
        == "urn:review:1"
    )
