# Changelog

## Unreleased: compositions (ecosystem Phase 5)

- `platform_to_rows` loads an authored composition with its revision (component instances in the revision manifest, the composition file beside it, checked against it): a `Composition`, a `ModelComponent` per instance with its role and model as arguments, the `Output` and `Input` each connection joins, the `ComponentConnection` and a `COUPLED_TO` edge between the components; the `Revision` names it in `of_composition`, and an attempt of a composition run `used` its components.
- An exchange item's `value_kind` and `data_geometry` are optional: OpenSWMM's split phase ports declare neither. Relaxing a requirement adds, so there is no migration.
- One assertion: a connection joins an output and an input of components of its own composition.
- Re-vendor the platform schemas at `e016891` (component instances, `provenance.components`, composition hosts).

## Unreleased: the digital twin loop (ecosystem Phase 4)

- `Schedule` (a digital twin's cycles on one revision: state, pace, cycle length, the platform schedule view embedded); a `Run` names its `in_schedule`, `cycle_number` and `cycle_end`; an `Attempt` names the saved state it `resumed_from` (a result artifact of the attempt before it).
- `Setpoint` gains `setpoint_state` (`PROPOSED`, `APPROVED`, `REJECTED`, `SUPERSEDED`, `APPLIED`), `proposal_id`, `for_schedule`, `decided_by` and `decided_at`, `applied_in` (the attempt) and `applied_at`. Every change adds, so there is no migration.
- `platform_to_rows` loads a schedule with its setpoint proposals (`--schedule VIEW PROPOSALS`): who proposed each (the twin's control policy, an AI agent acting for its person, or a person) and who decided it; a twin cycle's `forecast.json` as `FORECAST` observations with their issue time and lead time; and telemetry (`--telemetry`, a JSON description of a CSV) as `MEASUREMENT` observations with a sensor and a datastream per column, each sensor attached to the asset it measures. Forecasts and measurements are normalized to SI at the model's UTC wall clock, so the two of the same element, property and time share their keys.
- Four assertions: a setpoint is decided by a person, never an AI agent, and an approved one names that person; only an approved setpoint is applied, naming the attempt that applied it; an attempt resumed from another attempt's result artifact; a forecast records its issue time and horizon.
- Re-vendor the platform schemas at `fdb0de1` (schedules, setpoint proposals, `provenance.resumed_from`).

## Unreleased: experiments (ecosystem Phase 3)

- `Study` gains `study_kind` (`SAMPLING`, `OPTIMIZATION`, `ALTERNATIVES`), `search_algorithm`, `acceptance_rule` (an inlined `AcceptanceRule` of `AcceptanceThreshold`s and a best fraction) and `scored_over`, the `EventSet` its candidates are scored on; `Candidate` gains `metric_values`, every value the evaluator reported; `ParameterSet` is a calibration's behavioral set: the candidates of one study its acceptance rule accepts. Every change adds; no existing property changed, so there is no migration (the semantic diff is empty).
- `platform_to_rows` fills these from a study manifest, an `EventSet` and its `ForcingEvent`s from the study's env config (each event's rain is an input artifact of the study's revision), and loads behavioral sets with `--parameter-set SET STUDY_MANIFEST`. Two assertions: a parameter set's members are candidates of its study, and an event set's events exist.
- Re-vendor the platform schemas at `91f0683` (metrics per evaluation) and `9b700dc` (discrete dimensions).

## Unreleased: scenarios and studies (ecosystem Phase 2)

- `Scenario` and `ScenarioOverride` (a revision plus element field overrides by engine catalog path, as `swmm.simulation@2` runs it), `EventSet` and `ForcingEvent`, `Approval`; `Revision` gains `was_derived_from`, `derived_in_study` and `chosen_evaluation`; `Study` embeds its study manifest and `AgentSession` its platform view, both generated from the platform's new schemas.
- **Breaking:** `Candidate.parameters` (JSON text) is replaced by `decision_values` and `objective_values` becomes a typed list. `python -m migrations.v0_3 OLD -o NEW` migrates rows; the semantic diff names exactly these two slots.
- `platform_to_rows` loads derived revisions, scenario runs and study manifests (`--study`); three assertions: a scenario overrides only writable scalar element fields of the vendored catalog, a derived revision's base is known, an approval is decided by a person.
- Re-vendor the platform schemas at `b3ba318`.

## 0.2.0 review branch

- Generate a governed SQLite snapshot, scoped term registry, versioned Delta terminology records and native-to-ontology coverage inventory.
- Preserve SWMM source evidence, model water quality, checksummed results, occurrence identity, unit metadata and provenance across the import and simulation.
- Share SWMM code schemes and aliases across projections. Migrate legacy identifiers and publish or roll back local releases with an audit trail.
- Check a new engine catalog for compatibility: `python -m generators.catalog_to_linkml --catalog NEW.json --compat` fails when the catalog drops a path `schema/model.yaml` uses and passes on additions. The engine's contract job runs it.
- Re-vendor the OpenSWMM catalog at engine `0bf55d4f`, where every method carries an `effect`.
- Agent trace vocabulary: `ToolCall`, `Tool` and `AgentSession`; `AgentFinding` gains `tool_calls` and a `verifier_report`; `trace_id` is no longer tied to MLflow. Every change adds; no existing property changed, so there is no migration.
- `loaders/platform_to_rows.py` turns hydrocouple.platform revision and attempt manifests into rows with the person, AI agent, agent session and tool call they name. `reference.artifact_lineage` and `reference.finding_lineage` are generated for Databricks, DuckDB and the SQLite snapshot.
- `platform_to_rows --journal` loads the openswmm.mcp tool-call journal as `ToolCall` and `Tool` rows; a line with no platform agent session is refused.
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
