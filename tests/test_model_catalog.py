"""model.yaml must be exactly what the generator produces from the pinned catalog."""
import json
import os
from pathlib import Path

import pytest
import yaml

from generators.catalog_to_linkml import CatalogToLinkML, MAPPINGS, OUT, LOCK, load_catalog

ROOT = Path(__file__).resolve().parent.parent
CATALOG = os.environ.get("CATALOG", str(ROOT.parent / "openswmm.engine/python/openswmm/engine/catalog.json"))


@pytest.fixture(scope="module")
def cat():
    if not Path(CATALOG).exists():
        pytest.skip(f"catalog not found at {CATALOG}; set CATALOG=")
    return load_catalog(CATALOG)


def test_model_yaml_is_fresh(cat):
    schema = CatalogToLinkML(cat, yaml.safe_load(MAPPINGS.read_text())).build()
    expected = yaml.safe_dump(schema, sort_keys=False, allow_unicode=True, width=120)
    assert OUT.read_text() == expected, "schema/model.yaml is stale: run `make model`"


def test_lock_matches_catalog_revision(cat):
    lock = json.loads(LOCK.read_text())
    assert lock["catalog_revision"] == cat["revision"]


def test_every_mapping_target_is_a_generated_class():
    classes = yaml.safe_load(OUT.read_text())["classes"]
    for cname in yaml.safe_load(MAPPINGS.read_text()):
        assert cname in classes, f"{cname} in swmm_to_wsys.yaml is not a generated class"


def test_subtypes_cover_node_and_link_enums(cat):
    classes = yaml.safe_load(OUT.read_text())["classes"]
    codes = {c["annotations"]["subtype_code"] for c in classes.values() if "subtype_code" in c.get("annotations", {})}
    assert set(cat["enums"]["NodeType"]) | set(cat["enums"]["LinkType"]) <= codes


def test_swmm_mapping_targets_resolve_to_wsys_classes():
    """Every wsys:X in swmm_to_wsys.yaml is a class in the release root (or in a module not yet released)."""
    from linkml_runtime.utils.schemaview import SchemaView
    sv = SchemaView(str(ROOT / "schema" / "wsys.yaml"))
    classes = set(sv.all_classes())
    pending = set()
    missing = [v for v in yaml.safe_load(MAPPINGS.read_text()).values()
               if v not in pending and v.split(":", 1)[1] not in classes]
    assert not missing, f"mapping targets not in the ontology: {missing}"
