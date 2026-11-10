# Changelog

## 0.1.0 (unreleased)
- LinkML source of truth: `core`, `topology`, `analysis` (stub), `ml`, generated `model`; release root `wsys.yaml`.
- Generators: OpenSWMM catalog → `model.yaml` (with lock and staleness check); LinkML → Delta DDL, Unity Catalog comments/tags, enum reference tables, class hierarchy table, SCD2 views, asset union view, STRUCT component views.
- Topology refactored to SWMM/OpenSWMM element semantics; distribution classes moved to a stub module; REPRESENTED_BY links assets to model elements.
- Agent context digest, glossary payload, Asset Bundle and deploy scripts (not yet run on a workspace).
- Fixed: SQL string literals now use backslash escapes (doubled quotes broke 35 DDL files under a Databricks parser); `NDArray` catalog types map to opaque JSON.
- End-to-end example: `loaders/swmm_to_rows.py`, `harness/duckdb_harness.py`, `examples/site_drainage/` with committed rows, and tests that break the data to prove the rules fire.
- Operations module (`WorkOrder`, `Inspection`); `WaterSystem.current_revision`; four new assertions (current revision, completed work, AI-proposed work needs approval).
- Fixed: eleven slots were defined in two modules (one with conflicting ranges); de-duplicated, with a test.
- Validation: SHACL fixtures and tests; 35 generated SQL assertions with `reference.entity`, `reference.class_closure` and `reference.ontology_violation`; `model.swmm_element` union view.
- Provenance module: PROV-O `Agent` and `Attributed`/`Activity`/`Dataset` mixins; Dublin Core metadata; DCAT distributions; actor slots re-typed to `Agent`.
- Generated Mermaid figures (module map, knowledge-graph views, relations, per-module class diagrams), architecture page, mkdocs site with GitHub Pages deploy.
- `edge_domains.yaml`: allowed from/to classes per Edge and EventLink relation.
- Coupling module mirroring HydroCouple SDK; analysis layer: generated `analysis_platform` from hydrocouple.platform JSON Schemas plus curated Revision/Run/Attempt/Study/Candidate/AgentFinding.
- Observation module (SOSA): Sensor, ObservedProperty, Datastream, Observation, Setpoint. Event module: Event, EventLink, EventStateChange.
- ML module: MLModel, MLModelVersion (scikit-learn, PyTorch, Lightning first-class), FeatureBinding, TrainingDataset, Prediction, DriftCheck.
- Hand-curated mappings: SWMM model classes → wsys topology; Esri Utility Network → wsys.
