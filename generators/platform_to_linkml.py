"""
hydrocouple.platform JSON Schemas -> LinkML module (schema/analysis_platform.yaml).

The platform publishes versioned JSON Schemas for its contracts (revision
manifest, run request, job spec, attempt manifest, study view, ...). This
generator turns them into LinkML classes so the ontology's analysis layer
mirrors the real execution contracts instead of inventing shapes. Each top-
level schema becomes a `Platform*` class; `$defs` become inlined component
classes; enums are copied. The curated semantic layer (schema/analysis.yaml)
subclasses or embeds these.

Usage:
    python -m generators.platform_to_linkml --schemas ../hydrocouple.platform/src/hydrocouple_platform/schemas/platform
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
OUT = HERE / "schema" / "analysis_platform.yaml"
LOCK = HERE / "schema" / "analysis_platform.lock.json"

CONTRACTS = {  # schema file stem -> generated class name
    "revision-manifest-1": "PlatformRevisionManifest",
    "run-request-1": "PlatformRunRequest",
    "job-spec-1": "PlatformJobSpec",
    "attempt-manifest-1": "PlatformAttemptManifest",
    "study-view-1": "PlatformStudyView",
    "event-1": "PlatformEvent",
    "error-1": "PlatformError",
}
RESERVED = {"description", "label", "geometry", "system", "value", "unit", "event", "revision", "status"}


def pascal(s: str) -> str:
    return "".join(p[:1].upper() + p[1:] for p in re.split(r"[^A-Za-z0-9]+", s) if p)


class SchemaToLinkML:
    def __init__(self, schemas: dict[str, dict]):
        self.schemas = schemas
        self.classes: dict[str, dict] = {}
        self.enums: dict[str, dict] = {}

    def enum_for(self, name: str, values: list, doc: str = "") -> str:
        ename = pascal(name) if not name.endswith("Enum") else pascal(name)
        if ename not in self.enums:
            self.enums[ename] = {"description": doc or f"hydrocouple.platform enum {name}.",
                                 "permissible_values": {str(v).upper().replace("-", "_").replace(" ", "_"): {"description": f"platform value '{v}'"} for v in values}}
        return ename

    def resolve(self, node: dict, root: dict) -> dict:
        if "$ref" in node:
            name = node["$ref"].split("/")[-1]
            defs = root.get("$defs") or root.get("definitions") or {}
            return defs.get(name, {}), name
        return node, None

    def attr_for(self, prop: str, node: dict, root: dict, owner: str) -> dict:
        attr: dict = {"description": (node.get("description") or node.get("title") or f"{owner}.{prop}").strip()}
        node, ref = self.resolve(node, root)
        if ref and not node.get("enum"):
            attr["range"] = self.class_for(ref, node, root); attr["inlined"] = True
            return attr
        # anyOf / oneOf with null -> optional of the other
        for key in ("anyOf", "oneOf"):
            if key in node:
                opts = [o for o in node[key] if o.get("type") != "null"]
                if len(opts) == 1:
                    sub = self.attr_for(prop, opts[0], root, owner); sub["description"] = attr["description"]; return sub
                attr["range"] = "string"; attr.setdefault("annotations", {})["json_schema"] = json.dumps(node, separators=(",", ":"))[:400]
                return attr
        t = node.get("type")
        if isinstance(t, list):
            t = next((x for x in t if x != "null"), "string")
        if "enum" in node:
            attr["range"] = self.enum_for(ref or f"{owner}{pascal(prop)}", node["enum"], node.get("description", "")); return attr
        if t == "array":
            items = node.get("items") or {}
            sub = self.attr_for(prop, items, root, owner)
            sub.update({"multivalued": True, "description": attr["description"]})
            if sub.get("inlined"):
                sub["inlined_as_list"] = True
            return sub
        if t == "object":
            if node.get("properties"):
                attr["range"] = self.class_for(ref or f"{owner}{pascal(prop)}", node, root); attr["inlined"] = True
            else:
                attr["range"] = "string"; attr.setdefault("annotations", {})["json_object"] = "true"
            return attr
        fmt = node.get("format")
        attr["range"] = {"string": "datetime" if fmt == "date-time" else ("date" if fmt == "date" else "string"),
                         "integer": "integer", "number": "float", "boolean": "boolean"}.get(t, "string")
        for k in ("minimum", "maximum"):
            if k in node:
                attr[f"{k}_value"] = node[k]
        if node.get("const") is not None:
            attr.setdefault("annotations", {})["const"] = str(node["const"])
        return attr

    def class_for(self, name: str, node: dict, root: dict, top: bool = False) -> str:
        cname = name if top else ("Platform" + pascal(name) if not name.startswith("Platform") else name)
        if cname in self.classes:
            return cname
        self.classes[cname] = {}  # reserve against recursion
        attrs = {}
        req = set(node.get("required") or [])
        for prop, sub in (node.get("properties") or {}).items():
            a = self.attr_for(prop, sub, root, cname)
            if prop in req:
                a["required"] = True
            a.setdefault("annotations", {})["json_property"] = prop
            attrs[f"platform_{prop}" if prop in RESERVED else prop] = a
        self.classes[cname] = {"description": (node.get("description") or node.get("title") or f"hydrocouple.platform {name}").strip(),
                               "attributes": attrs,
                               "annotations": {"json_schema_id": node.get("$id", "") or f"{name}", "platform_contract": "true"}}
        return cname

    def build(self) -> dict:
        for stem, cname in CONTRACTS.items():
            if stem in self.schemas:
                self.class_for(cname, self.schemas[stem], self.schemas[stem], top=True)
                self.classes[cname]["annotations"]["schema_file"] = f"{stem}.json"
        return {
            "id": "https://w3id.org/wsys/analysis-platform",
            "name": "wsys-analysis-platform",
            "title": "Water Systems Ontology — hydrocouple.platform contracts (generated)",
            "description": ("GENERATED from hydrocouple.platform JSON Schemas by generators/platform_to_linkml.py; do not edit. "
                            "Top-level contracts become Platform* classes, $defs become inlined component classes. "
                            "The curated analysis module (schema/analysis.yaml) embeds these as manifests."),
            "version": "platform-" + hashlib.sha256(json.dumps(self.schemas, sort_keys=True).encode()).hexdigest()[:12],
            "license": "Apache-2.0",
            "prefixes": {"wsys": "https://w3id.org/wsys/", "hcp": "https://w3id.org/wsys/platform/", "linkml": "https://w3id.org/linkml/"},
            "default_prefix": "hcp",
            "default_range": "string",
            "imports": ["linkml:types"],
            "enums": self.enums,
            "classes": self.classes,
        }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--schemas", required=True, help="directory of hydrocouple.platform JSON Schemas")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    d = Path(a.schemas)
    schemas = {p.stem: json.loads(p.read_text()) for p in sorted(d.glob("*.json")) if p.stem in CONTRACTS}
    gen = SchemaToLinkML(schemas)
    text = yaml.safe_dump(gen.build(), sort_keys=False, allow_unicode=True, width=120)
    digest = hashlib.sha256(json.dumps(schemas, sort_keys=True).encode()).hexdigest()
    if a.check:
        if OUT.read_text() != text or json.loads(LOCK.read_text()).get("schemas_sha256") != digest:
            raise SystemExit("schema/analysis_platform.yaml is stale: run `make platform`")
        print("analysis_platform.yaml up to date"); return
    OUT.write_text(text)
    LOCK.write_text(json.dumps({"schemas_sha256": digest, "contracts": sorted(schemas), "classes": len(gen.classes), "enums": len(gen.enums),
                                "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}, indent=2) + "\n")
    print(f"wrote {OUT.relative_to(HERE)}: {len(gen.classes)} classes, {len(gen.enums)} enums from {len(schemas)} contracts")


if __name__ == "__main__":
    main()
