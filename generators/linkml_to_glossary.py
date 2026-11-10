"""
LinkML -> business glossary payload (dist/glossary/glossary.json, domains.json).

A neutral JSON form of the ontology's terms: one entry per class, slot and
enum value with its IRI, definition, module (domain), parent, standard
mappings and the Unity Catalog object it is bound to. A small adapter posts
these to the catalog's glossary API; the adapter is deliberately not included
until it can be written against the target workspace's API version.

Usage: python -m generators.linkml_to_glossary schema/wsys.yaml -o dist/glossary
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from generators.linkml_to_delta import DeltaGenerator, snake


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("schema", nargs="?", default="schema/wsys.yaml")
    ap.add_argument("--catalog", default="water_core")
    ap.add_argument("-o", "--out", default="dist/glossary")
    a = ap.parse_args()
    d = DeltaGenerator(a.schema, a.catalog)
    sv = d.sv
    sv.imports_closure()
    by_id = {s.id: s for s in sv.schema_map.values()}
    concrete = set(d.concrete_classes())
    terms = []
    for c in sv.all_classes().values():
        mod = by_id[c.from_schema].name if c.from_schema in by_id else sv.schema.name
        terms.append({"kind": "class", "iri": sv.get_uri(c, expand=True), "name": c.name, "definition": " ".join((c.description or "").split()),
                      "domain": mod.replace("wsys-", ""), "parent": sv.get_uri(sv.get_class(c.is_a), expand=True) if c.is_a else None,
                      "abstract": bool(c.abstract), "mappings": list(c.exact_mappings or []) + list(c.close_mappings or []),
                      "bound_to": d.fq(d.module_of(c.name), snake(c.name)) if c.name in concrete else None})
        if c.name in concrete:
            for s in d.ordered_slots(c.name):
                terms.append({"kind": "property", "iri": sv.get_uri(s, expand=True), "name": s.name, "of_class": c.name,
                              "definition": " ".join((s.description or "").split()), "domain": mod.replace("wsys-", ""), "range": s.range,
                              "required": bool(s.required), "unit": s.unit.ucum_code if s.unit else None,
                              "bound_to": d.fq(d.module_of(c.name), snake(c.name)) + "." + s.name})
    for e in sv.all_enums().values():
        for pv in e.permissible_values.values():
            terms.append({"kind": "value", "iri": f"{sv.get_uri(e, expand=True)}#{pv.text}", "name": pv.text, "of_enum": e.name,
                          "definition": pv.description or "", "bound_to": d.fq("reference", snake(e.name))})
    domains = [{"name": s.name.replace("wsys-", ""), "title": s.title, "description": " ".join((s.description or "").split()), "iri": s.id}
               for s in sv.schema_map.values() if s.name.startswith("wsys-")]
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    (out / "glossary.json").write_text(json.dumps({"ontology_version": sv.schema.version, "terms": terms}, indent=1) + "\n")
    (out / "domains.json").write_text(json.dumps(sorted(domains, key=lambda x: x["name"]), indent=1) + "\n")
    kinds = {k: sum(1 for t in terms if t["kind"] == k) for k in ("class", "property", "value")}
    print(f"wrote {out}: {kinds} in {len(domains)} domains")


if __name__ == "__main__":
    main()
