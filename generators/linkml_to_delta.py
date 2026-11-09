"""
LinkML -> Databricks Delta / Unity Catalog.

Reads the ontology (a LinkML module and everything it imports) and emits SQL
that materialises it in Unity Catalog:

  1. reference tables for every enum            -> <catalog>.reference.<enum_snake>
  2. the class hierarchy table                  -> <catalog>.reference.asset_class
  3. one Delta table per concrete leaf class    -> <catalog>.<module>.<class_snake>
     (inherited slots flattened, descriptions as COMMENTs, units as tags)
  4. a *_current view per table (SCD2 filter)
  5. an asset_current union view over all Asset descendants

Mapping rules live in ONE place (this file) so the physical model can never
drift from the ontology by hand-editing.

Usage:
    python -m generators.linkml_to_delta schema/topology.yaml --catalog water_core -o dist/delta
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

from linkml_runtime.utils.schemaview import SchemaView

# --- type mapping ----------------------------------------------------------
LINKML_TO_SQL = {
    "string": "STRING", "uriorcurie": "STRING", "uri": "STRING", "curie": "STRING",
    "IRI": "STRING", "WKT": "STRING",
    "integer": "BIGINT", "float": "DOUBLE", "double": "DOUBLE", "decimal": "DECIMAL(18,6)",
    "boolean": "BOOLEAN", "date": "DATE", "datetime": "TIMESTAMP", "time": "STRING",
}

# UCUM -> QUDT unit IRI. Extend as slots acquire units; CI fails on a missing one.
UCUM_TO_QUDT = {
    "m": "unit:M", "mm": "unit:MilliM", "km": "unit:KiloM", "m2": "unit:M2", "ha": "unit:HA", "mm/h": "unit:MilliM-PER-HR",
    "m3": "unit:M3", "m3/h": "unit:M3-PER-HR", "m3/s": "unit:M3-PER-SEC", "L/s": "unit:L-PER-SEC",
    "kW": "unit:KiloW", "kW.h": "unit:KiloW-HR", "kPa": "unit:KiloPA", "bar": "unit:BAR",
    "mg/L": "unit:MilliGM-PER-L", "Cel": "unit:DEG_C", "NTU": "unit:NTU", "[pH]": "unit:PH",
    "s": "unit:SEC", "min": "unit:MIN", "h": "unit:HR", "d": "unit:DAY", "%": "unit:PERCENT",
}

# Which Unity Catalog schema each LinkML module lands in.
MODULE_TO_SCHEMA = {
    "wsys-core": "reference",      # Utility etc.
    "wsys-topology": "topology",
    "wsys-observation": "observation",
    "wsys-event": "event",
    "wsys-operations": "operations",
    "wsys-analysis": "analysis",
    "wsys-analysis-platform": "analysis",
    "wsys-coupling": "coupling",
    "wsys-model": "model",
    "wsys-ml": "ml",
    "wsys-distribution": "distribution",
}


def snake(name: str) -> str:
    """CamelCase -> snake_case, keeping acronyms together: MLModelVersion -> ml_model_version, SwmmNodeStats -> swmm_node_stats."""
    s = re.sub(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "_", name)
    s = s.replace("XSect", "Xsect")   # SWMM spelling: xsect_shape, not x_sect_shape
    s = re.sub(r"(?<=[A-Za-z])(?=\d)(?!\d*$)", "_", s)                        # digits inside a name get a separator
    return re.sub(r"__+", "_", s).lower()


def q(s: str | None) -> str:
    """SQL string literal."""
    return "'" + (s or "").replace("'", "''").replace("\n", " ").strip() + "'"


class DeltaGenerator:
    def __init__(self, schema_path: str, catalog: str):
        self.sv = SchemaView(schema_path)
        self.catalog = catalog
        self.errors: list[str] = []

    # -- helpers -----------------------------------------------------------
    def module_of(self, cls_name: str) -> str:
        cls = self.sv.get_class(cls_name)
        schema = next((sc for sc in self.sv.schema_map.values() if sc.id == cls.from_schema), None)
        name = schema.name if schema else self.sv.schema.name
        return MODULE_TO_SCHEMA.get(name, snake(name.replace("wsys-", "")))

    def fq(self, schema: str, table: str) -> str:
        return f"{self.catalog}.{schema}.{table}"

    def has_identifier(self, cls_name: str) -> bool:
        return any(s.identifier for s in self.sv.class_induced_slots(cls_name))

    def inlined_targets(self) -> set[str]:
        """Classes only ever used as inlined component values (rendered as STRUCT, never as tables)."""
        out = set()
        for cls in self.sv.all_classes():
            for s in self.sv.class_induced_slots(cls):
                if (s.inlined or s.inlined_as_list) and s.range in self.sv.all_classes() and not self.has_identifier(s.range):
                    out.add(s.range)
        return out

    def concrete_classes(self) -> list[str]:
        inl = self.inlined_targets()
        return [c.name for c in self.sv.all_classes().values()
                if not c.abstract and not c.mixin and c.name != "Entity" and c.name not in inl]

    def struct_type(self, cls_name: str) -> str:
        """A component view (inlined, no identifier) as a Delta STRUCT."""
        fields = []
        for s in self.sv.class_induced_slots(cls_name):
            t, _ = self.sql_type(s)
            fields.append(f"{s.name}: {t} COMMENT {q((s.description or '')[:200])}")
        return "STRUCT<" + ", ".join(fields) + ">"

    def ordered_slots(self, cls_name: str):
        """Induced slots ordered root-first: Entity's identity columns, then each ancestor's, then the leaf's."""
        induced = {s.name: s for s in self.sv.class_induced_slots(cls_name)}
        seen, ordered = set(), []
        for anc in reversed(self.sv.class_ancestors(cls_name)):
            for name in (self.sv.get_class(anc).slots or []):
                if name in induced and name not in seen:
                    ordered.append(induced[name]); seen.add(name)
        ordered += [s for n, s in induced.items() if n not in seen]
        return ordered

    def sql_type(self, slot) -> tuple[str, str | None]:
        """Return (sql_type, kind) where kind is 'enum' | 'class' | 'type'."""
        rng = slot.range or self.sv.schema.default_range or "string"
        if rng in self.sv.all_enums():
            base, kind = "STRING", "enum"
        elif rng in self.sv.all_classes():
            if slot.inlined and not self.has_identifier(rng):
                base, kind = self.struct_type(rng), "struct"
            else:
                base, kind = "STRING", "class"          # FK by IRI
        else:
            t = self.sv.get_type(rng)
            root = t.typeof or t.name if t else rng
            # walk typeof chain to a builtin
            while t and t.typeof and t.typeof in self.sv.all_types() and root not in LINKML_TO_SQL:
                t = self.sv.get_type(t.typeof); root = t.name
            base, kind = LINKML_TO_SQL.get(rng, LINKML_TO_SQL.get(root, "STRING")), "type"
        return (f"ARRAY<{base}>" if slot.multivalued else base), kind

    def unit_tag(self, slot) -> str | None:
        if not slot.unit or not slot.unit.ucum_code:
            return None
        qudt = UCUM_TO_QUDT.get(slot.unit.ucum_code)
        if not qudt:
            self.errors.append(f"no QUDT mapping for UCUM '{slot.unit.ucum_code}' on slot {slot.name}")
        return qudt

    # -- emitters ----------------------------------------------------------
    def emit_enums(self) -> str:
        out = [f"CREATE SCHEMA IF NOT EXISTS {self.catalog}.reference;"]
        for e in self.sv.all_enums().values():
            t = self.fq("reference", snake(e.name))
            out.append(f"\nCREATE TABLE IF NOT EXISTS {t} (\n  code STRING NOT NULL,\n  label STRING,\n  definition STRING,\n  CONSTRAINT pk_{snake(e.name)} PRIMARY KEY (code)\n) COMMENT {q(e.description)}\nTBLPROPERTIES ('wsys_enum' = {q(self.sv.get_uri(e, expand=True))});")
            rows = ",\n".join(f"  ({q(pv.text)}, {q(pv.title or pv.text)}, {q(pv.description)})" for pv in e.permissible_values.values())
            out.append(f"MERGE INTO {t} AS tgt USING (VALUES\n{rows}\n) AS src(code, label, definition) ON tgt.code = src.code\nWHEN MATCHED THEN UPDATE SET * WHEN NOT MATCHED THEN INSERT *;")
        return "\n".join(out) + "\n"

    def emit_class_table(self) -> str:
        t = self.fq("reference", "asset_class")
        out = [f"CREATE TABLE IF NOT EXISTS {t} (\n  iri STRING NOT NULL,\n  name STRING NOT NULL,\n  parent_iri STRING,\n  label STRING,\n  definition STRING,\n  is_abstract BOOLEAN,\n  table_name STRING,\n  exact_mappings ARRAY<STRING>,\n  CONSTRAINT pk_asset_class PRIMARY KEY (iri)\n) COMMENT 'Every class in the ontology with its parent; join on class_iri to answer hierarchy questions without code.';"]
        rows = []
        for c in self.sv.all_classes().values():
            iri = self.sv.get_uri(c, expand=True)
            parent = self.sv.get_uri(self.sv.get_class(c.is_a), expand=True) if c.is_a else None
            table = None if (c.abstract or c.name == "Entity") else self.fq(self.module_of(c.name), snake(c.name))
            maps = "array(" + ", ".join(q(m) for m in (c.exact_mappings or [])) + ")"
            rows.append(f"  ({q(iri)}, {q(c.name)}, {q(parent) if parent else 'NULL'}, {q(c.title or c.name)}, {q(c.description)}, {str(bool(c.abstract)).lower()}, {q(table) if table else 'NULL'}, {maps})")
        out.append(f"MERGE INTO {t} AS tgt USING (VALUES\n" + ",\n".join(rows) + "\n) AS src(iri, name, parent_iri, label, definition, is_abstract, table_name, exact_mappings) ON tgt.iri = src.iri\nWHEN MATCHED THEN UPDATE SET * WHEN NOT MATCHED THEN INSERT *;")
        return "\n".join(out) + "\n"

    def emit_table(self, cls_name: str) -> str:
        cls = self.sv.get_class(cls_name)
        schema = self.module_of(cls_name)
        t = self.fq(schema, snake(cls_name))
        cols, tags, checks, fks = [], [], [], []
        for s in self.ordered_slots(cls_name):
            sql_t, kind = self.sql_type(s)
            nn = " NOT NULL" if (s.required or s.identifier) else ""
            comment = (s.description or "").strip()
            qudt = self.unit_tag(s)
            if s.unit and s.unit.ucum_code:
                comment += f" [{s.unit.ucum_code}]"
                tags.append(f"ALTER TABLE {t} ALTER COLUMN {s.name} SET TAGS ('unit' = {q(qudt or s.unit.ucum_code)});")
            if kind == "class":
                comment += f" -> {self.fq(self.module_of(s.range), snake(s.range))}.iri"
                fks.append((s.name, s.range))
            if kind == "struct":
                comment += f" (inlined {s.range})"
            ann = {}
            for k, v in (s.annotations._as_dict.items() if hasattr(s.annotations, "_as_dict") else dict(s.annotations or {}).items()):
                val = v.get("value") if isinstance(v, dict) else getattr(v, "value", v)
                ann[str(k)] = str(val)
            tag_keys = [k for k in ("unit_kind", "access", "catalog_path", "python_type", "item_view") if k in ann]
            if tag_keys:
                if "unit_kind" in ann and not (s.unit and s.unit.ucum_code):
                    comment += f" [{ann['unit_kind']}]"
                tags.append(f"ALTER TABLE {t} ALTER COLUMN {s.name} SET TAGS (" + ", ".join(f"{q(k)} = {q(ann[k])}" for k in tag_keys) + ");")
            if kind == "enum" and not s.multivalued:
                vals = ", ".join(q(pv) for pv in self.sv.get_enum(s.range).permissible_values)
                checks.append(f"ALTER TABLE {t} ADD CONSTRAINT ck_{snake(cls_name)}_{s.name} CHECK ({s.name} IS NULL OR {s.name} IN ({vals}));")
                comment += f" (enum {s.range}; see reference.{snake(s.range)})"
            if s.minimum_value is not None or s.maximum_value is not None:
                lo = f"{s.name} >= {s.minimum_value}" if s.minimum_value is not None else None
                hi = f"{s.name} <= {s.maximum_value}" if s.maximum_value is not None else None
                cond = " AND ".join(x for x in (lo, hi) if x)
                checks.append(f"ALTER TABLE {t} ADD CONSTRAINT ck_{snake(cls_name)}_{s.name}_range CHECK ({s.name} IS NULL OR ({cond}));")
            cols.append(f"  {s.name} {sql_t}{nn} COMMENT {q(comment)}")
        pk = next((s.name for s in self.sv.class_induced_slots(cls_name) if s.identifier), None)
        if pk:
            cols.append(f"  CONSTRAINT pk_{snake(cls_name)} PRIMARY KEY ({pk})")
        else:
            req = [s.name for s in self.ordered_slots(cls_name) if s.required][:3]
            cols.append(f"  CONSTRAINT pk_{snake(cls_name)} PRIMARY KEY ({', '.join(req)})" if req else "  -- link table: no single-column identifier")
        props = {
            "wsys_class": self.sv.get_uri(cls, expand=True),
            "wsys_parent": self.sv.get_uri(self.sv.get_class(cls.is_a), expand=True) if cls.is_a else "",
            "wsys_ancestors": ",".join(self.sv.class_ancestors(cls_name)),
            "saref_mapping": ",".join(cls.exact_mappings or []),
            "delta.enableChangeDataFeed": "true",
        }
        tbl = (f"CREATE SCHEMA IF NOT EXISTS {self.catalog}.{schema};\n"
               f"CREATE TABLE IF NOT EXISTS {t} (\n" + ",\n".join(cols) + "\n)"
               f"\nCOMMENT {q(cls.description)}\nTBLPROPERTIES (" + ", ".join(f"{q(k)} = {q(v)}" for k, v in props.items() if v) + ");")
        current = (f"CREATE OR REPLACE VIEW {t}_current COMMENT {q('Current rows of ' + cls_name + ' (valid_to IS NULL).')} AS\n"
                   f"SELECT * FROM {t} WHERE valid_to IS NULL;") if "valid_to" in {s.name for s in self.sv.class_induced_slots(cls_name)} else ""
        return "\n".join(x for x in [tbl, *tags, *checks, current] if x) + "\n"

    def emit_union_view(self, root: str = "Asset") -> str:
        leaves = [c for c in self.concrete_classes() if root in self.sv.class_ancestors(c)]
        common = [s.name for s in self.sv.class_induced_slots(root)]
        selects = [f"SELECT {', '.join(common)} FROM {self.fq(self.module_of(c), snake(c))}" for c in leaves]
        v = self.fq(self.module_of(root), snake(root))
        return (f"CREATE OR REPLACE VIEW {v} COMMENT {q(self.sv.get_class(root).description)} AS\n" + "\nUNION ALL\n".join(selects) + ";\n"
                f"CREATE OR REPLACE VIEW {v}_current AS SELECT * FROM {v} WHERE valid_to IS NULL;\n")

    def run(self, out_dir: Path) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "00_enums.sql").write_text(self.emit_enums())
        (out_dir / "01_asset_class.sql").write_text(self.emit_class_table())
        for i, c in enumerate(self.concrete_classes(), start=10):
            (out_dir / f"{i:02d}_{self.module_of(c)}_{snake(c)}.sql").write_text(self.emit_table(c))
        (out_dir / "90_asset_union_view.sql").write_text(self.emit_union_view("Asset"))
        if self.errors:
            raise SystemExit("mapping errors:\n  " + "\n  ".join(self.errors))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("schema")
    ap.add_argument("--catalog", default="water_core")
    ap.add_argument("-o", "--out", default="dist/delta")
    a = ap.parse_args()
    DeltaGenerator(a.schema, a.catalog).run(Path(a.out))
    print(f"wrote {len(list(Path(a.out).glob('*.sql')))} files to {a.out}")


if __name__ == "__main__":
    main()
