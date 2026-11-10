"""
LinkML -> Mermaid diagrams (docs/diagrams/*.md), generated so figures never go stale.

  modules.md          module dependency map (from imports)
  knowledge-graph.md  the curated classes grouped by module, with inheritance,
                      reference slots and the Edge / EventLink relations
                      allowed by schema/mappings/edge_domains.yaml
  relations.md        one figure per relation family + a table of allowed pairs
  <module>.md         class diagram per module (attributes abbreviated)

Usage: python -m generators.linkml_to_diagrams schema/wsys.yaml -o docs/diagrams
"""
from __future__ import annotations

import argparse
import re
from collections import defaultdict
from pathlib import Path

import yaml
from linkml_runtime.utils.schemaview import SchemaView

HERE = Path(__file__).resolve().parent.parent
DOMAINS = HERE / "schema" / "mappings" / "edge_domains.yaml"

# classes shown in the overview; generated modules are collapsed to their roots
OVERVIEW_HIDE_PREFIX = ("Platform",)
OVERVIEW_MODEL_KEEP = {"SwmmElement", "SwmmNode", "SwmmLink", "SwmmSubcatchment", "SwmmGage", "SwmmPollutant"}
# Focused views of the knowledge graph: name -> (one-line reading, classes)
VIEWS = {
    "Physical network and its SWMM model": (
        "Assets form a directed graph through Edge rows; each asset is REPRESENTED_BY its element in a packaged Revision.",
        ["WaterSystem", "Zone", "Subcatchment", "RainGage", "Node", "Junction", "Outfall", "StorageUnit", "LIDControl", "Link", "Conduit", "Pump", "Orifice", "Weir",
         "Inlet", "Street", "Structure", "Terminal", "Edge", "SwmmElement", "SwmmNode", "SwmmLink", "SwmmSubcatchment", "SwmmGage", "Revision"]),
    "Telemetry and events around an asset": (
        "A Sensor is ATTACHED_TO an asset; its Datastream carries Observations; Observations are EVIDENCE_FOR Events, which AFFECT assets and zones and are EXPLAINED_BY model attempts.",
        ["Asset", "Zone", "Structure", "Sensor", "ObservedProperty", "Datastream", "Observation", "Setpoint", "Event", "EventLink", "EventStateChange", "Attempt", "AgentFinding"]),
    "Simulation lifecycle (HydroCouple and hydrocouple.platform)": (
        "A Composition of ModelComponents is packaged as a Revision, executed as Runs and Attempts (or optimised in a Study); simulated values come back as Observations of kind SIMULATION.",
        ["ModelComponent", "ComponentArgument", "ExchangeItem", "Input", "Output", "AdaptedOutput", "ComponentConnection", "Composition", "Revision", "Run", "Attempt", "Study", "Candidate", "Observation", "WaterSystem", "Mesh2D", "Aquifer"]),
    "Machine learning": (
        "An MLModel's target and inputs are FeatureBindings to ontology classes and properties; each MLModelVersion is trained on a TrainingDataset and produces Predictions about entities, with DriftChecks over time; surrogates emulate a Revision.",
        ["MLModel", "MLModelVersion", "FeatureBinding", "TrainingDataset", "EvaluationMetric", "Prediction", "DriftCheck", "Revision", "Event", "Asset", "Zone"]),
}

MODULE_LABEL = {
    "wsys-core": "core", "wsys-topology": "topology", "wsys-observation": "observation", "wsys-event": "event",
    "wsys-coupling": "coupling", "wsys-analysis": "analysis", "wsys-analysis-platform": "platform contracts (generated)",
    "wsys-model": "SWMM model (generated)", "wsys-ml": "ml",
}


def mid(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", name)


class Diagrams:
    def __init__(self, schema: str):
        self.sv = SchemaView(schema)
        self.sv.imports_closure()                      # load every module before iterating schema_map
        self.schemas = dict(self.sv.schema_map)
        self.domains = yaml.safe_load(DOMAINS.read_text())

    def module_of(self, cls_name: str) -> str:
        c = self.sv.get_class(cls_name)
        sc = next((s for s in self.schemas.values() if s.id == c.from_schema), None)
        return sc.name if sc else self.sv.schema.name

    def classes_by_module(self) -> dict[str, list[str]]:
        out = defaultdict(list)
        for c in self.sv.all_classes().values():
            out[self.module_of(c.name)].append(c.name)
        return out

    # ------------------------------------------------------------------ modules
    def modules_md(self) -> str:
        lines = ["# Module map", "", "Each module is one LinkML file; arrows are `imports`. Generated modules are regenerated from vendored inputs, never edited.", "", "```mermaid", "flowchart LR"]
        for name, sc in self.schemas.items():
            if name == "linkml:types" or name == "types":
                continue
            label = MODULE_LABEL.get(sc.name, sc.name)
            style = ":::gen" if "generated" in label else ""
            lines.append(f"  {mid(sc.name)}[\"{label}<br/><small>{len([c for c in self.sv.all_classes().values() if c.from_schema == sc.id])} classes</small>\"]{style}")
        for name, sc in self.schemas.items():
            for imp in sc.imports or []:
                if imp.startswith("linkml:"):
                    continue
                tgt = next((s for s in self.schemas.values() if s.name == imp or s.name == f"wsys-{imp}" or s.name == f"wsys-{imp.replace('_', '-')}"), None)
                if tgt and tgt.name != sc.name:
                    lines.append(f"  {mid(sc.name)} --> {mid(tgt.name)}")
        lines += ["  classDef gen fill:#eef,stroke:#88a,stroke-dasharray: 4 3;", "```", ""]
        return "\n".join(lines)

    # ------------------------------------------------------------- overview KG
    def overview_classes(self) -> dict[str, list[str]]:
        keep = defaultdict(list)
        for mod, classes in self.classes_by_module().items():
            for c in classes:
                if c.startswith(OVERVIEW_HIDE_PREFIX):
                    continue
                if mod == "wsys-model" and c not in OVERVIEW_MODEL_KEEP:
                    continue
                if c in ("Entity",):
                    continue
                keep[mod].append(c)
        return keep

    def graph_block(self, shown: set[str], direction: str = "TB") -> list[str]:
        keep = defaultdict(list)
        for c in shown:
            keep[self.module_of(c)].append(c)
        lines = ["```mermaid", f"flowchart {direction}"]
        for mod, classes in keep.items():
            lines.append(f"  subgraph {mid(mod)}[\"{MODULE_LABEL.get(mod, mod)}\"]")
            for c in sorted(classes):
                cls = self.sv.get_class(c)
                shape = f"{mid(c)}([\"{c}\"])" if cls.abstract else f"{mid(c)}[\"{c}\"]"
                lines.append(f"    {shape}")
            lines.append("  end")
        # is_a
        for c in sorted(shown):
            p = self.sv.get_class(c).is_a
            if p and p in shown:
                lines.append(f"  {mid(c)} -.-> {mid(p)}")
        # reference slots (own slots only, to keep it readable)
        for c in sorted(shown):
            cls = self.sv.get_class(c)
            own = list(cls.slots or []) + list((cls.attributes or {}).keys())
            for sn in own:
                s = self.sv.induced_slot(sn, c)
                if s.range in shown and s.range != c and not s.inlined and not s.inlined_as_list:
                    lines.append(f"  {mid(c)} -- {sn} --> {mid(s.range)}")
        # edge domains
        for fam, rels in self.domains.items():
            for rel, pairs in rels.items():
                for a, b in pairs:
                    if a in shown and b in shown:
                        lines.append(f"  {mid(a)} == {rel} ==> {mid(b)}")
        lines += ["```"]
        return lines

    def knowledge_graph_md(self) -> str:
        legend = ("Boxes are classes grouped by module; rounded boxes are abstract. Dashed arrows are `is_a`. Thin labelled arrows are reference slots "
                  "(a column holding another entity's IRI). Thick arrows are `topology.edge` / `event_link` relations allowed by `edge_domains.yaml`; "
                  "an abstract class stands for all its subclasses.")
        lines = ["# Knowledge graph", "", legend, ""]
        for title, (reading, classes) in VIEWS.items():
            shown = {c for c in classes if c in self.sv.all_classes()}
            lines += [f"## {title}", "", reading, ""] + self.graph_block(shown) + [""]
        full = {c for cs in self.overview_classes().values() for c in cs}
        lines += ["## Everything at once", "", "<details><summary>Full graph of the curated classes (large)</summary>", ""] + self.graph_block(full) + ["", "</details>", ""]
        return "\n".join(lines)

    # ---------------------------------------------------------------- relations
    def relations_md(self) -> str:
        lines = ["# Relations", "", "Allowed (from → to) pairs per relation, from `schema/mappings/edge_domains.yaml`. These become SQL assertions on `topology.edge` and `event.event_link`.", ""]
        for fam, rels in self.domains.items():
            lines += [f"## {fam}", "", "```mermaid", "flowchart LR"]
            for rel, pairs in rels.items():
                for a, b in pairs:
                    lines.append(f"  {mid(a)}[{a}] -- {rel} --> {mid(b)}[{b}]")
            lines += ["```", "", "| Relation | From | To | Meaning |", "| --- | --- | --- | --- |"]
            enum = self.sv.get_enum(fam)
            for rel, pairs in rels.items():
                desc = (enum.permissible_values[rel].description or "") if enum and rel in enum.permissible_values else ""
                for a, b in pairs:
                    lines.append(f"| `{rel}` | {a} | {b} | {desc} |")
            lines.append("")
        return "\n".join(lines)

    # ------------------------------------------------------------- per module
    def module_md(self, mod: str, classes: list[str]) -> str:
        sc = next(s for s in self.schemas.values() if s.name == mod)
        lines = [f"# {MODULE_LABEL.get(mod, mod)}", "", (sc.description or "").strip(), "", "```mermaid", "classDiagram"]
        for c in sorted(classes):
            cls = self.sv.get_class(c)
            lines.append(f"  class {mid(c)} {{")
            if cls.abstract:
                lines.append("    <<abstract>>")
            own = list(cls.slots or []) + list((cls.attributes or {}).keys())
            for sn in own[:14]:
                s = self.sv.induced_slot(sn, c)
                t = s.range or "string"
                mark = "*" if s.required or s.identifier else ""
                lines.append(f"    {t} {sn}{mark}")
            if len(own) > 14:
                lines.append(f"    ... {len(own) - 14} more")
            lines.append("  }")
        for c in sorted(classes):
            cls = self.sv.get_class(c)
            if cls.is_a:
                lines.append(f"  {mid(cls.is_a)} <|-- {mid(c)}")
            own = list(cls.slots or []) + list((cls.attributes or {}).keys())
            for sn in own:
                s = self.sv.induced_slot(sn, c)
                if s.range in self.sv.all_classes() and s.range != c:
                    arrow = "*--" if (s.inlined or s.inlined_as_list) else "-->"
                    mult = '"*"' if s.multivalued else '"1"'
                    lines.append(f"  {mid(c)} {arrow} {mult} {mid(s.range)} : {sn}")
        lines += ["```", "", "## Classes", "", "| Class | Parent | Description |", "| --- | --- | --- |"]
        for c in sorted(classes):
            cls = self.sv.get_class(c)
            lines.append(f"| `{c}` | {cls.is_a or ''} | {(cls.description or '').strip().replace(chr(10), ' ')} |")
        return "\n".join(lines) + "\n"

    def run(self, out: Path) -> None:
        out.mkdir(parents=True, exist_ok=True)
        (out / "modules.md").write_text(self.modules_md())
        (out / "knowledge-graph.md").write_text(self.knowledge_graph_md())
        (out / "relations.md").write_text(self.relations_md())
        for mod, classes in self.classes_by_module().items():
            if mod in MODULE_LABEL:
                (out / f"{mod.replace('wsys-', '')}.md").write_text(self.module_md(mod, classes))
        print(f"wrote {len(list(out.glob('*.md')))} diagram pages to {out}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("schema", nargs="?", default="schema/wsys.yaml")
    ap.add_argument("-o", "--out", default="docs/diagrams")
    a = ap.parse_args()
    Diagrams(a.schema).run(Path(a.out))


if __name__ == "__main__":
    main()
