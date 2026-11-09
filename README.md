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
| `schema/model.yaml` | **Generated** from `openswmm.engine.catalog` by `make model`; do not edit. `model.lock.json` pins the catalog revision and hash |
| `schema/mappings/swmm_to_wsys.yaml` | Hand-curated: which `wsys:` topology class each generated `Swmm*` class means |
| `schema/analysis.yaml` | `Revision`, `Run`, `Attempt` (stub; full classes generated from hydrocouple.platform JSON Schemas in step 3) |
| `schema/wsys.yaml` | Release root that imports every module; build targets point here |
| `generators/` | LinkML → Delta DDL + UC metadata, catalog → LinkML, LinkML → prompt |
| `imports/` | Pinned copies of SAREF4WATR, SOSA, PROV-O for mapping checks |
| `tests/` | Lint, SHACL over fixtures, catalog round-trip |
| `dist/` | Build output (gitignored) |

## Build

```bash
source ./activate.sh     # venv on PATH; caches kept inside the repo
make                     # model-check + lint + owl + shacl + sql + delta into dist/
make model               # regenerate schema/model.yaml from the OpenSWMM catalog (CATALOG=path/to/catalog.json)
make delta               # Delta DDL + Unity Catalog metadata into dist/delta
make test
```

## Conventions

- Class and slot `description`s are written for an LLM reader; they become Unity Catalog comments and glossary definitions verbatim.
- Enum values are UPPER_SNAKE so they survive into SQL unchanged; each enum becomes a `reference.*` table.
- Every numeric slot carries a `unit` (UCUM); the Delta generator maps it to a QUDT IRI.
- Keep the class tree shallow (Esri Utility Network lesson): variants are attributes, not subclasses.
- Connectivity is never stored on an asset; it is an `Edge` row.
- Breaking changes (rename/remove) need a script in `migrations/` or CI fails.

## Generated model layer

`generators/catalog_to_linkml.py` reads the OpenSWMM API catalog and emits `schema/model.yaml`:

- element kinds (`node`, `link`, `subcatchment`, `gage`, `pollutant`, ...) become `Swmm*` classes under `SwmmElement` (an `Entity` keyed by `revision` + `element_id`);
- subtype views (`node.outfall`, `link.orifice`, ...) become subclasses, and `NodeType`/`LinkType` codes without a view (`JUNCTION`, `CONDUIT`) still get a class;
- component views (`node.stats`, `link.xsect`, `subcatchment.infiltration`) become inlined classes, which the Delta generator renders as `STRUCT` columns;
- keyed item views (`subcatchment.coverage`, `loadings`) become keyed-map attributes;
- properties become attributes with `catalog_path`, `access`, `c_symbols`, `unit_kind` and `python_type` annotations, which the Delta generator turns into column tags; read-only engine results are marked `readonly`;
- referenced enums are copied verbatim; methods and services are behaviour and are only listed in the module annotations.

`make model-check` (and `tests/test_model_catalog.py`) fail when the catalog moves and `model.yaml` was not regenerated.
