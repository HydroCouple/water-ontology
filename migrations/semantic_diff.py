"""Compare the public properties and code identifiers of two release schemas."""

import argparse
import json
from pathlib import Path

from generators.linkml_to_delta import DeltaGenerator
from generators.profile import normalize_code


def compare(old_schema, new_schema):
    old = DeltaGenerator(str(old_schema), "water_core")
    new = DeltaGenerator(str(new_schema), "water_core")
    changes = []
    for cls in sorted(old.sv.all_classes()):
        if cls not in new.sv.all_classes():
            changes.append({"kind": "class", "scope": cls, "status": "removed"})
            continue
        current = {s.name: s for s in new.sv.class_induced_slots(cls)}
        for previous in old.sv.class_induced_slots(cls):
            slot = current.get(previous.name)

            def signature(view, s):
                return {
                    "iri": view.get_uri(s, expand=True),
                    "range": str(s.range),
                    "unit": str(s.unit.ucum_code)
                    if s.unit and s.unit.ucum_code
                    else None,
                    "many": bool(s.multivalued),
                    "required": bool(s.required),
                }

            a = signature(old.sv, previous)
            b = signature(new.sv, slot) if slot else None
            if a != b:
                changes.append(
                    {
                        "kind": "property",
                        "scope": cls,
                        "column": previous.name,
                        "old": a,
                        "new": b,
                    }
                )
    # Historical root imports shadowed same-named topology enums. Resolve each
    # declaration in its own module so old spellings and namespaces survive.
    for module in old.sv.schema_map.values():
        if module.name == "types":
            continue
        for name, enum in module.enums.items():
            replacement = "GageRainType" if name == "RainFormat" else name
            target = new.sv.all_enums().get(replacement)
            prefix = module.prefixes[module.default_prefix].prefix_reference
            for token in enum.permissible_values:
                code = normalize_code(replacement, token)
                a = str(prefix) + str(name) + "#" + str(token)
                b = (
                    new.sv.get_uri(target, expand=True) + "#" + code
                    if target and code in target.permissible_values
                    else None
                )
                if a != b:
                    changes.append(
                        {
                            "kind": "code",
                            "scope": str(module.id),
                            "scheme": str(name),
                            "old": a,
                            "new": b,
                        }
                    )
    return changes


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("old", type=Path)
    parser.add_argument("new", type=Path)
    parser.add_argument("out", type=Path)
    args = parser.parse_args()
    args.out.write_text(json.dumps(compare(args.old, args.new), indent=2) + "\n")
