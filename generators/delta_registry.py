"""Materialize the same versioned terminology records used by SQLite and the glossary."""

import hashlib
import json

from .linkml_to_delta import q
from .semantic_registry import registry


def emit(generator):
    data = registry(generator)
    records = {k: data[k] for k in ("terms", "labels", "mappings", "bindings")}
    records["mappings"] = data["mapping_assertions"]
    records["decisions"] = data["governance"]["decisions"]

    def encode(value):
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )

    release = hashlib.sha256(encode(data).encode()).hexdigest()
    table = generator.fq("reference", "terminology_record")
    sql = [
        f"""CREATE TABLE IF NOT EXISTS {table} (
        release_sha256 STRING NOT NULL, profile STRING NOT NULL, ontology_version STRING NOT NULL,
        record_kind STRING NOT NULL, record_sha256 STRING NOT NULL, payload STRING NOT NULL
        ) USING DELTA;"""
    ]
    for kind, items in records.items():
        for offset in range(0, len(items), 100):
            batch = []
            for row in items[offset : offset + 100]:
                if kind == "labels":
                    fields = ("iri", "language", "role", "label", "scheme")
                    row = dict(zip(fields, row))
                payload = encode(row)
                digest = hashlib.sha256(payload.encode()).hexdigest()
                batch.append(f"({q(digest)}, {q(payload)})")
            values = ",\n".join(batch)
            sql.append(f"""MERGE INTO {table} AS target USING (VALUES {values}) AS src(record_sha256,payload)
                ON target.release_sha256={q(release)} AND target.record_kind={q(kind)}
                AND target.record_sha256=src.record_sha256
                WHEN NOT MATCHED THEN INSERT (release_sha256,profile,ontology_version,record_kind,record_sha256,payload)
                VALUES ({q(release)},{q(data["profile"])},{q(str(data["version"]))},{q(kind)},src.record_sha256,src.payload);""")
    return "\n".join(sql) + "\n"
