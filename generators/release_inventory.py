"""Produce a release inventory and reconcile coverage against actual exported evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

import yaml

from .linkml_to_assertions import Assertions
from .linkml_to_delta import DeltaGenerator
from .profile import ROOT, annotation, profile
from .semantic_registry import registry


def inventory(rows, compare_catalog=None):
    schema_dir = ROOT / "schema"
    gen = DeltaGenerator(str(schema_dir / "wsys.yaml"), "water_core")
    catalog = json.loads((ROOT / "vendor/openswmm/catalog.json").read_text())
    lock = json.loads((schema_dir / "model.lock.json").read_text())
    digest = hashlib.sha256(json.dumps(catalog, sort_keys=True).encode()).hexdigest()
    if digest != lock["catalog_sha256"]:
        raise ValueError("Engine catalog digest differs from lock")
    sites = defaultdict(list)
    for c in gen.sv.all_classes().values():
        for key, slot in (c.attributes or {}).items():
            path = annotation(slot, "catalog_path")
            if path:
                sites[path].append(
                    {
                        "class": c.name,
                        "field": key,
                        "iri": gen.sv.get_uri(slot, expand=True),
                    }
                )
    coverage = Counter()
    counts = Counter()
    field_counts = Counter()
    extra_tokens = Counter()
    summary = json.loads((rows / "SUMMARY.json").read_text())

    def observe(cls, data):
        for key, val in data.items():
            slot = slots.get((cls, key))
            if slot is None:
                continue
            path = annotation(slot, "catalog_path")
            if path and val is not None:
                coverage[path] += 1
            if isinstance(val, dict) and slot.inlined:
                observe(slot.range, val)

    slots = {
        (cls, s.name): s
        for cls in gen.sv.all_classes()
        for s in gen.sv.class_induced_slots(cls)
    }
    by_uri = {
        gen.sv.get_uri(c, expand=True): c.name for c in gen.sv.all_classes().values()
    }
    for file in rows.glob("model.*.jsonl"):
        for line in file.read_text().splitlines():
            row = json.loads(line)
            observe(by_uri[row["class_iri"]], row)
    for line in (rows / "analysis.input_record.jsonl").read_text().splitlines():
        row = json.loads(line)
        section = row["section"]
        counts[section] += 1
        fields = json.loads(row["native_fields"]) if row.get("native_fields") else {}
        field_counts.update((section, key) for key in fields)
        extra_tokens[section] += len(row["tokens"]) - len(fields)

    if counts != {k: v["source_records"] for k, v in summary["coverage"].items()}:
        raise ValueError("Coverage manifest differs from the exported source records")
    terms = registry(gen)
    declared = set()
    duplicates = {}
    for path in schema_dir.glob("*.yaml"):
        doc = yaml.safe_load(path.read_text())
        if path.stem == "distribution":
            continue
        for category in ("classes", "slots", "enums", "types"):
            for name in doc.get(category, {}) or {}:
                key = category + "." + name
                if key in declared:
                    duplicates[key] = path.name
                declared.add(key)
    if duplicates:
        raise ValueError(f"Duplicate declarations: {duplicates}")
    hc = json.loads((ROOT / "vendor/baselines/hydrocouple.json").read_text())
    standards = json.loads((ROOT / "vendor/baselines/standards.json").read_text())
    wsys_classes = set(gen.sv.all_classes())
    resolutions = []
    for subject, predicate, target, source in terms["mappings"]:
        if target.startswith("hc:"):
            resolved = target[3:] in hc["terms"]
        elif target.startswith("wsys:"):
            resolved = target[5:] in wsys_classes
        elif target.partition(":")[0] in standards:
            prefix, _, local = target.partition(":")
            resolved = True if local in standards[prefix]["terms"] else None
        else:
            resolved = None
        if resolved is False:
            raise ValueError(f"Unresolved mapping target {target}")
        resolutions.append(
            {
                "subject": subject,
                "predicate": predicate,
                "target": target,
                "declared_in": source,
                "term_resolution": "verified" if resolved else "external_review",
            }
        )
    members = []
    for m in catalog["members"]:
        path = m["path"]
        members.append(
            {
                "path": path,
                "form": m["form"],
                "native_unit": m.get("units"),
                "declared": path in sites,
                "bindings": sites.get(path, []),
                "exported_observations": coverage[path],
                "status": "observed_in_fixture"
                if coverage[path]
                else "declared_untested"
                if path in sites
                else "operation"
                if m["form"] == "method"
                else "unbound",
                "read_failures": summary.get(
                    "catalog_properties_not_readable_with_this_engine", {}
                ).get(path, 0),
            }
        )
    epa = json.loads((ROOT / "vendor/baselines/epa-5.2.4.json").read_text())
    inp = [
        {
            "section_prefix": prefix,
            "fixture_records": sum(
                n for k, n in counts.items() if k.startswith(prefix)
            ),
            "configured_sections": [
                k for k in profile()["sections"] if k.startswith(prefix)
            ],
        }
        for prefix in epa["section_prefixes"]
    ]
    rule_gen = Assertions(str(schema_dir / "wsys.yaml"), "water_core")
    rule_gen.build_rules()
    field_evidence = [
        {
            "section": section,
            "field": field,
            "source_records_with_field": n,
            "preservation": "named native value and original token",
            "fixture": summary["inp_sha256"],
        }
        for (section, field), n in sorted(field_counts.items())
    ]
    result = {
        "named_input_fields": field_evidence,
        "source_tokens_without_field_label": dict(extra_tokens),
        "rule_count": len(rule_gen.rules),
        "term_count": len(terms["terms"]),
        "class_column_bindings": len(terms["bindings"]),
        "api_coverage_totals": dict(Counter(item["status"] for item in members)),
        "ontology_version": gen.sv.schema.version,
        "engine_catalog_sha256": digest,
        "engine_version_tested": summary["engine"],
        "profile": summary["mapping_release"],
        "epa_registry": inp,
        "source_sections": summary["coverage"],
        "api_members": members,
        "mappings": resolutions,
        "duplicate_declarations": duplicates,
        "publication": {
            "local": "generated",
            "databricks": "requires workspace verification",
        },
    }
    if compare_catalog:
        other = json.loads(Path(compare_catalog).read_text())
        before = {m["path"]: m for m in catalog["members"]}
        after = {m["path"]: m for m in other["members"]}
        result["engine_diff"] = {
            "added": sorted(after.keys() - before.keys()),
            "removed": sorted(before.keys() - after.keys()),
            "changed": sorted(
                k for k in before.keys() & after.keys() if before[k] != after[k]
            ),
        }
    return result


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("rows", type=Path)
    cli.add_argument("--compare-catalog", type=Path)
    cli.add_argument("-o", "--output", type=Path, required=True)
    args = cli.parse_args()
    data = inventory(args.rows, args.compare_catalog)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, indent=2) + "\n")
