"""Read the source evidence accompanying an engine import. The engine interprets the model."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from generators.profile import profile

TOKEN = re.compile(r'"(?:[^"]|"")*"|[^\s;"]+|;.*')


@dataclass(frozen=True)
class Record:
    section: str
    line: int
    raw: str
    tokens: list[str]

    def named_fields(self, spec=None):
        spec = (spec or profile())["sections"].get(self.section, {})
        names = spec.get("fields", [])
        if self.section == "TIMESERIES" and len(self.tokens) == 3:
            names = ["series", "time", "value"]
        if (
            self.section == "TIMESERIES"
            and self.tokens[1:2]
            and self.tokens[1].upper() == "FILE"
        ):
            names = ["series", "source_kind", "path"]
        return dict(zip(names, self.tokens))

    def objects(self, spec=None):
        specs = (spec or profile())["sections"].get(self.section, {}).get("refs", {})
        return [
            (kind, self.tokens[int(index)])
            for index, kind in specs.items()
            if int(index) < len(self.tokens) and self.tokens[int(index)] != "*"
        ]


def read_records(path: Path):
    data = path.read_bytes()
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    section = ""
    records = []
    for number, raw in enumerate(text.splitlines(), 1):
        tokens = []
        for match in TOKEN.finditer(raw):
            token = match.group()
            if token.startswith(";"):
                break
            tokens.append(
                token[1:-1].replace('""', '"') if token.startswith('"') else token
            )
        if not tokens:
            continue
        if tokens[0].startswith("[") and tokens[0].endswith("]"):
            section = tokens[0][1:-1].upper()
        elif section:
            records.append(Record(section, number, raw, tokens))
    return records


def dependencies(records):
    for r in records:
        t = r.tokens
        upper = [v.upper() for v in t]
        if r.section == "FILES" and len(t) >= 3 and upper[0] == "USE":
            yield t[2]
        elif r.section == "TEMPERATURE" and len(t) >= 2 and upper[0] == "FILE":
            yield t[1]
        elif r.section == "RAINGAGES" and len(t) >= 6 and upper[4] == "FILE":
            yield t[5]
        elif r.section == "TIMESERIES" and len(t) >= 3 and upper[1] == "FILE":
            yield t[2]


def file_digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def input_bundle(path, records):
    files = [("primary", path.name, file_digest(path))]
    for name in sorted(set(dependencies(records))):
        target = path.parent / name
        if not target.is_file():
            raise FileNotFoundError(
                f"SWMM input dependency {name!r} referenced by {path.name} is missing"
            )
        files.append(("dependency", name, file_digest(target)))
    digest = hashlib.sha256(
        json.dumps(files, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()
    return files, digest
