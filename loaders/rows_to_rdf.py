"""Project the same exported JSONL instances to RDF for SHACL validation."""

import argparse
import json
from pathlib import Path

from rdflib import BNode, Graph, Literal, RDF, RDFS, URIRef

from generators.linkml_to_delta import DeltaGenerator, snake
from generators.profile import ROOT


def graph_from_rows(rows, schema=ROOT / "schema/wsys.yaml"):
    d = DeltaGenerator(str(schema), "water_core")
    sv = d.sv
    classes = sv.all_classes()
    types = sv.all_types()
    tables = {d.module_of(c) + "." + snake(c): c for c in d.concrete_classes()}
    graph = Graph()
    for cls in classes.values():
        if cls.is_a:
            graph.add(
                (
                    URIRef(sv.get_uri(cls, expand=True)),
                    RDFS.subClassOf,
                    URIRef(sv.get_uri(classes[cls.is_a], expand=True)),
                )
            )
    slots = {c: {s.name: s for s in sv.class_induced_slots(c)} for c in classes}

    def uri(raw):
        return URIRef(sv.expand_curie(str(raw)))

    def emit(cls, row):
        subject = uri(row["iri"]) if row.get("iri") else BNode()
        graph.add((subject, RDF.type, URIRef(sv.get_uri(classes[cls], expand=True))))
        for key, raw in row.items():
            slot = slots[cls][key]
            if raw is None or slot.identifier:
                continue
            pred = URIRef(sv.get_uri(slot, expand=True))
            for item in raw if slot.multivalued else [raw]:
                if isinstance(item, dict) and slot.range in classes:
                    obj = emit(slot.range, item)
                elif slot.range in classes or slot.range in {
                    "IRI",
                    "uri",
                    "uriorcurie",
                    "curie",
                }:
                    obj = uri(item)
                elif slot.range in types:
                    datatype = types[slot.range]
                    while datatype.uri is None and datatype.typeof:
                        datatype = types[datatype.typeof]
                    obj = Literal(
                        item, datatype=uri(datatype.uri) if datatype.uri else None
                    )
                else:
                    obj = Literal(item)
                graph.add((subject, pred, obj))
        return subject

    for path in sorted(Path(rows).glob("*.jsonl")):
        cls = tables[path.stem]
        with path.open() as stream:
            for line in stream:
                emit(cls, json.loads(line))
    return graph


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("rows", type=Path)
    p.add_argument("-o", "--out", type=Path)
    p.add_argument("--shapes", type=Path)
    args = p.parse_args()
    graph = graph_from_rows(args.rows)
    if args.out:
        graph.serialize(destination=args.out, format="turtle")
    if args.shapes:
        from pyshacl import validate

        conforms, _, report = validate(graph, shacl_graph=str(args.shapes))
        if not conforms:
            raise SystemExit(report)
