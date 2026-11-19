# SWMM implementation and SQLite release guide

The 0.2 review branch builds a queryable SQLite snapshot directly from LinkML and a SWMM export. The committed drainage run contains 962 rows in 26 tables, including 396 source records, 23 hydraulic maxima and 360 native outfall TSS concentrations. The source and binary results accompany the rows. Repeating a run produces a new execution identifier.

## Reproduce the release locally

From a source checkout, install the development dependencies and build the SQLite snapshot. The committed example works without an engine installation. The SQLite library must support STRICT tables and JSON functions.

```sh
python -m pip install -e '.[dev]'
make sqlite inventory
sqlite3 dist/water-ontology.sqlite 'SELECT count(*) FROM reference__violations'
make shacl
python -m loaders.rows_to_rdf examples/site_drainage/rows --shapes dist/wsys.shacl.ttl
```

For a fresh run, install the engine extra. Use a separate output directory for each export that should remain independently accessible. Supply source rights and license when known. The binary artifact retains every native report period, while JSONL materializes the requested series and the outfall pollutographs.

```sh
python -m pip install -e '.[dev,engine]'
python -m loaders.swmm_to_rows examples/site_drainage/model.inp --utility demo --system site-drainage --run --series node J1 -o dist/second-run
python -m generators.linkml_to_sqlite dist/second-run dist/second-run.sqlite
```

## Find names, bindings and lineage

```sql
SELECT iri, role, label, scheme FROM reference__label
WHERE iri IN (SELECT iri FROM reference__label WHERE lower(label) = 'rain gauge');

SELECT catalog_path, iri, table_name, column_name FROM reference__binding
WHERE catalog_path IN ('node.type','node.outfall.type','link.type')
ORDER BY catalog_path, table_name;

SELECT entity_iri, value, unit, native_unit, conversion_factor, interval_start, interval_end,
       source_section, source_line, revision_iri, input_sha256, attempt_iri, agent_iri, output_sha256
FROM analysis__result_lineage WHERE property_iri='wsys:prop/depth'
ORDER BY value DESC LIMIT 1;
```

Use the observation's source line to recover its native object. Follow the output checksum to the retained binary for the actual result. Each source record includes its original text, section, line number and ordered tokens. A model revision identifies the utility, system and hashed input bundle. The source file has its own digest. Assets inferred from the model carry UNKNOWN lifecycle status. Geographic interpretation requires a separately evidenced CRS.

Use `--geopackage PATH --simulation ID` on the SQLite generator to bind all native nodes, links, rain gages and subcatchments. It checks for missing, extra and ambiguous objects within the selected simulation. A package with an input hash must match the revision. For a model-only package with no hash, evidence consists of the simulation key and exact native object set. Binding leaves the original GeoPackage intact.

## Standards, review and extension

| Evidence | Implementation | Review boundary |
| --- | --- | --- |
| Terms and names | The glossary registry, SQLite reference tables and Delta terminology records derive from the same schema. One preferred label per language, scoped notations and typed mapping predicates preserve distinctions. | A term's presence in a published vocabulary establishes its identifier. Equivalence of meaning still requires domain review. |
| Versioned sources | Content locks cover the engine catalog and platform schemas. The EPA parser keyword inventory pins SWMM 5.2.4. The HydroCouple term list and RDFLib packaged namespace declarations carry source revisions or versions and checksums. | `dist/release/inventory.json` names external targets awaiting review, source-only fields and catalog members with no tested binding. |
| Engineering evidence | SQLite rules, shared SHACL shapes, native period-by-period checks, scope tests, paired SI/US lengths, 2D groundwater coverage on OpenSWMM 6.0.0a4.dev1 and the additional heat and layered transport options on 6.0.0a4.dev2 or later and a GeoPackage identity test run from source. | The EPA inventory enumerates section keywords. It does not constitute a field-by-field parity claim for EPA or all OpenSWMM numerical services. |
| Governance | `schema/mappings/decisions.yaml` records ownership, authority, decision scope and proposed status. The revision accepts source rights and license. Snapshot publication records actor, digest and release history. | Individual assertion reviews record subject, predicate, object, reviewer, date, target version and evidence. Approval and deprecation require these fields. Database file permissions and workspace grants remain the responsibility of the deploying organization. |

The base adapter reads each section and preserves unknown tokens. Add a versioned section profile under `schema/profiles/` with a distinct ID, version, parent ID, owner, source and section definitions. Supply it using `--profile`. The loader rejects attempts to redefine a base section. The optional OpenSWMM 2D profile exercises this path. New interpreted results and unit conversions require native comparison fixtures. The field inventory distinguishes named source fields from preserved tokens and records fixture evidence for API members.

## Migration and publication

Use a separate checkout of the previous release to generate both migration reports. The semantic report compares per-class properties, ranges, units, cardinality and historical enum declarations. The identity report matches source and system before proposing redirects and preserves ambiguity by class or table. Keep old execution records with their original occurrence identifiers.

```sh
python -m migrations.semantic_diff ../previous/schema/wsys.yaml schema/wsys.yaml dist/release/semantic-diff.json
python -m migrations.v0_2 ../previous/examples/site_drainage/rows examples/site_drainage/rows dist/release/identity-crosswalk.json
python -m deploy.sqlite_releases register dist/releases dist/water-ontology.sqlite --actor curator
python -m deploy.sqlite_releases activate dist/releases PREVIOUS_SHA256 --actor curator
```

Registering the same bytes again leaves the active release and history intact. Activation verifies the stored file and records a new event for a changed active release. The registry's `active` view gives the selected digest for each system. Published snapshots retain artifact references. Distribute the matching `rows/artifacts/` directory alongside the snapshot when consumers need to open the binary results.

For Delta, generate with `make delta assertions glossary`, apply to a staging catalog and compare the exported results and terminology with SQLite. Release-scoped terminology records include a content digest, mapping predicate, physical binding or decision. Workspace execution, access grants, native glossary API publication and production cutover require deployment-specific verification. The local release registry supports SQLite rollback. A Databricks rollback must be rehearsed against the deployment's prior catalog or table versions.
