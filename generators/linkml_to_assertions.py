"""
LinkML + edge_domains.yaml -> SQL assertions for Databricks (dist/assertions/*.sql).

SHACL checks one node at a time. The rules below need joins across rows
(an edge and the classes of both its ends, a conduit and its edges, an event
and its links), so they are compiled to SQL and run by the nightly violation
job. Every rule is a view returning (rule_id, severity, focus_iri, message);
the last file rebuilds reference.ontology_violation from all of them.

Usage: python -m generators.linkml_to_assertions schema/wsys.yaml --catalog water_core -o dist/assertions
"""
from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from generators.linkml_to_delta import DeltaGenerator, q, snake

HERE = Path(__file__).resolve().parent.parent
DOMAINS = HERE / "schema" / "mappings" / "edge_domains.yaml"


class Assertions:
    def __init__(self, schema: str, catalog: str):
        self.d = DeltaGenerator(schema, catalog)
        self.sv = self.d.sv
        self.cat = catalog
        self.domains = yaml.safe_load(DOMAINS.read_text())
        self.rules: list[tuple[str, str, str, str]] = []   # (rule_id, severity, description, select)

    # -- helpers -----------------------------------------------------------
    def iri(self, cls: str) -> str:
        return self.sv.get_uri(self.sv.get_class(cls), expand=True)

    def leaves(self, cls: str) -> list[str]:
        """Concrete classes that are cls or descend from it."""
        return sorted(c for c in self.sv.all_classes() if cls in self.sv.class_ancestors(c) and not self.sv.get_class(c).abstract)

    def in_list(self, classes: list[str]) -> str:
        return ", ".join(q(self.iri(c)) for c in classes) or "''"

    def t(self, schema: str, table: str) -> str:
        return f"{self.cat}.{schema}.{table}"

    def add(self, rule_id: str, severity: str, description: str, select: str) -> None:
        self.rules.append((rule_id, severity, description, select.strip()))

    # -- support objects -----------------------------------------------------
    def entity_view(self) -> str:
        parts = []
        for c in self.d.concrete_classes():
            slots = {s.name for s in self.sv.class_induced_slots(c)}
            if {"iri", "class_iri"} <= slots:
                tbl = self.d.fq(self.d.module_of(c), snake(c))
                cur = " WHERE valid_to IS NULL" if "valid_to" in slots else ""
                parts.append(f"SELECT iri, class_iri, {q(tbl)} AS table_name FROM {tbl}{cur}")
        return (f"CREATE OR REPLACE VIEW {self.t('reference', 'entity')} COMMENT 'Every current entity with its class; the lookup the assertions and the entity grounder share.' AS\n"
                + "\nUNION ALL\n".join(parts) + ";\n")

    def closure_table(self) -> str:
        rows = []
        for c in self.sv.all_classes():
            for a in self.sv.class_ancestors(c):
                rows.append(f"  ({q(self.iri(c))}, {q(self.iri(a))})")
        tbl = self.t("reference", "class_closure")
        return (f"CREATE TABLE IF NOT EXISTS {tbl} (class_iri STRING NOT NULL, ancestor_iri STRING NOT NULL) COMMENT 'Reflexive transitive closure of is_a, generated from the ontology.';\n"
                f"INSERT OVERWRITE {tbl} VALUES\n" + ",\n".join(rows) + ";\n")

    def violation_table(self) -> str:
        return (f"CREATE TABLE IF NOT EXISTS {self.t('reference', 'ontology_violation')} (\n  rule_id STRING NOT NULL,\n  severity STRING NOT NULL,\n  focus_iri STRING,\n"
                "  message STRING,\n  source_system STRING,\n  detected_at TIMESTAMP NOT NULL\n) COMMENT 'Open violations of ontology rules; agents must disclose when an entity they reason about appears here.';\n")

    # -- rules -----------------------------------------------------------------
    def build_rules(self) -> None:
        edge, ent = self.t("topology", "edge_current"), self.t("reference", "entity")
        # R1: edge endpoints exist
        self.add("edge_endpoint_exists", "ERROR", "Both ends of every edge are known entities.", f"""
SELECT e.from_iri AS focus_iri, concat('edge ', e.relation, ' -> ', e.to_iri, ' has an unknown ', CASE WHEN f.iri IS NULL THEN 'from_iri' ELSE 'to_iri' END) AS message
FROM {edge} e LEFT JOIN {ent} f ON f.iri = e.from_iri LEFT JOIN {ent} t ON t.iri = e.to_iri
WHERE f.iri IS NULL OR t.iri IS NULL""")
        # R2: edge domain per relation
        for rel, pairs in self.domains["EdgeRelation"].items():
            conds = " OR ".join(f"(f.class_iri IN ({self.in_list(self.leaves(a))}) AND t.class_iri IN ({self.in_list(self.leaves(b))}))" for a, b in pairs)
            allowed = "; ".join(f"{a} -> {b}" for a, b in pairs)
            self.add(f"edge_domain_{rel.lower()}", "ERROR", f"{rel} only connects: {allowed}.", f"""
SELECT e.from_iri AS focus_iri, concat({q(rel)}, ' from ', f.class_iri, ' to ', t.class_iri, ' is not allowed (', {q(allowed)}, ')') AS message
FROM {edge} e JOIN {ent} f ON f.iri = e.from_iri JOIN {ent} t ON t.iri = e.to_iri
WHERE e.relation = {q(rel)} AND NOT ({conds})""")
        # R3: every link has exactly one upstream and one downstream node
        nodes, links = self.in_list(self.leaves("Node")), self.in_list(self.leaves("Link"))
        self.add("link_two_endpoints", "ERROR", "Every in-service Link has exactly one upstream node (Node CONNECTS_TO Link) and one downstream node (Link CONNECTS_TO Node).", f"""
SELECT l.iri AS focus_iri, concat('link has ', coalesce(u.n, 0), ' upstream and ', coalesce(dn.n, 0), ' downstream CONNECTS_TO edges; expected 1 and 1') AS message
FROM {ent} l
LEFT JOIN (SELECT e.to_iri AS iri, count(*) AS n FROM {edge} e JOIN {ent} x ON x.iri = e.from_iri WHERE e.relation = 'CONNECTS_TO' AND x.class_iri IN ({nodes}) GROUP BY e.to_iri) u ON u.iri = l.iri
LEFT JOIN (SELECT e.from_iri AS iri, count(*) AS n FROM {edge} e JOIN {ent} x ON x.iri = e.to_iri WHERE e.relation = 'CONNECTS_TO' AND x.class_iri IN ({nodes}) GROUP BY e.from_iri) dn ON dn.iri = l.iri
WHERE l.class_iri IN ({links}) AND (coalesce(u.n, 0) <> 1 OR coalesce(dn.n, 0) <> 1)""")
        # R4/R5: required relations
        for rid, cls, rel, side, msg in [
            ("subcatchment_drains_somewhere", "Subcatchment", "DRAINS_TO", "from_iri", "subcatchment has no DRAINS_TO edge"),
            ("subcatchment_has_gage", "Subcatchment", "USES_GAGE", "from_iri", "subcatchment has no USES_GAGE edge"),
            ("sensor_is_attached", "Sensor", "ATTACHED_TO", "from_iri", "sensor is not ATTACHED_TO any asset"),
            ("asset_in_a_system", "Asset", "PART_OF_SYSTEM", "from_iri", "asset has no PART_OF_SYSTEM edge"),
        ]:
            sev = "WARNING" if rid in ("subcatchment_has_gage", "asset_in_a_system") else "ERROR"
            self.add(rid, sev, f"Every {cls} has at least one {rel} edge.", f"""
SELECT x.iri AS focus_iri, {q(msg)} AS message
FROM {ent} x LEFT JOIN {edge} e ON e.{side} = x.iri AND e.relation = {q(rel)}
WHERE x.class_iri IN ({self.in_list(self.leaves(cls))}) AND e.{side} IS NULL""")
        # R6: REPRESENTED_BY is one-to-one per revision
        self.add("represented_by_unique", "ERROR", "An asset is REPRESENTED_BY at most one model element per revision.", f"""
SELECT e.from_iri AS focus_iri, concat('asset is represented by ', count(*), ' elements in revision ', m.revision) AS message
FROM {edge} e JOIN {self.t('model', 'swmm_element')} m ON m.iri = e.to_iri
WHERE e.relation = 'REPRESENTED_BY' GROUP BY e.from_iri, m.revision HAVING count(*) > 1""")
        # events
        ev, link = self.t("event", "event_current"), self.t("event", "event_link")
        self.add("event_resolved_has_resolution", "ERROR", "A RESOLVED event has a RESOLVED_BY link.", f"""
SELECT v.iri AS focus_iri, 'event is RESOLVED but has no RESOLVED_BY link' AS message
FROM {ev} v LEFT JOIN {link} k ON k.event = v.iri AND k.link_relation = 'RESOLVED_BY'
WHERE v.lifecycle_state = 'RESOLVED' AND k.event IS NULL""")
        self.add("event_confirmed_by_someone", "ERROR", "CONFIRMED, IN_PROGRESS and RESOLVED events record who confirmed them.", f"""
SELECT v.iri AS focus_iri, concat('event is ', v.lifecycle_state, ' but confirmed_by is empty') AS message
FROM {ev} v WHERE v.lifecycle_state IN ('CONFIRMED', 'IN_PROGRESS', 'RESOLVED') AND v.confirmed_by IS NULL""")
        self.add("event_never_confirmed_by_ai", "ERROR", "An AI agent may detect an event but never confirm it.", f"""
SELECT v.iri AS focus_iri, concat('event was confirmed by AI agent ', a.iri) AS message
FROM {ev} v JOIN {self.t('reference', 'agent')} a ON a.iri = v.confirmed_by WHERE a.agent_kind = 'AI_AGENT'""")
        self.add("event_has_location_or_impact", "WARNING", "Every event is LOCATED_AT or AFFECTS something.", f"""
SELECT v.iri AS focus_iri, 'event has neither a LOCATED_AT nor an AFFECTS link' AS message
FROM {ev} v LEFT JOIN {link} k ON k.event = v.iri AND k.link_relation IN ('LOCATED_AT', 'AFFECTS')
WHERE k.event IS NULL""")
        self.add("event_times_ordered", "ERROR", "occurred_at <= detected_at <= ended_at where present.", f"""
SELECT v.iri AS focus_iri, 'event times are out of order' AS message
FROM {ev} v WHERE (v.occurred_at IS NOT NULL AND v.occurred_at > v.detected_at) OR (v.ended_at IS NOT NULL AND v.ended_at < coalesce(v.occurred_at, v.detected_at))""")
        for rel, pairs in self.domains["EventLinkRelation"].items():
            others = sorted({b if a == "Event" else a for a, b in pairs})
            allowed = self.in_list(sorted({leaf for o in others for leaf in self.leaves(o)}))
            self.add(f"event_link_domain_{rel.lower()}", "ERROR", f"{rel} links an event to: {', '.join(others)}.", f"""
SELECT k.event AS focus_iri, concat({q(rel)}, ' target ', k.target_iri, ' has class ', coalesce(t.class_iri, 'unknown'), '; allowed: ', {q(', '.join(others))}) AS message
FROM {link} k LEFT JOIN {ent} t ON t.iri = k.target_iri
WHERE k.link_relation = {q(rel)} AND (t.class_iri IS NULL OR t.class_iri NOT IN ({allowed}))""")
        # ML
        self.add("prediction_target_matches_model_class", "ERROR", "A prediction's target entity is an instance of its model's applies_to_class.", f"""
SELECT p.iri AS focus_iri, concat('target ', p.target_entity, ' of class ', coalesce(t.class_iri, 'unknown'), ' is not a ', m.applies_to_class) AS message
FROM {self.t('ml', 'prediction')} p
JOIN {self.t('ml', 'ml_model_version')} v ON v.iri = p.of_version
JOIN {self.t('ml', 'ml_model')} m ON m.iri = v.of_model
LEFT JOIN {ent} t ON t.iri = p.target_entity
LEFT JOIN {self.t('reference', 'class_closure')} c ON c.class_iri = t.class_iri AND c.ancestor_iri = m.applies_to_class
WHERE m.applies_to_class IS NOT NULL AND c.class_iri IS NULL""")
        self.add("only_one_champion", "ERROR", "A model has at most one CHAMPION version.", f"""
SELECT v.of_model AS focus_iri, concat(count(*), ' CHAMPION versions') AS message
FROM {self.t('ml', 'ml_model_version')} v WHERE v.stage = 'CHAMPION' GROUP BY v.of_model HAVING count(*) > 1""")
        # observations
        self.add("observation_unit_matches_property", "WARNING", "An observation's unit is its property's default unit (after normalisation).", f"""
SELECT o.iri AS focus_iri, concat('unit ', o.unit, ' differs from default ', p.default_unit, ' for ', o.property_iri) AS message
FROM {self.t('observation', 'observation')} o JOIN {self.t('observation', 'observed_property')} p ON p.iri = o.property_iri
WHERE p.default_unit IS NOT NULL AND o.unit <> p.default_unit""")

    # -- output ------------------------------------------------------------------
    def run(self, out: Path) -> None:
        self.build_rules()
        out.mkdir(parents=True, exist_ok=True)
        (out / "00_support.sql").write_text(self.violation_table() + "\n" + self.entity_view() + "\n" + self.closure_table())
        views = []
        for i, (rid, sev, desc, sel) in enumerate(self.rules, start=10):
            v = self.t("reference", f"v_violation_{rid}")
            views.append((rid, sev, v))
            (out / f"{i:02d}_{rid}.sql").write_text(f"-- {rid} [{sev}]: {desc}\nCREATE OR REPLACE VIEW {v} COMMENT {q(desc)} AS\n{sel};\n")
        union = "\nUNION ALL\n".join(f"SELECT {q(rid)} AS rule_id, {q(sev)} AS severity, focus_iri, message, CAST(NULL AS STRING) AS source_system, current_timestamp() AS detected_at FROM {v}" for rid, sev, v in views)
        (out / "99_refresh_violations.sql").write_text(f"-- Rebuild the open-violations table from every rule view.\nINSERT OVERWRITE {self.t('reference', 'ontology_violation')}\n{union};\n")
        rules_md = ["| Rule | Severity | Meaning |", "| --- | --- | --- |"] + [f"| `{rid}` | {sev} | {desc} |" for rid, sev, desc, _ in self.rules]
        (out / "RULES.md").write_text("# Ontology assertions\n\n" + "\n".join(rules_md) + "\n")
        print(f"wrote {len(self.rules)} rules to {out}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("schema", nargs="?", default="schema/wsys.yaml")
    ap.add_argument("--catalog", default="water_core")
    ap.add_argument("-o", "--out", default="dist/assertions")
    a = ap.parse_args()
    Assertions(a.schema, a.catalog).run(Path(a.out))


if __name__ == "__main__":
    main()
