# Water Systems Ontology build. `source ./activate.sh` first.
SCHEMAS   := $(wildcard schema/*.yaml)
ROOT      := schema/topology.yaml      # module that imports everything released so far
DIST      := dist

.PHONY: all lint owl shacl sql docs clean test

all: lint owl shacl sql delta

lint:
	linkml-lint --config .linkmllint.yaml $(SCHEMAS)

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
