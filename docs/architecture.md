# How it fits together

The ontology is the one place meaning is written down. Everything the agents touch on Databricks — tables, comments, glossary, tool contracts, prompt context — is generated from it, and everything the agents produce — findings, predictions, events — is written back in its terms. The simulation engine and the execution platform are consumed, not rebuilt.

## From source systems to agents

```mermaid
flowchart TB
  subgraph sources["Sources"]
    scada[SCADA / historian]
    gis[GIS or ArcGIS Utility Network]
    cmms[CMMS / CIS / LIMS]
    inp[SWMM .inp and HydroCouple compositions]
  end

  subgraph ontology["water-ontology (this repo)"]
    yaml[LinkML schema/*.yaml]
    vendor[vendor/: OpenSWMM catalog, platform JSON Schemas]
    gen[generators/]
    vendor --> gen
    yaml --> gen
    gen --> owl[OWL + SHACL]
    gen --> ddl[Delta DDL, comments, tags]
    gen --> gloss[UC Glossary and Domains]
    gen --> prompt[Agent prompt context]
    gen --> figs[These diagrams]
  end

  subgraph dbx["Databricks (Unity Catalog)"]
    direction TB
    bronze[(water_raw: bronze)]
    subgraph core["water_core"]
      topo[(topology, observation, event)]
      model[(model: SwmmElement tables)]
      analysis[(analysis: Revision, Run, Attempt, Study, AgentFinding)]
      coupling[(coupling)]
      ml[(ml: models, predictions, drift)]
      ref[(reference: enums, asset_class, ontology_violation)]
    end
    semantic[water_semantic: metric views, Genie spaces]
    tools[water_agents: UC functions, tool_catalog, vector indexes]
    agents[Agents: supervisor, grounder, specialists, verifier]
    mlflow[MLflow 3: tracing, evaluation, UC Models]
    gateway[Unity AI Gateway]
  end

  subgraph exec["Execution (consumed, not rebuilt)"]
    engine[openswmm.engine + openswmm.mcp]
    platform[hydrocouple.platform: revisions, runs, attempts, studies]
  end

  sources --> bronze --> core
  ddl --> core
  gloss --> semantic
  prompt --> agents
  core --> semantic --> tools --> agents
  agents --> gateway
  agents --> mlflow
  tools -- interactive sessions --> engine
  tools -- submit_run / create_study --> platform
  platform -- attempt manifests, results --> analysis
  inp --> platform
  agents -- AgentFinding, proposed Setpoints, DETECTED events --> analysis
  ml --> tools
```

## How a question travels

```mermaid
sequenceDiagram
  autonumber
  actor Op as Operator
  participant Sup as Supervisor agent
  participant Gr as Entity grounder
  participant Cat as tool_catalog
  participant T as UC tool (isolation_trace)
  participant E as topology.edge
  participant P as hydrocouple.platform
  participant V as Verifier
  Op->>Sup: "If we close the Elm St gate, what floods in a 10-yr storm?"
  Sup->>Gr: ground "Elm St gate"
  Gr-->>Sup: wsys:asset/acme/OR-17 (Orifice, is_isolating, IN_SERVICE)
  Sup->>Cat: tools for Orifice, scenario, flooding
  Cat-->>Sup: isolation_trace@2, submit_run(swmm.simulation@1), report(flooding)
  Sup->>T: isolation_trace(OR-17)
  T->>E: CONNECTS_TO walk, stop at is_isolating
  E-->>T: upstream conduits and nodes
  Sup->>P: submit_run(revision=current, parameters={OR-17: closed, storm: 10yr}, tool_call_id=span)
  P-->>Sup: attempt manifest + flooding report
  Sup->>V: trace, IRIs, units, provenance
  V-->>Sup: passed: all numbers from attempt a-91, units m3, revision matches REPRESENTED_BY
  Sup-->>Op: 3 nodes flood (J-104, J-110, J-112), 420 m3, from attempt a-91 on revision r-7, assumptions listed
  Sup->>P: write AgentFinding + EXPLAINED_BY link
```

## Where each kind of knowledge lives

| Knowledge | Lives in | Reaches agents through |
| --- | --- | --- |
| What things are, how they relate, units, constraints | `schema/*.yaml` | Column comments and tags, glossary, `describe_ontology` tool, prompt context |
| What exists and how it is connected | `topology.*` tables and `topology.edge` | Lookup and traversal UC functions |
| How the system behaves | SWMM model elements (`model.*`) in a `Revision`; the engine | openswmm.mcp sessions; platform runs via `submit_run` |
| What is happening | `observation.*`, `event.*` | Time-series and event tools; `EVIDENCE_FOR` links |
| What was computed and why | `analysis.*` (attempt manifests with provenance), `ml.prediction` | Every answer cites attempt, model version, table version |
| What is allowed | `water_agents.tool_catalog`, Unity Catalog grants, AI Gateway, platform budgets | The agent can only call what is `active`; proposals need human approval |

## Reading the generated figures

- [Module map](diagrams/modules.md) — which schema files import which.
- [Knowledge graph](diagrams/knowledge-graph.md) — classes and the relations between them, in four focused views plus the full graph.
- [Relations](diagrams/relations.md) — allowed from/to classes per `EdgeRelation` and `EventLinkRelation`.
- Per-module class diagrams: [core](diagrams/core.md), [topology](diagrams/topology.md), [observation](diagrams/observation.md), [event](diagrams/event.md), [coupling](diagrams/coupling.md), [analysis](diagrams/analysis.md), [platform contracts](diagrams/analysis-platform.md), [SWMM model](diagrams/model.md), [ml](diagrams/ml.md).
