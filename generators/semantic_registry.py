"""Canonical glossary with physical bindings and typed, traceable mapping assertions."""

from .linkml_to_delta import snake
from .profile import ROOT, profile, annotation
from datetime import date
import yaml


def mapping_records(mappings, governance, version):
    keys = ("subject", "predicate", "object")
    reviews = {}
    for review in governance.get("assertion_reviews", []):
        key = tuple(review[k] for k in keys)
        if key in reviews:
            raise ValueError(f"Duplicate mapping review: {key}")
        if review.get("status") not in {
            "approved",
            "deprecated",
            "proposed_for_release",
        }:
            raise ValueError(f"Unknown mapping status for {key}")
        if review["status"] != "proposed_for_release":
            if not all(
                review.get(k)
                for k in ("reviewer", "decision_date", "evidence", "target_version")
            ):
                raise ValueError(f"Missing decision evidence for {key}")
            date.fromisoformat(str(review["decision_date"]))
        reviews[key] = review
    records = []
    seen = set()
    for subject, predicate, target, source in sorted(set(mappings)):
        key = (subject, predicate, target)
        seen.add(key)
        decision = reviews.get(key, {})
        records.append(
            {
                "subject": subject,
                "predicate": predicate,
                "object": target,
                "source": source,
                "source_version": version,
                "target_version": decision.get("target_version"),
                "owner": decision.get("owner", governance["owner"]),
                "status": decision.get("status", governance["review_status"]),
                "reviewer": decision.get("reviewer"),
                "decision_date": str(decision["decision_date"])
                if decision.get("decision_date")
                else None,
                "evidence": decision.get("evidence", source),
                "replacement": decision.get("replacement"),
            }
        )
    if reviews.keys() - seen:
        raise ValueError(
            f"Mapping reviews reference undeclared assertions: {reviews.keys() - seen}"
        )
    return records


def registry(d):
    sv = d.sv
    terms, bindings, labels, mappings = {}, [], [], []
    concrete = set(d.concrete_classes())
    profile_data = profile()
    for c in sv.all_classes().values():
        iri = sv.get_uri(c, expand=True)
        terms[iri] = {
            "iri": iri,
            "name": c.name,
            "kind": "class",
            "definition": c.description or "",
        }
        labels.append((iri, "en", "preferred", c.title or c.name, "LinkML"))
        for alias in c.aliases or ():
            labels.append((iri, "en", "alternative", str(alias), "LinkML"))
        for scheme, token in (
            profile_data.get("labels", {}).get(c.name, {}).get("notation", {}).items()
        ):
            labels.append((iri, "", "notation", token, scheme))
        for strength in ("exact", "close", "broad", "narrow", "related"):
            for target in getattr(c, strength + "_mappings", []) or ():
                mappings.append(
                    (iri, f"skos:{strength}Match", str(target), str(c.from_schema))
                )
        role = annotation(c, "represents_domain_class")
        if role:
            mappings.append(
                (iri, "wsys:representsDomainClass", role, profile_data["id"])
            )
        if c.name not in concrete:
            continue
        for slot in d.ordered_slots(c.name):
            sid = sv.get_uri(slot, expand=True)
            terms.setdefault(
                sid,
                {
                    "iri": sid,
                    "name": slot.name,
                    "kind": "property",
                    "definition": slot.description or "",
                },
            )
            preferred = profile_data.get("property_labels", {}).get(
                sid, slot.title or slot.name
            )
            labels.append((sid, "en", "preferred", preferred, "LinkML"))
            if slot.name != preferred:
                labels.append((sid, "en", "alternative", slot.name, "LinkML"))
            for alias in slot.aliases or ():
                labels.append((sid, "en", "alternative", str(alias), "LinkML"))
            for strength in ("exact", "close", "broad", "narrow", "related"):
                for target in getattr(slot, strength + "_mappings", []) or ():
                    mappings.append(
                        (
                            sid,
                            f"skos:{strength}Match",
                            str(target),
                            str(slot.from_schema),
                        )
                    )
            ann = annotation(slot, "catalog_path")
            bindings.append(
                {
                    "iri": sid,
                    "class_iri": iri,
                    "table": d.fq(d.module_of(c.name), snake(c.name)),
                    "column": slot.name,
                    "range": slot.range,
                    "unit": slot.unit.ucum_code if slot.unit else None,
                    "catalog_path": ann,
                }
            )
    for enum in sv.all_enums().values():
        scheme = sv.get_uri(enum, expand=True)
        for token, member in enum.permissible_values.items():
            iri = f"{scheme}#{token}"
            terms[iri] = {
                "iri": iri,
                "name": token,
                "kind": "value",
                "definition": member.description or "",
            }
            labels.append((iri, "", "notation", token, scheme))
        for alias, canonical in profile_data["codes"].get(enum.name, {}).items():
            if canonical not in enum.permissible_values:
                raise ValueError(f"Unknown target in {enum.name}: {canonical}")
            labels.append(
                (
                    f"{scheme}#{canonical}",
                    "",
                    "notation",
                    alias,
                    profile_data["id"] + "/" + enum.name,
                )
            )
    decisions = yaml.safe_load((ROOT / "schema/mappings/decisions.yaml").read_text())
    return {
        "governance": decisions,
        "profile": profile_data["id"],
        "version": sv.schema.version,
        "terms": list(terms.values()),
        "labels": sorted(set(labels)),
        "mappings": sorted(set(mappings)),
        "mapping_assertions": mapping_records(mappings, decisions, sv.schema.version),
        "bindings": bindings,
    }
