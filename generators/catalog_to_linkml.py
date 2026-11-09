"""
OpenSWMM API catalog -> LinkML module (schema/model.yaml).

The engine publishes a machine-readable catalog (``openswmm.engine.catalog``,
``catalog.json``) of every target, property and method with type, access,
units and wrapped C symbols. This generator turns the *state* part of that
catalog -- element kinds, their subtype views and component views, their
properties and the enums they use -- into the ``wsys:model`` vocabulary, so
the ontology, the engine and the agent tool descriptions stay in lockstep.

Services (forcing, climate, options, ...) and methods are *behaviour*; they
belong to the tool registry, not the ontology, and are only listed in the
module's annotations.

Usage:
    python -m generators.catalog_to_linkml --catalog ../openswmm.engine/python/openswmm/engine/catalog.json
    python -m generators.catalog_to_linkml                      # uses the installed openswmm package
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent.parent
MAPPINGS = HERE / "schema" / "mappings" / "swmm_to_wsys.yaml"
OUT = HERE / "schema" / "model.yaml"
LOCK = HERE / "schema" / "model.lock.json"

# names that already exist as core/topology slots; prefix to avoid global-slot clashes
RESERVED_SLOTS = {"elevation", "geometry", "length", "roughness", "volume", "description", "label",
                  "system", "diameter", "material", "setting", "confidence"}

SCALAR = {"float": "float", "int": "integer", "str": "string", "bool": "boolean",
          "datetime": "datetime", "timedelta": "string"}
# unit *kinds* in the catalog depend on the model's unit system (US/SI); only these are fixed
FIXED_UCUM = {"m": "m", "m3": "m3", "s": "s", "d": "d", "min": "min", "m/s": "m/s",
              "percent": "%", "deg": "deg", "manning_n": None, "dimensionless": None}

SUBTYPE_CLASS = {  # catalog subtype code -> generated class name
    "JUNCTION": "SwmmJunction", "OUTFALL": "SwmmOutfall", "STORAGE": "SwmmStorage", "DIVIDER": "SwmmDivider",
    "CONDUIT": "SwmmConduit", "PUMP": "SwmmPump", "ORIFICE": "SwmmOrifice", "WEIR": "SwmmWeir", "OUTLET": "SwmmOutlet",
}
KIND_CLASS = {  # element kind target -> generated class name
    "node": "SwmmNode", "link": "SwmmLink", "subcatchment": "SwmmSubcatchment", "gage": "SwmmGage",
    "pollutant": "SwmmPollutant", "landuse": "SwmmLanduse", "pattern": "SwmmPattern", "table": "SwmmTable",
    "species": "SwmmReactionSpecies", "coefficient": "SwmmReactionCoefficient", "term": "SwmmReactionTerm",
}


def camel(s: str) -> str:
    return "".join(p.capitalize() for p in re.split(r"[._]", s))


def load_catalog(path: str | None) -> dict:
    if path:
        return json.loads(Path(path).read_text())
    from openswmm.engine import catalog  # type: ignore
    return catalog.load()


class CatalogToLinkML:
    def __init__(self, cat: dict, mappings: dict[str, str]):
        self.cat = cat
        self.targets = cat["targets"]
        self.members = cat["members"]
        self.enums_in = cat["enums"]
        self.mappings = mappings
        self.classes: dict[str, dict] = {}
        self.enums_out: dict[str, dict] = {}
        self.skipped: list[str] = []

    # -- type mapping ------------------------------------------------------
    def range_for(self, t: str, attr: dict) -> bool:
        """Set range/multivalued on attr; return False if the property is navigation, not state."""
        t = (t or "str").strip()
        m = re.fullmatch(r"Optional\[(.+)\]", t)
        if m:
            t = m.group(1)
        if t.startswith("Literal["):
            attr["range"] = "string"
            attr.setdefault("annotations", {})["python_type"] = t
            return True
        m = re.fullmatch(r"(?:List|list|tuple|Tuple|NDArray)\[(.+)\]", t)
        if m:
            inner = re.split(r",\s*", m.group(1))[0].strip().rstrip(".")
            inner = "float" if inner in ("Any", "...") else inner
            if inner in SCALAR:
                attr["range"] = SCALAR[inner]; attr["multivalued"] = True
                attr.setdefault("annotations", {})["python_type"] = t
                return True
            if inner in self.enums_in:
                attr["range"] = inner; attr["multivalued"] = True; self.use_enum(inner); return True
            attr["range"] = "string"; attr["multivalued"] = True
            attr.setdefault("annotations", {})["python_type"] = t
            attr["annotations"]["opaque"] = "true"
            return True
        if t in SCALAR:
            attr["range"] = SCALAR[t]
            if t == "timedelta":
                attr.setdefault("annotations", {})["python_type"] = t
            return True
        if t in self.enums_in:
            attr["range"] = t; self.use_enum(t); return True
        m = re.fullmatch(r"ref:(\w+)", t)
        if m and m.group(1) in KIND_CLASS:
            attr["range"] = KIND_CLASS[m.group(1)]; return True
        if t.startswith("Union["):
            attr["range"] = "SwmmElement"
            attr.setdefault("annotations", {})["python_type"] = t
            return True
        if t in self.cat.get("records", {}):
            attr["range"] = "string"
            attr.setdefault("annotations", {})["python_type"] = t
            attr["annotations"]["record_fields"] = ",".join(self.cat["records"][t].get("fields", {}))
            attr["annotations"]["opaque"] = "true"
            return True
        if t.startswith("dict["):
            attr["range"] = "string"; attr.setdefault("annotations", {})["python_type"] = t
            attr["annotations"]["opaque"] = "true"; return True
        # a view/solver object: navigation, not state
        return False

    def use_enum(self, name: str) -> None:
        if name in self.enums_out:
            return
        vals = self.enums_in[name]
        self.enums_out[name] = {
            "description": f"OpenSWMM enum {name} (catalog revision {self.cat['revision']}).",
            "annotations": {"catalog_enum": name},
            "permissible_values": {k: {"description": f"code {v}"} for k, v in vals.items()},
        }

    # -- attributes --------------------------------------------------------
    def attributes_for(self, target: str) -> tuple[dict, int]:
        attrs, methods = {}, 0
        for m in self.members:
            if m.get("target") != target:
                continue
            if m.get("form") == "method":
                methods += 1; continue
            if m.get("form") != "property":
                continue
            name = m["name"]
            attr: dict = {"description": (m.get("doc") or f"{target}.{name}").strip()}
            if not self.range_for(m.get("type", "str"), attr):
                self.skipped.append(m["path"]); continue
            ann = attr.setdefault("annotations", {})
            ann["catalog_path"] = m["path"]
            if m.get("access"):
                ann["access"] = m["access"]
            if m.get("access") == "r":
                attr["readonly"] = "engine-computed; not settable through the API"
            if m.get("c"):
                ann["c_symbols"] = ",".join(m["c"])
            if m.get("bulk"):
                ann["bulk"] = m["bulk"]
            u = m.get("units")
            if u:
                ann["unit_kind"] = u
                if u in FIXED_UCUM and FIXED_UCUM[u]:
                    attr["unit"] = {"ucum_code": FIXED_UCUM[u]}
            slot_name = f"swmm_{name}" if name in RESERVED_SLOTS else name
            attrs[slot_name] = attr
        return attrs, methods

    def class_annotations(self, target: str, methods: int, extra: dict | None = None) -> dict:
        t = self.targets[target]
        ann = {"catalog_target": target, "python_class": t["class"], "catalog_revision": str(self.cat["revision"]),
               "method_count": str(methods)}
        if extra:
            ann.update(extra)
        return ann

    # -- build -------------------------------------------------------------
    def build(self) -> dict:
        # base
        self.classes["SwmmElement"] = {
            "is_a": "Entity", "abstract": True,
            "description": "An element of a SWMM/OpenSWMM model as it exists in one packaged Revision. Identified by revision plus the element's SWMM id; state properties mirror the engine's API catalog.",
            "slots": ["element_id", "revision"],
        }
        # element kinds
        for target, cname in KIND_CLASS.items():
            t = self.targets[target]
            attrs, methods = self.attributes_for(target)
            cls = {"is_a": "SwmmElement", "description": (t.get("doc") or f"SWMM {t['class']}").strip(),
                   "attributes": attrs, "annotations": self.class_annotations(target, methods)}
            if t.get("subtype_field"):
                cls["abstract"] = True
                cls["annotations"]["subtype_field"] = t["subtype_field"]
            if cname in self.mappings:
                cls["exact_mappings"] = [self.mappings[cname]]
            self.classes[cname] = cls
        # subtype views -> subclasses; component views -> inlined classes
        for target, t in self.targets.items():
            parent = t.get("parent")
            if not parent or parent not in KIND_CLASS:
                continue
            pname = KIND_CLASS[parent]
            attrs, methods = self.attributes_for(target)
            if t.get("subtypes"):
                code = t["subtypes"][0]
                cname = SUBTYPE_CLASS[code]
                cls = {"is_a": pname, "description": (t.get("doc") or f"{t['class']}: {parent} of subtype {code}").strip(),
                       "attributes": attrs, "annotations": self.class_annotations(target, methods, {"subtype_code": code})}
                if cname in self.mappings:
                    cls["exact_mappings"] = [self.mappings[cname]]
                self.classes[cname] = cls
            elif not attrs and any(m.get("target") == target and m.get("form") == "item" for m in self.members):
                slot = target.split(".")[-1]
                self.classes[pname]["attributes"][slot] = {
                    "range": "string", "multivalued": True,
                    "description": f"Keyed items of {t['class']} ({target}); one 'key=value' entry per item. Read and write through the engine tools, e.g. get(kind='{parent}', fields=['{slot}.<key>']).",
                    "annotations": {"catalog_target": target, "python_class": t["class"], "item_view": "true"}}
            else:
                cname = "Swmm" + camel(target)           # node.stats -> SwmmNodeStats
                self.classes[cname] = {"description": (t.get("doc") or f"{t['class']} component view of {parent}").strip(),
                                       "attributes": attrs, "annotations": self.class_annotations(target, methods)}
                slot = target.split(".")[-1]
                self.classes[pname]["attributes"][slot] = {"range": cname, "inlined": True,
                                                           "description": f"{t['class']} view ({target})."}
        # subtypes with no dedicated view (JUNCTION, CONDUIT) still get a class
        for enum_name, parent in (("NodeType", "SwmmNode"), ("LinkType", "SwmmLink")):
            for code in self.enums_in.get(enum_name, {}):
                cname = SUBTYPE_CLASS[code]
                if cname not in self.classes:
                    cls = {"is_a": parent, "description": f"{parent} of subtype {code}; no additional engine properties.",
                           "annotations": {"subtype_code": code, "catalog_revision": str(self.cat["revision"])}}
                    if cname in self.mappings:
                        cls["exact_mappings"] = [self.mappings[cname]]
                    self.classes[cname] = cls
            self.use_enum(enum_name)
        services = sorted(k for k, v in self.targets.items() if not v.get("collection") and not (v.get("parent") in KIND_CLASS))
        return {
            "id": "https://w3id.org/wsys/model",
            "name": "wsys-model",
            "title": "Water Systems Ontology — SWMM model elements (generated)",
            "description": ("GENERATED from the OpenSWMM engine API catalog by generators/catalog_to_linkml.py; do not edit. "
                            f"Catalog revision {self.cat['revision']}. Element kinds, subtype views and component views become classes; "
                            "properties become attributes with catalog path, access, C symbols and unit kind as annotations. "
                            "Services and methods are behaviour and live in the tool registry."),
            "version": f"catalog-r{self.cat['revision']}",
            "license": "Apache-2.0",
            "prefixes": {"wsys": "https://w3id.org/wsys/", "model": "https://w3id.org/wsys/model/", "linkml": "https://w3id.org/linkml/"},
            "default_prefix": "model",
            "default_range": "string",
            "imports": ["linkml:types", "core", "analysis"],
            "annotations": {"catalog_revision": str(self.cat["revision"]), "catalog_services": ",".join(services),
                            "skipped_navigation_properties": str(len(self.skipped))},
            "slots": {
                "element_id": {"range": "string", "required": True, "description": "The element's id in the SWMM model (.inp name)."},
                "revision": {"range": "Revision", "required": True, "description": "The immutable packaged model revision this element belongs to (hydrocouple.platform revision-manifest)."},
            },
            "enums": self.enums_out,
            "classes": self.classes,
        }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--catalog", help="path to catalog.json; default: the installed openswmm package")
    ap.add_argument("--check", action="store_true", help="fail if schema/model.yaml is stale")
    a = ap.parse_args()
    cat = load_catalog(a.catalog)
    mappings = yaml.safe_load(MAPPINGS.read_text()) or {}
    gen = CatalogToLinkML(cat, mappings)
    schema = gen.build()
    text = yaml.safe_dump(schema, sort_keys=False, allow_unicode=True, width=120)
    digest = hashlib.sha256(json.dumps(cat, sort_keys=True).encode()).hexdigest()
    lock = {"catalog_revision": cat["revision"], "catalog_sha256": digest, "classes": len(schema["classes"]),
            "enums": len(schema["enums"]), "skipped_navigation_properties": gen.skipped}
    if a.check:
        if OUT.read_text() != text or json.loads(LOCK.read_text()).get("catalog_sha256") != digest:
            raise SystemExit("schema/model.yaml is stale: run `make model`")
        print("model.yaml up to date"); return
    OUT.write_text(text)
    lock["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    LOCK.write_text(json.dumps(lock, indent=2) + "\n")
    print(f"wrote {OUT.relative_to(HERE)}: {lock['classes']} classes, {lock['enums']} enums; "
          f"skipped {len(gen.skipped)} navigation properties; catalog r{cat['revision']}")


if __name__ == "__main__":
    main()
