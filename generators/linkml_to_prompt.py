"""
LinkML -> agent context digest (docs/agent-context.md, copied to dist/prompt/).

A compact, generated description of the ontology for an agent's system prompt:
class trees, the relation vocabulary with allowed ends, key enums, identity and
unit conventions, and the hard rules (taken from the SQL assertions, so the
prompt and the validator state the same constraints). Detail beyond this is
fetched on demand with a describe_ontology tool.

Usage: python -m generators.linkml_to_prompt schema/wsys.yaml -o docs/agent-context.md
"""
from __future__ import annotations

import argparse
import contextlib
import io
import tempfile
from pathlib import Path

import yaml
from linkml_runtime.utils.schemaview import SchemaView

from generators.linkml_to_assertions import Assertions
from generators.linkml_to_delta import DeltaGenerator, snake

HERE = Path(__file__).resolve().parent.parent
MODULES = ["wsys-core", "wsys-provenance", "wsys-topology", "wsys-observation", "wsys-event", "wsys-operations",
           "wsys-coupling", "wsys-analysis", "wsys-ml"]
KEY_ENUMS = ["LifecycleStatus", "QualityFlag", "ResultKind", "EventLifecycleState", "Severity", "ModelStage", "AgentKind"]


def first_sentence(text: str | None, limit: int = 140) -> str:
    t = " ".join((text or "").split())
    cut = t.find(". ")
    t = t[: cut + 1] if 0 < cut < limit else t[:limit]
    return t


class Prompt:
    def __init__(self, schema: str):
        self.d = DeltaGenerator(schema, "water_core")
        self.sv: SchemaView = self.d.sv
        self.sv.imports_closure()
        self.schemas = {s.name: s for s in self.sv.schema_map.values()}
        self.domains = yaml.safe_load((HERE / "schema/mappings/edge_domains.yaml").read_text())

    def module_classes(self, mod: str) -> list[str]:
        sid = self.schemas[mod].id
        return [c.name for c in self.sv.all_classes().values() if c.from_schema == sid and not c.mixin]

    def tree(self, mod: str) -> list[str]:
        names = set(self.module_classes(mod))
        lines = []

        def walk(c: str, depth: int) -> None:
            cls = self.sv.get_class(c)
            table = "" if cls.abstract or not self.d.has_identifier(c) and c not in ("Edge", "EventLink", "ComponentConnection") else f"  [{self.d.module_of(c)}.{snake(c)}]"
            mark = " (abstract)" if cls.abstract else ""
            lines.append(f"{'  ' * depth}- {c}{mark}{table}: {first_sentence(cls.description)}")
            for ch in sorted(k for k in names if self.sv.get_class(k).is_a == c):
                walk(ch, depth + 1)
        roots = sorted((c for c in names if self.sv.get_class(c).is_a not in names), key=lambda c: (self.sv.get_class(c).is_a or "", c))
        last = None
        for r in roots:
            parent = self.sv.get_class(r).is_a
            if parent and parent != last:
                lines.append(f"- (under {parent})")
            last = parent
            walk(r, 1 if parent else 0)
        return lines

    def render(self) -> str:
        out = ["# Ontology context for agents", "",
               f"Generated from the water-ontology schema (version {self.sv.schema.version}); do not edit. "
               "Tables are in catalog `water_core`; the table for a class is shown in brackets. Abstract classes have no table; query the union views `topology.asset` and `model.swmm_element`, or `reference.entity` (every entity with its class).", "",
               "## Identity and conventions", "",
               "- Every row has `iri` (the thing) and `class_iri` (what it is). Use IRIs, never labels, as tool arguments; resolve names through `label` and `alt_ids`.",
               "- Connectivity, drainage, containment, attachment, zone membership and model links are rows in `topology.edge` (`from_iri`, `relation`, `to_iri`), never columns on an asset.",
               "- An asset is linked to its SWMM model element by a `REPRESENTED_BY` edge; `topology.*` values are SI (m, ha, mm, m3/s), `model.*` values keep the model's own units.",
               "- Tables with `valid_to` are versioned; read the `<table>_current` view unless the question is historical.",
               "- Every derived value carries `was_generated_by`; cite it (attempt, model version, job) with any number you report.",
               "- Units are QUDT IRIs in a `unit` column or a column tag; never assume a unit.", ""]
        out += ["## Classes", ""]
        for mod in MODULES:
            out += [f"### {mod.replace('wsys-', '')}", ""] + self.tree(mod) + [""]
        out += ["### SWMM model elements (generated from the engine catalog)", "",
                "- SwmmElement (abstract), keyed by `revision` + `element_id`: SwmmNode -> SwmmJunction, SwmmOutfall, SwmmStorage, SwmmDivider; SwmmLink -> SwmmConduit, SwmmPump, SwmmOrifice, SwmmWeir, SwmmOutlet; SwmmSubcatchment; SwmmGage; SwmmPollutant; SwmmLanduse; SwmmPattern; SwmmTable. Columns mirror engine properties and are tagged with `catalog_path`, `access` and `unit_kind`.", ""]
        for fam, title, table in (("EdgeRelation", "Relations in topology.edge", "topology.edge"), ("EventLinkRelation", "Relations in event.event_link", "event.event_link")):
            enum = self.sv.get_enum(fam)
            out += [f"## {title}", ""]
            for rel, pv in enum.permissible_values.items():
                pairs = "; ".join(f"{a} -> {b}" for a, b in self.domains.get(fam, {}).get(rel, []))
                out.append(f"- `{rel}`: {pv.description or ''}" + (f" Allowed: {pairs}." if pairs else ""))
            out.append("")
        out += ["## Key enumerations", ""]
        for e in KEY_ENUMS:
            if e in self.sv.all_enums():
                out.append(f"- {e}: " + ", ".join(self.sv.get_enum(e).permissible_values))
        out.append("")
        a = Assertions(str(HERE / "schema" / "wsys.yaml"), "water_core")
        with tempfile.TemporaryDirectory() as td, contextlib.redirect_stdout(io.StringIO()):
            a.run(Path(td))
        out += ["## Rules the data must satisfy", "",
                "The validator enforces these; open violations are in `reference.ontology_violation`. If an entity you reason about appears there, say so.", ""]
        seen = set()
        for rid, sev, desc, _ in a.rules:
            if rid.startswith(("edge_domain_", "event_link_domain_")):
                continue
            if desc not in seen:
                out.append(f"- {desc}"); seen.add(desc)
        out += ["- Each relation only connects the classes listed under Relations.", "",
                "## What an agent may and may not do", "",
                "- May read governed tables and call registered tools; may write `AgentFinding` rows, events in state DETECTED with source AGENT, and proposals (`Setpoint`, `WorkOrder`) that name a human approver field left empty.",
                "- May not confirm events, approve work orders, set setpoints, or issue control actions.",
                "- Must state staleness, non-GOOD quality flags, out-of-scope predictions and non-current model revisions when they affect an answer.", ""]
        return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("schema", nargs="?", default="schema/wsys.yaml")
    ap.add_argument("-o", "--out", default="docs/agent-context.md")
    a = ap.parse_args()
    text = Prompt(a.schema).render()
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(text)
    print(f"wrote {a.out}: {len(text)} characters, about {len(text) // 4} tokens")


if __name__ == "__main__":
    main()
