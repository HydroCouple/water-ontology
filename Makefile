# Water Systems Ontology build. `source ./activate.sh` first.
SCHEMAS   := $(wildcard schema/*.yaml)
ROOT      := schema/wsys.yaml          # release root: imports every module
DIST      := dist

.PHONY: all lint owl shacl sql docs clean test

all: model-check platform-check lint owl shacl sql delta assertions diagrams prompt glossary

lint:
	for f in schema/*.yaml; do linkml-lint --config .linkmllint.yaml --ignore-warnings $$f || exit 1; done

owl: | $(DIST)
	gen-owl --mergeimports $(ROOT) > $(DIST)/wsys.owl.ttl

shacl: | $(DIST)
	gen-shacl --mergeimports $(ROOT) > $(DIST)/wsys.shacl.ttl

sql: | $(DIST)
	gen-sqlddl --mergeimports --dialect postgresql $(ROOT) > $(DIST)/wsys.postgres.sql

docs: | $(DIST)
	gen-doc --mergeimports -d $(DIST)/docs $(ROOT)

test:
	pytest -q

$(DIST):
	mkdir -p $(DIST)

clean:
	rm -rf $(DIST)

delta: | $(DIST)
	python -m generators.linkml_to_delta $(ROOT) --catalog water_core -o $(DIST)/delta

CATALOG ?= vendor/openswmm/catalog.json
ENGINE_REPO ?= ../openswmm.engine

model:
	python -m generators.catalog_to_linkml --catalog $(CATALOG)

PLATFORM_SCHEMAS ?= vendor/hydrocouple.platform/schemas
PLATFORM_REPO ?= ../hydrocouple.platform

platform:
	python -m generators.platform_to_linkml --schemas $(PLATFORM_SCHEMAS)

platform-check:
	python -m generators.platform_to_linkml --schemas $(PLATFORM_SCHEMAS) --check

model-check:
	python -m generators.catalog_to_linkml --catalog $(CATALOG) --check

# Refresh the vendored inputs from sibling checkouts, then regenerate. Review the diff before committing.
update-vendor:
	cp $(ENGINE_REPO)/python/openswmm/engine/catalog.json vendor/openswmm/catalog.json
	cd $(ENGINE_REPO) && printf 'repo: HydroCouple/openswmm.engine\nbranch: %s\ncommit: %s\npath: python/openswmm/engine/catalog.json\n' "$$(git branch --show-current)" "$$(git rev-parse HEAD)" > $(CURDIR)/vendor/openswmm/SOURCE.yaml
	cp $(PLATFORM_REPO)/src/hydrocouple_platform/schemas/platform/*.json vendor/hydrocouple.platform/schemas/
	cd $(PLATFORM_REPO) && printf 'repo: HydroCouple/hydrocouple.platform\nbranch: %s\ncommit: %s\npath: src/hydrocouple_platform/schemas/platform\n' "$$(git branch --show-current)" "$$(git rev-parse HEAD)" > $(CURDIR)/vendor/hydrocouple.platform/SOURCE.yaml
	$(MAKE) model platform

diagrams:
	python -m generators.linkml_to_diagrams $(ROOT) -o docs/diagrams

docs-serve:
	mkdocs serve

assertions: | $(DIST)
	python -m generators.linkml_to_assertions $(ROOT) --catalog water_core -o $(DIST)/assertions
	cp $(DIST)/assertions/RULES.md docs/rules.md

# End-to-end example. `example` needs the engine (pip install -e '.[engine]'); `check-example` only needs the committed rows.
EXAMPLE ?= examples/site_drainage

example:
	python -m loaders.swmm_to_rows $(EXAMPLE)/model.inp --utility demo --system site-drainage --run -o $(EXAMPLE)/rows

check-example:
	python -m harness.duckdb_harness $(EXAMPLE)/rows

prompt: | $(DIST)
	python -m generators.linkml_to_prompt $(ROOT) -o docs/agent-context.md
	mkdir -p $(DIST)/prompt && cp docs/agent-context.md $(DIST)/prompt/ontology_context.md

glossary: | $(DIST)
	python -m generators.linkml_to_glossary $(ROOT) -o $(DIST)/glossary
