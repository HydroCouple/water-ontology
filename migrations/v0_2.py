"""Build scoped identity and property crosswalks from a legacy export and its re-import."""

import argparse
from collections import defaultdict
import json
from pathlib import Path


def rows(folder, table):
    file = folder / (table + ".jsonl")
    return (
        [json.loads(line) for line in file.read_text().splitlines()]
        if file.exists()
        else []
    )


def crosswalk(old, new):
    before = json.loads((old / "SUMMARY.json").read_text())
    after = json.loads((new / "SUMMARY.json").read_text())
    if (before["inp_sha256"], before["system"]) != (
        after["inp_sha256"],
        after["system"],
    ):
        raise ValueError(
            "Source input and utility/system must match for an identity migration"
        )
    replacements = defaultdict(set)
    for file in sorted(old.glob("*.jsonl")):
        table = file.stem
        if not table.startswith(("model.", "topology.")) or table == "topology.edge":
            continue
        key = "element_id" if table.startswith("model.") else "label"
        targets = {row[key]: row["iri"] for row in rows(new, table) if key in row}
        for row in rows(old, table):
            if row.get(key) in targets:
                replacements[row["iri"]].add((table, targets[row[key]]))
    replacements[before["revision"]].add(("analysis.revision", after["revision"]))
    return {
        "from_revision": before["revision"],
        "to_revision": after["revision"],
        "input_sha256": after["inp_sha256"],
        "identities": [
            {
                "legacy_iri": old_id,
                "scope": table,
                "iri": iri,
                "requires_scope": len(found) > 1,
            }
            for old_id, found in sorted(replacements.items())
            for table, iri in sorted(found)
        ],
        "execution_policy": "Keep prior runs, attempts and observations as historical occurrences. Re-execution has a new identity.",
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("old", type=Path)
    p.add_argument("new", type=Path)
    p.add_argument("out", type=Path)
    a = p.parse_args()
    a.out.write_text(json.dumps(crosswalk(a.old, a.new), indent=2) + "\n")
