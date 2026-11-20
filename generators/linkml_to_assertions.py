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
ENGINE_CATALOG = HERE / "vendor" / "openswmm" / "catalog.json"


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

    @staticmethod
    def overridable_fields() -> list[str]:
        """Engine catalog paths a scenario may override: writable float, int or bool element
        fields of the vendored catalog, as swmm.simulation@2's runner accepts them."""
        import json

        catalog = json.loads(ENGINE_CATALOG.read_text(encoding="utf-8"))
        kinds = {name for name, entry in catalog["targets"].items() if "collection" in entry}
        return sorted(m["path"] for m in catalog["members"]
                      if m["form"] == "property" and m.get("access") == "rw"
                      and m.get("type") in ("float", "int", "bool") and m["path"].split(".")[0] in kinds)

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

    def cited(self) -> str:
        """One row per piece of evidence a finding cites."""
        return ("SELECT f.iri AS finding_iri, f.trace_id, f.verifier_passed, explode(f.evidence) AS evidence_iri "
                f"FROM {self.t('analysis', 'agent_finding')} f")

    def derived_bases(self) -> str:
        """One row per base revision a revision names in was_derived_from."""
        return f"SELECT r.iri, explode(r.was_derived_from) AS base_iri FROM {self.t('analysis', 'revision')} r"

    def set_members(self) -> str:
        """One row per member a parameter set names, with the set's study."""
        return f"SELECT p.iri, p.of_study, explode(p.members) AS member_iri FROM {self.t('analysis', 'parameter_set')} p"

    def set_events(self) -> str:
        """One row per event an event set names."""
        return f"SELECT s.iri, explode(s.events) AS event_iri FROM {self.t('analysis', 'event_set')} s"

    def lineage_views(self) -> list[tuple[str, str, str]]:
        """(view, description, select): where a result artifact, and a finding's evidence, came from."""
        artifact = self.t("reference", "artifact_lineage")
        return [
            (artifact, "Every result artifact with the attempt that generated it, the run and who asked for it, and the revision it ran.", f"""
SELECT x.iri AS artifact_iri, x.artifact_sha256, a.iri AS attempt_iri, a.attempt_id, a.manifest_sha256 AS attempt_manifest_sha256,
       r.iri AS run_iri, r.run_id, r.workflow, r.was_attributed_to AS requested_by, r.session_id, r.tool_call_id,
       v.iri AS revision_iri, v.revision_id, v.manifest_sha256 AS revision_manifest_sha256, v.input_sha256
FROM {self.t('analysis', 'result_artifact')} x JOIN {self.t('analysis', 'attempt')} a ON a.iri = x.was_generated_by
JOIN {self.t('analysis', 'run')} r ON r.iri = a.of_run JOIN {self.t('analysis', 'revision')} v ON v.iri = r.of_revision""".strip()),
            (self.t("reference", "finding_lineage"), "Every piece of evidence a finding cites, with the lineage of the result artifact it is.", f"""
SELECT e.finding_iri, e.trace_id, e.verifier_passed, e.evidence_iri, l.*
FROM ({self.cited()}) e LEFT JOIN {artifact} l ON l.artifact_iri = e.evidence_iri""".strip()),
        ]

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
        ws = self.t("topology", "water_system_current")
        self.add("represented_by_current_revision", "WARNING", "An asset with model links is REPRESENTED_BY an element in its own system's current revision.", f"""
SELECT a.focus_iri, 'asset is only linked to model elements in non-current revisions' AS message
FROM (SELECT e.from_iri AS focus_iri, max(CASE WHEN w.iri IS NOT NULL THEN 1 ELSE 0 END) AS has_current
      FROM {edge} e JOIN {self.t('model', 'swmm_element')} m ON m.iri = e.to_iri
      LEFT JOIN {edge} membership ON membership.from_iri = e.from_iri AND membership.relation = 'PART_OF_SYSTEM'
      LEFT JOIN {ws} w ON w.iri = membership.to_iri AND w.current_revision = m.revision
      WHERE e.relation = 'REPRESENTED_BY' GROUP BY e.from_iri) a
WHERE a.has_current = 0""")
        self.add("system_current_revision_exists", "ERROR", "A WaterSystem's current_revision is a known Revision.", f"""
SELECT w.iri AS focus_iri, concat('current_revision ', w.current_revision, ' is not in analysis.revision') AS message
FROM {ws} w LEFT JOIN {self.t('analysis', 'revision')} r ON r.iri = w.current_revision
WHERE w.current_revision IS NOT NULL AND r.iri IS NULL""")
        wo = self.t("operations", "work_order_current")
        self.add("event_resolved_by_completed_work", "ERROR", "A work order that resolves an event is COMPLETED with a completion time.", f"""
SELECT k.event AS focus_iri, concat('RESOLVED_BY work order ', o.iri, ' has status ', o.work_status) AS message
FROM {self.t('event', 'event_link')} k JOIN {wo} o ON o.iri = k.target_iri
WHERE k.link_relation = 'RESOLVED_BY' AND (o.work_status <> 'COMPLETED' OR o.completed_at IS NULL)""")
        self.add("work_order_ai_proposal_needs_approval", "ERROR", "A work order proposed by an AI agent is not APPROVED or beyond without a human approver.", f"""
SELECT o.iri AS focus_iri, 'AI-proposed work order advanced without approved_by_agent' AS message
FROM {wo} o JOIN {self.t('reference', 'agent')} a ON a.iri = o.proposed_by
WHERE a.agent_kind = 'AI_AGENT' AND o.work_status NOT IN ('REQUESTED', 'CANCELLED') AND o.approved_by_agent IS NULL""")
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
        self.add("swmm_model_source_revision", "ERROR", "A model element with an INP source locator points to a record in the same revision.", f"""
SELECT m.iri AS focus_iri, 'model source line is missing or belongs to a different revision' AS message
FROM {self.t('model','swmm_element')} m
LEFT JOIN {self.t('analysis','input_record')} s ON s.iri=m.source_record AND s.of_revision=m.revision
WHERE m.source_record IS NOT NULL AND m.ingestion_activity IS NOT NULL AND s.iri IS NULL""")
        self.add("swmm_result_lineage", "ERROR", "An imported SWMM simulation result resolves its source line, attempt, run, input revision, software agent and output artifact.", f"""
SELECT o.iri AS focus_iri, 'incomplete SWMM observation provenance' AS message
FROM {self.t('observation','observation')} o
LEFT JOIN {self.t('analysis','ingestion_activity')} i ON i.iri=o.ingestion_activity
LEFT JOIN {self.t('analysis','attempt')} a ON a.iri=o.was_generated_by
LEFT JOIN {self.t('analysis','run')} r ON r.iri=a.of_run
LEFT JOIN {self.t('analysis','revision')} rv ON rv.iri=r.of_revision
LEFT JOIN {self.t('analysis','input_record')} rec ON rec.iri=o.source_record AND rec.of_revision=rv.iri
LEFT JOIN {self.t('reference','agent')} agent ON agent.iri=a.was_associated_with
WHERE o.result_kind='SIMULATION' AND o.ingestion_activity IS NOT NULL
AND (i.iri IS NULL OR rec.iri IS NULL OR agent.iri IS NULL OR rv.bundle_sha256 IS NULL
OR NOT EXISTS (SELECT 1 FROM {self.t('analysis','result_artifact')} output WHERE output.was_generated_by=a.iri))""")
        self.add("swmm_input_bundle_evidence", "ERROR", "A local SWMM bundle records a primary artifact matching its input checksum.", f"""
SELECT r.iri AS focus_iri, 'primary input artifact is missing or differs from the input digest' AS message
FROM {self.t('analysis','revision')} r WHERE r.bundle_sha256 IS NOT NULL AND NOT EXISTS
(SELECT 1 FROM {self.t('analysis','input_artifact')} f
 WHERE f.of_revision=r.iri AND f.file_role='primary' AND f.input_sha256=r.input_sha256)""")
        self.add("observation_time_evidence", "ERROR", "An observation has an instant or an ordered time interval.", f"""
SELECT iri AS focus_iri, 'observation lacks valid temporal support' AS message
FROM {self.t('observation', 'observation')}
WHERE (phenomenon_time IS NULL AND (interval_start IS NULL OR interval_end IS NULL))
   OR interval_start > interval_end""")
        self.add("input_record_revision", "ERROR", "An input record belongs to an existing revision.", f"""
SELECT s.iri AS focus_iri, 'input record has no known revision' AS message
FROM {self.t('analysis', 'input_record')} s LEFT JOIN {self.t('analysis', 'revision')} r ON r.iri=s.of_revision
WHERE r.iri IS NULL""")
        # agents
        finding, agent = self.t("analysis", "agent_finding"), self.t("reference", "agent")
        self.add("finding_evidence_exists", "ERROR", "Everything a finding cites as evidence is a known entity.", f"""
SELECT e.finding_iri AS focus_iri, concat('evidence ', e.evidence_iri, ' is not a known entity') AS message
FROM ({self.cited()}) e LEFT JOIN {ent} x ON x.iri = e.evidence_iri
WHERE x.iri IS NULL""")
        self.add("finding_reviewed_by_a_person", "ERROR", "A finding is never reviewed by an AI agent, or by the agent it is attributed to.", f"""
SELECT f.iri AS focus_iri, concat('finding was reviewed by ', f.reviewed_by) AS message
FROM {finding} f LEFT JOIN {agent} a ON a.iri = f.reviewed_by
WHERE f.reviewed_by IS NOT NULL AND (a.agent_kind = 'AI_AGENT' OR f.reviewed_by = f.was_attributed_to)""")
        self.add("tool_call_in_a_session", "ERROR", "A tool call belongs to a known agent session.", f"""
SELECT c.iri AS focus_iri, concat('session ', c.in_session, ' is not in analysis.agent_session') AS message
FROM {self.t('analysis', 'tool_call')} c LEFT JOIN {self.t('analysis', 'agent_session')} s ON s.iri = c.in_session
WHERE s.iri IS NULL""")
        self.add("agent_session_acts_for_someone", "ERROR", "The agent of an agent session is an AI agent that acts on behalf of someone.", f"""
SELECT s.iri AS focus_iri, 'the session agent is unknown, is not an AI agent, or acts on behalf of no one' AS message
FROM {self.t('analysis', 'agent_session')} s LEFT JOIN {agent} a ON a.iri = s.was_associated_with
WHERE a.iri IS NULL OR a.agent_kind <> 'AI_AGENT' OR a.acts_on_behalf_of IS NULL""")
        # scenarios, derived revisions and approvals
        revision = self.t("analysis", "revision")
        self.add("scenario_override_writable", "ERROR", "A scenario overrides only writable float, int or bool element fields of the engine catalog.", f"""
SELECT o.iri AS focus_iri, concat('catalog path ', o.catalog_path, ' is not a writable scalar element field') AS message
FROM {self.t('analysis', 'scenario_override')} o
WHERE o.catalog_path NOT IN ({', '.join(q(f) for f in self.overridable_fields())})""")
        self.add("derived_revision_base_known", "ERROR", "A revision derived from another names a known revision.", f"""
SELECT d.iri AS focus_iri, concat('base revision ', d.base_iri, ' is not in analysis.revision') AS message
FROM ({self.derived_bases()}) d LEFT JOIN {revision} b ON b.iri = d.base_iri
WHERE b.iri IS NULL""")
        self.add("approval_decided_by_a_person", "ERROR", "An approval is decided by a person, never by an AI agent.", f"""
SELECT p.iri AS focus_iri, concat('approval was decided by ', p.was_associated_with) AS message
FROM {self.t('analysis', 'approval')} p JOIN {agent} a ON a.iri = p.was_associated_with
WHERE a.agent_kind = 'AI_AGENT'""")
        # experiments
        self.add("parameter_set_members_in_study", "ERROR", "A parameter set's members are candidates of its study.", f"""
SELECT m.iri AS focus_iri, concat('member ', m.member_iri, ' is not a candidate of ', m.of_study) AS message
FROM ({self.set_members()}) m LEFT JOIN {self.t('analysis', 'candidate')} c ON c.iri = m.member_iri AND c.of_study = m.of_study
WHERE c.iri IS NULL""")
        self.add("event_set_events_exist", "ERROR", "An event set's events are forcing events.", f"""
SELECT e.iri AS focus_iri, concat('event ', e.event_iri, ' is not in analysis.forcing_event') AS message
FROM ({self.set_events()}) e LEFT JOIN {self.t('analysis', 'forcing_event')} f ON f.iri = e.event_iri
WHERE f.iri IS NULL""")
        # observations
        self.add("observation_unit_matches_property", "WARNING", "An observation's unit is its property's default unit (after normalisation).", f"""
SELECT o.iri AS focus_iri, concat('unit ', o.unit, ' differs from default ', p.default_unit, ' for ', o.property_iri) AS message
FROM {self.t('observation', 'observation')} o JOIN {self.t('observation', 'observed_property')} p ON p.iri = o.property_iri
WHERE p.default_unit IS NOT NULL AND o.unit <> p.default_unit""")

    # -- output ------------------------------------------------------------------
    def run(self, out: Path) -> None:
        self.build_rules()
        out.mkdir(parents=True, exist_ok=True)
        lineage = "".join(f"CREATE OR REPLACE VIEW {view} COMMENT {q(desc)} AS\n{sel};\n\n" for view, desc, sel in self.lineage_views())
        (out / "00_support.sql").write_text(self.violation_table() + "\n" + self.entity_view() + "\n" + self.closure_table() + "\n" + lineage)
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
