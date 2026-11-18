"""Cross-layer regression tests for governed SWMM snapshots."""

from contextlib import closing
import json
from pathlib import Path
import shutil
import sqlite3

import pytest

from deploy.sqlite_releases import activate, register
from generators.linkml_to_sqlite import build
from generators.profile import geometry_values, source_profile
from loaders.source_records import input_bundle, read_records

ROOT = Path(__file__).resolve().parents[1]
ROWS = ROOT / "examples/site_drainage/rows"


@pytest.fixture(scope="module")
def snapshot(tmp_path_factory):
    path = tmp_path_factory.mktemp("snapshot") / "first.sqlite"
    build(ROWS, path)
    return path


def test_registry_lineage_and_native_evidence(snapshot):
    with closing(sqlite3.connect(snapshot)) as db:
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert db.execute("SELECT count(*) FROM reference__violations").fetchone() == (
            0,
        )
        assert db.execute("SELECT count(*) FROM reference__rule").fetchone()[0] >= 41
        assert db.execute(
            "SELECT count(*) FROM analysis__result_lineage"
        ).fetchone() == (383,)
        assert db.execute(
            "SELECT count(*) FROM analysis__result_lineage WHERE interval_start IS NOT NULL AND interval_end IS NOT NULL"
        ).fetchone() == (23,)
        assert (
            db.execute("""SELECT count(*) FROM analysis__result_lineage v
          JOIN analysis__input_record s ON v.source_record=s.iri AND v.revision_iri=s.of_revision
          JOIN reference__agent a ON a.iri=v.agent_iri JOIN analysis__result_artifact r ON r.was_generated_by=v.attempt_iri
          WHERE v.property_iri LIKE '%/concentration' AND v.unit='unit:MilliGM-PER-L' AND s.section='OUTFALLS'""").fetchone()
            == (360,)
        )
        assert db.execute(
            "SELECT DISTINCT property_kind FROM observation__observed_property WHERE pollutant IS NOT NULL ORDER BY 1"
        ).fetchall() == [("CONCENTRATION",), ("MASS_LOADING",)]
        assert db.execute(
            "SELECT count(DISTINCT iri) FROM reference__label WHERE lower(label) IN ('rain gauge','rain gage')"
        ).fetchone() == (1,)
        assert (
            db.execute("""SELECT count(DISTINCT iri) FROM reference__binding WHERE catalog_path IN
                             ('node.type','link.type','node.outfall.type')""").fetchone()
            == (3,)
        )
        assert db.execute(
            "SELECT json_extract(loadings,'$[0]') FROM model__swmm_subcatchment WHERE element_id='S1'"
        ).fetchone() == ("TSS=80",)
        assert (
            sum(
                n
                for (n,) in db.execute(
                    "SELECT json_array_length(coverage) FROM model__swmm_subcatchment WHERE coverage IS NOT NULL"
                )
            )
            == 10
        )


def test_rules_detect_broken_endpoint_and_wrong_system(snapshot):
    with closing(sqlite3.connect(snapshot)) as db:
        db.execute(
            "DELETE FROM topology__edge WHERE rowid=(SELECT min(rowid) FROM topology__edge WHERE relation='CONNECTS_TO')"
        )
        assert (
            db.execute(
                "SELECT count(*) FROM reference__violation_link_two_endpoints"
            ).fetchone()[0]
            >= 1
        )
        db.execute("""INSERT INTO topology__water_system(iri,class_iri,current_revision)
                SELECT 'wsys:system/demo/other',class_iri,current_revision FROM topology__water_system LIMIT 1""")
        db.execute(
            "UPDATE topology__water_system SET current_revision=NULL WHERE iri <> 'wsys:system/demo/other'"
        )
        assert (
            db.execute(
                "SELECT count(*) FROM reference__violation_represented_by_current_revision"
            ).fetchone()[0]
            >= 1
        )
        db.rollback()


def test_releases_are_idempotent_and_rollback_is_audited(snapshot, tmp_path):
    directory = tmp_path / "registry"
    first = register(directory, snapshot, "publisher")
    changed = tmp_path / "second.sqlite"
    shutil.copyfile(snapshot, changed)
    with closing(sqlite3.connect(changed)) as db, db:
        db.execute("INSERT INTO reference__release VALUES ('review','second release')")
    second = register(directory, changed, "publisher")
    assert second != first and register(directory, snapshot, "replay") == first
    activate(directory, first, "reviewer")
    activate(directory, first, "replay")
    with closing(sqlite3.connect(directory / "releases.sqlite")) as db:
        assert db.execute("SELECT digest,actor FROM active").fetchone() == (
            first,
            "reviewer",
        )
        assert db.execute("SELECT digest FROM changes ORDER BY id").fetchall() == [
            (first,),
            (second,),
            (first,),
        ]


def test_partial_export_is_not_published(tmp_path):
    rows = tmp_path / "rows"
    shutil.copytree(ROWS, rows)
    file = rows / "reference.agent.jsonl"
    file.write_text(file.read_text().replace("loaders.swmm_to_rows", "renamed-agent"))
    output = tmp_path / "rejected.sqlite"
    with pytest.raises(ValueError, match="Row digest mismatch"):
        build(rows, output)
    assert not output.exists()


def test_profile_and_dependency_evidence(tmp_path):
    import yaml

    src = tmp_path / "model.inp"
    src.write_text(
        '[TIMESERIES]\nR FILE "rain data.dat"\n[FUTURE]\nX "semi;colon" ;comment\n'
    )
    records = read_records(src)
    assert records[1].tokens == ["X", "semi;colon"] and records[1].line == 4
    with pytest.raises(FileNotFoundError, match="rain data.dat"):
        input_bundle(src, records)
    forcing = tmp_path / "rain data.dat"
    forcing.write_text("one")
    _, first = input_bundle(src, records)
    forcing.write_text("two")
    _, second = input_bundle(src, records)
    assert first != second
    assert geometry_values("RECT_TRIANG", [10, 5, 2, 1], 0.3048)["geom3"] == 0.6096
    assert geometry_values("CUSTOM", [10, 5, 0, 0], 0.3048)["geom2"] == 5
    extension = ROOT / "schema/profiles/openswmm-2d.yaml"
    assert "2D_QUADS" in source_profile(extension)["sections"]
    assert "2D_QUADS" not in source_profile()["sections"]
    invalid = yaml.safe_load(extension.read_text())
    invalid["sections"] = {"CONDUITS": {}}
    other = tmp_path / "bad.yaml"
    other.write_text(yaml.safe_dump(invalid))
    with pytest.raises(ValueError, match="override"):
        source_profile(other)


def test_native_pollutograph_matches_every_report_period():
    engine = pytest.importorskip("openswmm.engine")
    output = next((ROWS / "artifacts").glob("*.out"))
    records = [
        json.loads(line)
        for line in (ROWS / "observation.observation.jsonl").read_text().splitlines()
    ]
    samples = [r for r in records if r["property_iri"].endswith("/concentration")]
    with engine.OutputReader(str(output)) as reader:
        assert [r["phenomenon_time"] for r in samples] == [
            str(t) for t in reader.period_times
        ]
        native = reader.node_series("O1", int(engine.OutNodeVar.POLLUT_BASE))
        assert [r["value"] for r in samples] == pytest.approx(
            list(native), rel=1e-7, abs=1e-9
        )


@pytest.mark.parametrize(
    ("name", "sections"),
    [
        ("divider", {"DIVIDERS"}),
        ("lid", {"LID_CONTROLS", "LID_USAGE"}),
        ("inlet", {"STREETS", "INLETS", "INLET_USAGE"}),
        (
            "structures",
            {"PUMPS", "STORAGE", "ORIFICES", "WEIRS", "OUTLETS", "CONTROLS"},
        ),
        ("hydrology", {"AQUIFERS", "GROUNDWATER", "SNOWPACKS", "PATTERNS"}),
        ("groundwater_2d", {"2D_QUADS", "2D_AQUIFER", "GW_INITIAL_QUALITY"}),
    ],
)
def test_native_fixture_sections_reach_sqlite(name, sections, tmp_path):
    pytest.importorskip("openswmm.engine")
    from loaders.swmm_to_rows import Loader

    src = ROOT / "tests/fixtures/native" / (name + ".inp")
    extension = (
        ROOT / "schema/profiles/openswmm-2d.yaml" if name == "groundwater_2d" else None
    )
    load = Loader(src, "fixture", name, extension)
    load.load_static()
    load.write(tmp_path / "rows")
    snapshot = tmp_path / "model.sqlite"
    build(tmp_path / "rows", snapshot)
    with closing(sqlite3.connect(snapshot)) as db:
        actual = dict(
            db.execute(
                "SELECT section,count(*) FROM analysis__input_record GROUP BY section"
            )
        )
        assert sections <= actual.keys()
        assert sum(actual.values()) == len(read_records(src))
        if name == "structures":
            for table in ("pump", "orifice", "weir", "outlet", "storage_unit"):
                assert (
                    db.execute(f"SELECT count(*) FROM topology__{table}").fetchone()[0]
                    > 0
                )


def test_scope_and_repeat_executions(tmp_path):
    pytest.importorskip("openswmm.engine")
    from loaders.swmm_to_rows import Loader

    inp = ROWS.parent / "model.inp"
    a, b = [Loader(inp, "utility", system) for system in ("first", "second")]
    assert a.asset_iri("J1") != b.asset_iri("J1")
    assert a.asset_iri("J1", "link") != a.asset_iri("J1", "node")
    assert a.rev_iri != b.rev_iri and a.sha == b.sha
    a.load_static()
    a.run_local(tmp_path)
    a.run_local(tmp_path)
    attempts = a.rows["analysis.attempt"]
    assert len({r["iri"] for r in attempts}) == 2
    assert all(r["used"] == [a.rev_iri] for r in attempts)


@pytest.mark.parametrize("method", ["MODIFIED_HORTON", "MODIFIED_GREEN_AMPT"])
def test_native_infiltration_alias_round_trip(method, tmp_path):
    pytest.importorskip("openswmm.engine")
    from loaders.swmm_to_rows import Loader

    src = tmp_path / "infiltration.inp"
    lines = []
    section = None
    for line in (ROWS.parent / "model.inp").read_text().splitlines():
        if line.startswith("["):
            section = line.strip()
        if section == "[OPTIONS]" and line.startswith("INFILTRATION"):
            line = "INFILTRATION " + method
        elif (
            section == "[INFILTRATION]"
            and method == "MODIFIED_GREEN_AMPT"
            and line.strip()
            and not line.startswith(("[", ";"))
        ):
            line = line.split()[0] + " 3.5 0.5 0.25"
        lines.append(line)
    src.write_text("\n".join(lines) + "\n")
    loader = Loader(src, "fixture", "alias")
    loader.load_static()
    assert {
        r.get("infiltration_model") for r in loader.rows["topology.subcatchment"]
    } == {method}


def test_equivalent_native_lengths_in_us_and_si(tmp_path):
    pytest.importorskip("openswmm.engine")
    from loaders.swmm_to_rows import Loader

    lengths = []
    for flow, length, diameter in (("CFS", 100, 3), ("CMS", 30.48, 0.9144)):
        src = tmp_path / (flow + ".inp")
        src.write_text(
            f"[OPTIONS]\nFLOW_UNITS {flow}\nFLOW_ROUTING DYNWAVE\n[JUNCTIONS]\nJ 5 10\n[OUTFALLS]\nO 0 FREE NO\n[CONDUITS]\nC J O {length} 0.013 0 0\n[XSECTIONS]\nC CIRCULAR {diameter} 0 0 0 1\n"
        )
        loader = Loader(src, "test", flow)
        loader.load_static()
        row = loader.rows["topology.conduit"][0]
        lengths.append((row["length"], row["geom1"]))
    assert lengths[0] == pytest.approx(lengths[1], rel=1e-6)


def test_geopackage_match_is_complete_and_scoped(snapshot, tmp_path):
    from loaders.geopackage import bind, TABLES

    package = tmp_path / "model.gpkg"
    with (
        closing(sqlite3.connect(package)) as gp,
        gp,
        closing(sqlite3.connect(":memory:")) as db,
    ):
        with closing(sqlite3.connect(snapshot)) as source:
            source.backup(db)
        gp.execute("CREATE TABLE gpkg_contents(table_name TEXT)")
        gp.execute("INSERT INTO gpkg_contents VALUES('nodes')")
        gp.execute("CREATE TABLE simulations(simulation_id TEXT, inp_hash TEXT)")
        revision, digest = db.execute(
            "SELECT iri,input_sha256 FROM analysis__revision"
        ).fetchone()
        gp.execute("INSERT INTO simulations VALUES('demo',?)", (digest,))
        for kind, (table, key) in TABLES.items():
            gp.execute(f"CREATE TABLE {table}(simulation_id TEXT, {key} TEXT)")
            ids = [
                (r[0],)
                for r in db.execute(
                    "SELECT element_id FROM model__swmm_element WHERE iri LIKE ?",
                    ("%/" + kind + "/%",),
                )
            ]
            gp.executemany(f"INSERT INTO {table} VALUES('demo',?)", ids)
        gp.commit()
        bind(db, package, "demo", revision)
        assert db.execute(
            "SELECT count(*) FROM model__geopackage_binding"
        ).fetchone() == (31,)
        db.rollback()
        db.execute("DROP TABLE IF EXISTS model__geopackage_binding")
        gp.execute("DELETE FROM nodes WHERE rowid=(SELECT min(rowid) FROM nodes)")
        gp.commit()
        with pytest.raises(ValueError, match="Missing node"):
            bind(db, package, "demo", revision)


def test_missing_result_artifact_breaks_lineage(snapshot):
    with closing(sqlite3.connect(snapshot)) as db:
        db.execute("DELETE FROM analysis__result_artifact")
        assert db.execute(
            "SELECT count(*) FROM reference__violation_swmm_result_lineage"
        ).fetchone() == (383,)
        db.rollback()
