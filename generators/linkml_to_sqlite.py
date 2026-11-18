"""Build a portable, validated SQLite snapshot from the release schema and an exported row set."""

from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile

import sqlglot

from loaders.source_records import file_digest
from .linkml_to_assertions import Assertions
from .linkml_to_delta import DeltaGenerator, snake
from .profile import ROOT
from .semantic_registry import registry


def identifier(name):
    return '"' + name.replace('"', '""') + '"'


def literal(value):
    return "'" + str(value).replace("'", "''") + "'"


class SQLiteRules(Assertions):
    def t(self, schema, table):
        return schema + "__" + table


def column(d, slot):
    name = identifier(slot.name)
    source_type, kind = d.sql_type(slot)
    constraints = []
    if source_type.startswith(("STRUCT<", "ARRAY<")):
        kind_name = "object" if source_type.startswith("STRUCT<") else "array"
        sql_type = "TEXT"
        constraints.append(
            f"CHECK ({name} IS NULL OR (json_valid({name}) AND json_type({name})='{kind_name}'))"
        )
    else:
        sql_type = {
            "STRING": "TEXT",
            "BIGINT": "INTEGER",
            "DOUBLE": "REAL",
            "BOOLEAN": "INTEGER",
            "DATE": "TEXT",
            "TIMESTAMP": "TEXT",
            "DECIMAL(18,6)": "TEXT",
        }[source_type]
        if source_type == "BOOLEAN":
            constraints.append(f"CHECK ({name} IN (0,1))")
        if kind == "enum":
            codes = ", ".join(
                literal(code) for code in d.sv.get_enum(slot.range).permissible_values
            )
            constraints.append(f"CHECK ({name} IN ({codes}))")
    for bound, operator in [(slot.minimum_value, ">="), (slot.maximum_value, "<=")]:
        if bound is not None:
            constraints.append(f"CHECK ({name} {operator} {float(bound)})")
    if slot.required or slot.identifier:
        constraints.append("NOT NULL")
    return " ".join([name, sql_type, *constraints])


def create_schema(db, schema):
    d = DeltaGenerator(str(schema), "water_core")
    classes = d.concrete_classes()
    entities = []
    tables = {}
    for cls in classes:
        table = d.module_of(cls) + "__" + snake(cls)
        slots = d.ordered_slots(cls)
        names = {s.name for s in slots}
        tables[table] = names
        db.execute(
            f"CREATE TABLE {identifier(table)} ({', '.join(column(d, s) for s in slots)}) STRICT"
        )
        view = identifier(table)
        if "valid_to" in names:
            view = identifier(table + "_current")
            db.execute(
                f"CREATE VIEW {view} AS SELECT * FROM {identifier(table)} WHERE valid_to IS NULL"
            )
        if {"iri", "class_iri"} <= names:
            db.execute(
                f"CREATE UNIQUE INDEX {identifier(table + '_current_id')} ON {identifier(table)}(iri)"
                + (" WHERE valid_to IS NULL" if "valid_to" in names else "")
            )
            entities.append(
                f"SELECT iri,class_iri,{literal(table)} AS table_name FROM {view}"
            )
    db.execute("CREATE VIEW reference__entity AS " + " UNION ALL ".join(entities))
    db.execute(
        "CREATE INDEX topology__from_relation ON topology__edge(from_iri,relation)"
    )
    db.execute("CREATE INDEX topology__to_relation ON topology__edge(to_iri,relation)")
    for cls in ("Asset", "SwmmElement"):
        names = ", ".join(identifier(s.name) for s in d.sv.class_induced_slots(cls))
        target = d.module_of(cls) + "__" + snake(cls)
        members = [
            d.module_of(c) + "__" + snake(c)
            for c in classes
            if cls in d.sv.class_ancestors(c)
        ]
        db.execute(
            f"CREATE VIEW {identifier(target)} AS "
            + " UNION ALL ".join(
                f"SELECT {names} FROM {identifier(member)}" for member in members
            )
        )
        db.execute(
            f"CREATE VIEW {identifier(target + '_current')} AS SELECT * FROM {identifier(target)} WHERE valid_to IS NULL"
        )
    db.executescript("""
CREATE TABLE reference__concept(iri TEXT PRIMARY KEY, name TEXT, kind TEXT, definition TEXT) STRICT;
CREATE TABLE reference__label(iri TEXT NOT NULL REFERENCES reference__concept, language TEXT NOT NULL,
    role TEXT NOT NULL, label TEXT NOT NULL, scheme TEXT NOT NULL, PRIMARY KEY(iri,language,role,label,scheme)) STRICT;
CREATE UNIQUE INDEX reference__one_preferred ON reference__label(iri,language) WHERE role='preferred';
CREATE INDEX reference__label_lookup ON reference__label(label COLLATE NOCASE);
CREATE TABLE reference__mapping(subject TEXT REFERENCES reference__concept, predicate TEXT, object TEXT, source TEXT,
    source_version TEXT, target_version TEXT, owner TEXT NOT NULL, status TEXT NOT NULL, reviewer TEXT,
    decision_date TEXT, evidence TEXT, replacement TEXT, PRIMARY KEY(subject,predicate,object,source)) STRICT;
CREATE TABLE reference__binding(iri TEXT REFERENCES reference__concept, class_iri TEXT REFERENCES reference__concept,
    table_name TEXT, column_name TEXT, value_range TEXT, unit TEXT, catalog_path TEXT, PRIMARY KEY(table_name,column_name)) STRICT;
CREATE TABLE reference__class_closure(class_iri TEXT, ancestor_iri TEXT, PRIMARY KEY(class_iri,ancestor_iri)) STRICT;
CREATE TABLE reference__release(name TEXT PRIMARY KEY, value TEXT NOT NULL) STRICT;
CREATE TABLE reference__rule(rule_id TEXT PRIMARY KEY,severity TEXT,description TEXT) STRICT;
""")
    data = registry(d)
    db.execute(
        "INSERT INTO reference__release VALUES(?,?)",
        ("mapping_decisions", json.dumps(data["governance"], sort_keys=True)),
    )
    db.executemany(
        "INSERT INTO reference__concept VALUES(:iri,:name,:kind,:definition)",
        data["terms"],
    )
    db.executemany("INSERT INTO reference__label VALUES(?,?,?,?,?)", data["labels"])
    db.executemany(
        "INSERT INTO reference__mapping VALUES(:subject,:predicate,:object,:source,:source_version,:target_version,:owner,:status,:reviewer,:decision_date,:evidence,:replacement)",
        data["mapping_assertions"],
    )
    db.executemany(
        "INSERT INTO reference__binding VALUES(:iri,:class_iri,:table,:column,:range,:unit,:catalog_path)",
        data["bindings"],
    )
    rules = SQLiteRules(str(schema), "water_core")
    for cls in d.sv.all_classes():
        db.executemany(
            "INSERT INTO reference__class_closure VALUES(?,?)",
            [(rules.iri(cls), rules.iri(a)) for a in d.sv.class_ancestors(cls)],
        )
    rules.build_rules()
    views = []
    for rid, severity, desc, select in rules.rules:
        view = identifier("reference__violation_" + rid)
        sql = sqlglot.transpile(select, read="databricks", write="sqlite")[0]
        db.execute(f"CREATE VIEW {view} AS {sql}")
        db.execute("INSERT INTO reference__rule VALUES(?,?,?)", (rid, severity, desc))
        views.append(
            f"SELECT {literal(rid)} AS rule_id, {literal(severity)} AS severity, focus_iri,message FROM {view}"
        )
    db.execute("CREATE VIEW reference__violations AS " + " UNION ALL ".join(views))
    db.execute("""CREATE VIEW analysis__result_lineage AS
        SELECT o.iri AS observation_iri,o.entity_iri,o.property_iri,o.value,o.unit,o.source_record,
               o.interval_start,o.interval_end,o.native_unit,o.conversion_factor,a.iri AS attempt_iri,
               r.iri AS run_iri,rv.iri AS revision_iri,rv.input_sha256,rv.bundle_sha256,
               rv.mapping_release,a.was_associated_with AS agent_iri,a.distributions AS output_artifacts,
               o.ingestion_activity,rv.dc_rights AS input_rights,rv.dc_license AS input_license,
               rec.line_number AS source_line,rec.section AS source_section,artifact.artifact_sha256 AS output_sha256
        FROM observation__observation o JOIN analysis__attempt a ON a.iri=o.was_generated_by
        JOIN analysis__run r ON r.iri=a.of_run JOIN analysis__revision rv ON rv.iri=r.of_revision
        LEFT JOIN analysis__input_record rec ON rec.iri=o.source_record AND rec.of_revision=rv.iri
        LEFT JOIN analysis__result_artifact artifact ON artifact.iri=a.iri || '/output'""")
    return tables


def build(
    rows, output, schema=ROOT / "schema/wsys.yaml", *, geopackage=None, simulation=None
):
    """Publish a new snapshot atomically. An existing file is never overwritten."""
    rows, output = Path(rows), Path(output)
    summary = json.loads((rows / "SUMMARY.json").read_text())
    if bool(geopackage) != bool(simulation):
        raise ValueError("GeoPackage and simulation must be supplied together")
    if output.exists():
        raise FileExistsError(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as tmp:
        staging = Path(tmp) / "snapshot.sqlite"
        with closing(sqlite3.connect(staging)) as db:
            db.execute("PRAGMA foreign_keys=ON")
            tables = create_schema(db, schema)
            counts = {}
            for path in sorted(rows.glob("*.jsonl")):
                table = path.stem.replace(".", "__")
                if table not in tables:
                    raise ValueError(f"Unknown export table {path.stem}")
                count = 0
                digest = hashlib.sha256()
                with path.open("rb") as stream:
                    for count, line in enumerate(stream, 1):
                        digest.update(line)
                        row = json.loads(line)
                        if not row or set(row) - tables[table]:
                            raise ValueError(
                                f"Unexpected columns in {path.name} line {count}"
                            )
                        names = ",".join(map(identifier, row))
                        values = [
                            json.dumps(v, allow_nan=False)
                            if isinstance(v, (list, dict))
                            else v
                            for v in row.values()
                        ]
                        db.execute(
                            f"INSERT INTO {identifier(table)} ({names}) VALUES ({','.join('?' for _ in row)})",
                            values,
                        )
                if "row_sha256" in summary and digest.hexdigest() != summary[
                    "row_sha256"
                ].get(path.name):
                    raise ValueError(f"Row digest mismatch for {path.name}")
                counts[path.stem] = count
            if counts != summary["rows"]:
                raise ValueError("Export row counts differ from SUMMARY.json")
            for name in summary.get("artifacts", []):
                if (
                    Path(name).name != name
                    or file_digest(rows / "artifacts" / name) != name.split(".")[0]
                ):
                    raise ValueError(f"Artifact identity mismatch: {name}")
            if geopackage:
                from loaders.geopackage import bind

                bind(db, geopackage, simulation, summary["revision"])
            problems = db.execute(
                "SELECT rule_id,focus_iri FROM reference__violations WHERE severity='ERROR' LIMIT 5"
            ).fetchall()
            if problems:
                raise ValueError(f"Ontology violations: {problems}")
            db.executemany(
                "INSERT INTO reference__release VALUES(?,?)",
                [
                    (
                        "ontology_version",
                        DeltaGenerator(str(schema), "water_core").sv.schema.version,
                    ),
                    ("input_summary", json.dumps(summary, sort_keys=True)),
                    ("catalog_lock", (ROOT / "schema/model.lock.json").read_text()),
                    ("schema_digest", schema_digest(Path(schema))),
                ],
            )
            db.commit()
            if db.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise ValueError("SQLite integrity check failed")
        os.link(staging, output)


def schema_digest(schema):
    digest = hashlib.sha256()
    for source in sorted(schema.parent.rglob("*.yaml")):
        digest.update(str(source.relative_to(schema.parent)).encode() + b"\0")
        digest.update(hashlib.sha256(source.read_bytes()).digest())
    return digest.hexdigest()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rows", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--geopackage", type=Path)
    parser.add_argument("--simulation")
    args = parser.parse_args()
    build(
        args.rows, args.output, geopackage=args.geopackage, simulation=args.simulation
    )
