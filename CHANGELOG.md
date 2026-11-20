# Changelog

## 0.2.0 review branch

- Generate a governed SQLite snapshot, scoped term registry, versioned Delta terminology records and native-to-ontology coverage inventory.
- Preserve SWMM source evidence, model water quality, checksummed results, occurrence identity, unit metadata and provenance across the import and simulation.
- Share SWMM code schemes and aliases across projections. Migrate legacy identifiers and publish or roll back local releases with an audit trail.
- Check a new engine catalog for compatibility: `python -m generators.catalog_to_linkml --catalog NEW.json --compat` fails when the catalog drops a path `schema/model.yaml` uses and passes on additions. The engine's contract job runs it.
- Re-vendor the OpenSWMM catalog at engine `0bf55d4f`, where every method carries an `effect`.
- Agent trace vocabulary: `ToolCall`, `Tool` and `AgentSession`; `AgentFinding` gains `tool_calls` and a `verifier_report`; `trace_id` is no longer tied to MLflow. Every change adds; no existing property changed, so there is no migration.
- `loaders/platform_to_rows.py` turns hydrocouple.platform revision and attempt manifests into rows with the person, AI agent, agent session and tool call they name. `reference.artifact_lineage` and `reference.finding_lineage` are generated for Databricks, DuckDB and the SQLite snapshot.
- `loaders/findings.py` records an `AgentFinding` and verifies each number it cites against the run summary it cites, by checksum. Four new assertions cover finding evidence, review by a person, tool-call sessions and agent delegation.
- `loaders/rows.py` holds the row writer both loaders share; `swmm_to_rows` output is unchanged.
- Re-vendor the platform schemas at `b23c588`, which adds `run-summary-1.json` (`PlatformRunSummary`).


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
