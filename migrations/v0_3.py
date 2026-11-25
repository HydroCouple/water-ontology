"""Migrate rows from 0.2 to 0.3: a Candidate's decisions and objectives become typed lists.

0.2 kept them as JSON strings: ``parameters`` ({dimension: value or [values]}) and
``objective_values`` ({objective: value}). 0.3 has ``decision_values`` (dimension, value per
component) and ``objective_values`` (name, value). 0.2 recorded no component labels and no
objective directions, so the migrated rows have neither. Every other table is copied as it is.

Usage:
    python -m migrations.v0_3 OLD_ROWS -o NEW_ROWS
"""

import argparse
import json
import shutil
from pathlib import Path

CANDIDATES = "analysis.candidate.jsonl"


def candidate(row: dict) -> dict:
    """One 0.2 candidate row as a 0.3 row."""
    out = {k: v for k, v in row.items() if k not in ("parameters", "objective_values")}
    if isinstance(row.get("parameters"), str):
        decisions = json.loads(row["parameters"])
        out["decision_values"] = [
            {"dimension": key, "value": float(v)}
            for key, values in decisions.items()
            for v in (values if isinstance(values, list) else [values])
        ]
    if isinstance(row.get("objective_values"), str):
        out["objective_values"] = [
            {"name": name, "value": float(v)}
            for name, v in json.loads(row["objective_values"]).items()
        ]
    else:
        out["objective_values"] = row.get("objective_values")
    return {k: v for k, v in out.items() if v is not None}


def migrate(old: Path, new: Path) -> dict:
    if new.exists() and any(new.iterdir()):
        raise FileExistsError(f"{new} is not empty; migrate into a new folder")
    new.mkdir(parents=True, exist_ok=True)
    changed = 0
    for path in sorted(old.iterdir()):
        target = new / path.name
        if path.is_dir():
            shutil.copytree(path, target)
        elif path.name == CANDIDATES:
            rows = [candidate(json.loads(line)) for line in path.read_text().splitlines()]
            changed = len(rows)
            target.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
        else:
            shutil.copyfile(path, target)
    return {"candidates": changed}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("old", type=Path)
    ap.add_argument("-o", "--out", type=Path, required=True)
    a = ap.parse_args()
    print(json.dumps(migrate(a.old, a.out)))


if __name__ == "__main__":
    main()
