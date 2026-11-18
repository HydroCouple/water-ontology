"""Resolve revision-qualified ontology objects against an explicitly selected OpenSWMM simulation."""

from contextlib import closing
from pathlib import Path
import sqlite3

TABLES = {
    "node": ("nodes", "node_id"),
    "link": ("links", "link_id"),
    "subcatchment": ("subcatchments", "subcatch_id"),
    "gage": ("rain_gages", "gage_id"),
}


def bind(db, path, simulation, revision):
    url = Path(path).resolve().as_uri()
    with closing(sqlite3.connect(url + "?mode=ro", uri=True)) as gpkg:
        gpkg.execute("BEGIN")
        if not gpkg.execute("SELECT 1 FROM gpkg_contents LIMIT 1").fetchone():
            raise ValueError("Empty GeoPackage contents")
        known = gpkg.execute(
            "SELECT inp_hash FROM simulations WHERE simulation_id=?", (simulation,)
        ).fetchone()
        # Model-only packages can have objects under a simulation key before a results run exists.
        if known is None and not any(
            gpkg.execute(
                f"SELECT 1 FROM {table} WHERE simulation_id=? LIMIT 1", (simulation,)
            ).fetchone()
            for table, _ in TABLES.values()
        ):
            raise ValueError(f"Unknown GeoPackage simulation {simulation!r}")
        digest = db.execute(
            "SELECT input_sha256 FROM analysis__revision WHERE iri=?", (revision,)
        ).fetchone()
        if digest is None:
            raise ValueError("Unknown ontology revision")
        if known and known[0] and digest[0] and known[0].lower() != digest[0].lower():
            raise ValueError(
                "Simulation input digest differs from the ontology revision"
            )
        db.execute("""CREATE TABLE model__geopackage_binding(
            revision TEXT, element_iri TEXT, file_uri TEXT, simulation_id TEXT,
            native_table TEXT, native_id TEXT, PRIMARY KEY(revision, element_iri)) STRICT""")
        for kind, (table, key) in TABLES.items():
            # Restrict to the expected class hierarchy, independent of historical IRI syntax.
            parent = {
                "node": "SwmmNode",
                "link": "SwmmLink",
                "gage": "SwmmGage",
                "subcatchment": "SwmmSubcatchment",
            }[kind]
            elements = db.execute(
                """SELECT e.iri,e.element_id FROM model__swmm_element e
                JOIN reference__class_closure c ON c.class_iri=e.class_iri
                WHERE e.revision=? AND c.ancestor_iri=?""",
                (revision, "https://w3id.org/wsys/model/" + parent),
            )
            ids = {}
            for (eid,) in gpkg.execute(
                f"SELECT {key} FROM {table} WHERE simulation_id=?", (simulation,)
            ):
                if eid.casefold() in ids:
                    raise ValueError(f"Ambiguous {kind} ID {eid!r} in {simulation}")
                ids[eid.casefold()] = eid
            matched = set()
            for iri, eid in elements:
                native = ids.get(eid.casefold())
                if native is None:
                    raise ValueError(f"Missing {kind} {eid!r} in {simulation}")
                matched.add(native)
                db.execute(
                    "INSERT INTO model__geopackage_binding VALUES(?,?,?,?,?,?)",
                    (revision, iri, url, simulation, table, native),
                )
            if matched != set(ids.values()):
                raise ValueError(f"Unmatched native {kind} objects in {simulation}")
