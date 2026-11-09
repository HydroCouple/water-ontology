# water-ontology

LinkML source of truth for the water-systems ontology that backs the Databricks
data model, Unity Catalog semantic layer, and agent tool registry.

**One source, many targets.** `schema/*.yaml` is edited by people. Everything
else is generated: OWL and SHACL for reasoning and validation, Delta DDL and
Unity Catalog comments/tags/glossary for the physical model, prompt context
for agents, and the `wsys:model` element vocabulary from `openswmm.engine`'s
API catalog.

## Layout

| Path | What |
| --- | --- |
| `schema/core.yaml` | Entity, SpatialEntity, Asset, DerivedEntity; lifecycle and provenance slots; shared prefixes |
| `schema/topology.yaml` | Nodes, links, terminals, zones, Edge (one directed graph for connectivity, containment, attachment, zone membership) |
| `schema/observation.yaml` | Sensors, observed properties, observations *(step 3)* |
| `schema/event.yaml` | Event taxonomy, lifecycle states, evidence and causal links *(step 3)* |
| `schema/model.yaml` | **Generated** from `openswmm.engine.catalog`; do not edit *(step 2)* |
| `generators/` | LinkML → Delta DDL + UC metadata, catalog → LinkML, LinkML → prompt |
| `imports/` | Pinned copies of SAREF4WATR, SOSA, PROV-O for mapping checks |
| `tests/` | Lint, SHACL over fixtures, catalog round-trip |
| `dist/` | Build output (gitignored) |

## Build

```bash
source ./activate.sh     # venv on PATH; caches kept inside the repo
make                     # lint + owl + shacl + sql into dist/
```

## Conventions

- Class and slot `description`s are written for an LLM reader; they become Unity Catalog comments and glossary definitions verbatim.
- Enum values are UPPER_SNAKE so they survive into SQL unchanged; each enum becomes a `reference.*` table.
- Every numeric slot carries a `unit` (UCUM); the Delta generator maps it to a QUDT IRI.
- Keep the class tree shallow (Esri Utility Network lesson): variants are attributes, not subclasses.
- Connectivity is never stored on an asset; it is an `Edge` row.
- Breaking changes (rename/remove) need a script in `migrations/` or CI fails.
