"""Versioned profile bindings and unit transforms shared by exporters."""

from functools import lru_cache
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "schema/profiles/swmm.yaml"


@lru_cache
def profile(path=PROFILE):
    return yaml.safe_load(Path(path).read_text())


def normalize_code(scheme, token):
    return profile()["codes"].get(scheme, {}).get(token, token)


def geometry_values(shape, values, metres):
    positions = profile()["shape_lengths"].get(shape)
    if positions is None:
        return {}
    return {
        f"geom{i}": round(v * metres, 6) if i in positions else v
        for i, v in enumerate(values, 1)
        if v is not None
    }


def annotation(element, name):
    annotations = element.annotations
    if not annotations:
        return None
    items = annotations._as_dict if hasattr(annotations, "_as_dict") else annotations
    entry = items.get(name)
    if entry is None:
        return None
    return str(entry["value"] if isinstance(entry, dict) else entry.value)


def source_profile(extension=None):
    base = profile()
    if extension is None:
        return base
    extra = profile(Path(extension))
    required = {"id", "version", "extends", "owner", "source", "sections"}
    if (
        not required <= extra.keys()
        or extra["extends"] != base["id"]
        or extra["id"] == base["id"]
    ):
        raise ValueError(
            "Extension must declare a distinct release and its base, owner, source and sections"
        )
    overlap = set(base["sections"]) & set(extra["sections"])
    if overlap:
        raise ValueError(
            f"Extension would override existing sections: {sorted(overlap)}"
        )
    reserved = set(extra) - required
    if reserved:
        raise ValueError(f"Unsupported section-profile keys: {sorted(reserved)}")
    return {**base, **extra, "sections": {**base["sections"], **extra["sections"]}}
