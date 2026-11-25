"""Rows for the ontology's tables and the JSON Lines files that hold them. Every loader writes through this."""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from generators.linkml_to_delta import DeltaGenerator, snake
from loaders.source_records import file_digest

HERE = Path(__file__).resolve().parent.parent


class Rows:
    def __init__(self):
        self.d = DeltaGenerator(str(HERE / "schema" / "wsys.yaml"), "water_core")
        self.sv = self.d.sv
        self.rows: dict[str, list[dict]] = defaultdict(list)

    def table(self, cls: str) -> str:
        return f"{self.d.module_of(cls)}.{snake(cls)}"

    def class_iri(self, cls: str) -> str:
        return self.sv.get_uri(self.sv.get_class(cls), expand=True)

    def add(self, cls: str, row: dict) -> None:
        row.setdefault("class_iri", self.class_iri(cls))
        self.rows[self.table(cls)].append({k: v for k, v in row.items() if v is not None})

    def write_tables(self, folder: Path) -> dict[str, str]:
        """Write one JSON Lines file per table into folder and return each file's SHA-256."""
        digests = {}
        for table, rows in sorted(self.rows.items()):
            target = folder / f"{table}.jsonl"
            with target.open("w", encoding="utf-8") as stream:
                for row in rows:
                    stream.write(json.dumps(row, sort_keys=True, allow_nan=False) + "\n")
            digests[target.name] = file_digest(target)
        return digests
