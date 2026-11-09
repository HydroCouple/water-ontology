# Water Systems Ontology build. `source ./activate.sh` first.
SCHEMAS   := $(wildcard schema/*.yaml)
ROOT      := schema/wsys.yaml          # release root: imports every module
DIST      := dist

.PHONY: all lint owl shacl sql docs clean test

all: model-check lint owl shacl sql delta

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

CATALOG ?= ../openswmm.engine/python/openswmm/engine/catalog.json

model:
	python -m generators.catalog_to_linkml --catalog $(CATALOG)

model-check:
	python -m generators.catalog_to_linkml --catalog $(CATALOG) --check
