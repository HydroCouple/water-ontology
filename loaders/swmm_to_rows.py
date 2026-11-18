"""
SWMM model -> ontology rows (JSON Lines, one file per Delta table).

Opens an .inp with the OpenSWMM engine and writes:

  model.*        one row per element, with every property the generated
                 wsys:model class declares that this engine build can read
  analysis.*     the Revision (identified by the SHA-256 of the .inp), and with
                 --run a Run, an Attempt and simulated statistics as Observations
  topology.*     the assets the model implies (junctions, conduits, subcatchments,
                 gages, ...) in SI units, the WaterSystem, and every Edge:
                 CONNECTS_TO, DRAINS_TO, USES_GAGE, PART_OF_SYSTEM, REPRESENTED_BY

The same rows load into Delta on Databricks or into the local DuckDB harness.

A run made here is a LOCAL engine run. It is recorded with source_system
'local-engine' and has no platform manifests; runs of record go through
hydrocouple.platform and are ingested from its revision and attempt manifests.

Usage:
    python -m loaders.swmm_to_rows examples/site_drainage/model.inp --utility demo --system site-drainage --run -o examples/site_drainage/rows
"""
from __future__ import annotations

import argparse
import datetime as dt
from uuid import uuid4
from urllib.parse import quote
import json
import math
import os
import shutil
import tempfile
from collections import Counter
from filelock import FileLock
from collections import defaultdict
from pathlib import Path

from linkml_runtime.utils.schemaview import SchemaView

from generators.profile import source_profile, normalize_code, geometry_values
from loaders.source_records import read_records, input_bundle, file_digest
from generators.linkml_to_delta import DeltaGenerator, snake

HERE = Path(__file__).resolve().parent.parent
W = "https://w3id.org/wsys/"

NODE_CLASS = {"JUNCTION": ("SwmmJunction", "Junction"), "OUTFALL": ("SwmmOutfall", "Outfall"),
              "STORAGE": ("SwmmStorage", "StorageUnit"), "DIVIDER": ("SwmmDivider", "Divider")}
LINK_CLASS = {"CONDUIT": ("SwmmConduit", "Conduit"), "PUMP": ("SwmmPump", "Pump"), "ORIFICE": ("SwmmOrifice", "Orifice"),
              "WEIR": ("SwmmWeir", "Weir"), "OUTLET": ("SwmmOutlet", "Outlet")}
FLOW_TO_M3S = {"CFS": 0.028316846592, "GPM": 6.30901964e-5, "MGD": 0.0438126364, "CMS": 1.0, "LPS": 0.001, "MLD": 0.0115740741}


def annotations(slot) -> dict:
    a = slot.annotations
    if not a:
        return {}
    items = a._as_dict.items() if hasattr(a, "_as_dict") else dict(a).items()
    return {str(k): str(v.get("value") if isinstance(v, dict) else getattr(v, "value", v)) for k, v in items}


class Loader:
    def __init__(self, inp: Path, utility: str, system: str, extension=None):
        from openswmm import engine  # imported here so the rest of the repo works without the engine
        self.engine = engine
        self.inp = inp
        self.utility, self.system_id = utility, system
        self.d = DeltaGenerator(str(HERE / "schema" / "wsys.yaml"), "water_core")
        self.sv: SchemaView = self.d.sv
        self.rows: dict[str, list[dict]] = defaultdict(list)
        self.unread: dict[str, int] = defaultdict(int)
        self.notes: list[str] = []
        self.series = []
        self.output_artifacts = {}
        self.crs = None
        self.input_rights = None
        self.input_license = None
        self.records = read_records(inp)
        self.files, self.bundle_digest = input_bundle(inp, self.records)
        self.sha = self.files[0][2]
        self.scope = "/".join(quote(v, safe="") for v in (utility, system))
        self.rev_id = self.bundle_digest
        self.rev_iri = f"wsys:revision/{self.scope}/{self.rev_id}"
        self.sys_iri = f"wsys:system/{self.scope}"
        self.profile = source_profile(extension)
        self.mapping = self.profile["id"]
        self.import_iri = f"wsys:ingestion/{self.scope}/{self.rev_id}/{uuid4().hex}"
        self.loader_iri = "wsys:agent/software/swmm-to-rows"
        self.sources = {}
        self.identifiers = {}
        self.keyed = defaultdict(list)
        self.asset_source = {}
        object_sections = {"JUNCTIONS", "OUTFALLS", "DIVIDERS", "STORAGE", "CONDUITS",
                           "PUMPS", "ORIFICES", "WEIRS", "OUTLETS", "SUBCATCHMENTS",
                           "RAINGAGES", "POLLUTANTS", "LANDUSES", "CURVES", "TIMESERIES", "PATTERNS"}
        for record in self.records:
            if record.section in object_sections:
                kind, eid = record.objects(self.profile)[0]
                self.identifiers.setdefault((kind, eid.casefold()), eid)
                self.sources.setdefault((kind, eid.casefold()), self.record_iri(record.line))
            if record.section in ("COVERAGES", "LOADINGS") and len(record.tokens) >= 3:
                self.keyed[(record.section, record.tokens[0].casefold())].append(record)
        self.now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()

    # -- naming ----------------------------------------------------------------
    def table(self, cls: str) -> str:
        return f"{self.d.module_of(cls)}.{snake(cls)}"

    def class_iri(self, cls: str) -> str:
        return self.sv.get_uri(self.sv.get_class(cls), expand=True)

    def add(self, cls: str, row: dict) -> None:
        if cls not in ("Agent", "IngestionActivity"):
            row.setdefault("ingestion_activity", self.import_iri)
        if row.get("geometry") and self.crs:
            row["geometry_crs"] = self.crs
        row.setdefault("class_iri", self.class_iri(cls))
        self.rows[self.table(cls)].append({k: v for k, v in row.items() if v is not None})

    def model_iri(self, kind: str, eid: str) -> str:
        eid = self.identifiers.get((kind, eid.casefold()), eid)
        return f"wsys:model/{self.scope}/{self.rev_id}/{kind}/{quote(eid, safe='')}"

    def asset_iri(self, eid: str, kind: str = "node") -> str:
        eid = self.identifiers.get((kind, eid.casefold()), eid)
        return f"wsys:asset/{self.scope}/{kind}/{quote(eid, safe='')}"

    def record_iri(self, line):
        return f"{self.rev_iri}/line/{line}"

    def source_for(self, kind, eid):
        return self.sources.get((kind, eid.casefold()))

    def add_sources(self):
        self.add("IngestionActivity", {"iri": self.import_iri, "was_associated_with": self.loader_iri,
                 "started_at": self.now, "used": [self.rev_iri], "mapping_release": self.mapping})
        for role, name, digest in self.files:
            self.add("InputArtifact", {"iri": f"{self.rev_iri}/file/{quote(name, safe='')}",
                     "of_revision": self.rev_iri, "source_path": name, "file_role": role,
                     "mapping_release": self.mapping, "input_sha256": digest})
        for record in self.records:
            fields = record.named_fields(self.profile)
            objects = record.objects(self.profile)
            if record.section == "RAINGAGES" and fields.get("source", "").upper() == "TIMESERIES" and "source_id" in fields:
                objects.append(("table", fields["source_id"]))
            self.add("InputRecord", {"iri": self.record_iri(record.line), "of_revision": self.rev_iri,
                     "section": record.section, "line_number": record.line, "raw_text": record.raw,
                     "tokens": record.tokens, "native_fields": json.dumps(fields) if fields else None,
                     "source_subjects": [self.model_iri(kind, eid) for kind, eid in objects],
                     "mapping_release": self.mapping, "disposition": "field_binding" if fields else
                     ("object_binding" if objects else "source_only")})

    def edge(self, a: str, rel: str, b: str, **props) -> None:
        source = self.asset_source.get(a)
        if rel == "CONNECTS_TO" and "/link/" in b:
            source = self.asset_source.get(b)
        if a == self.sys_iri:
            source = f"{self.rev_iri}/file/{quote(self.inp.name, safe='')}"
        self.rows["topology.edge"].append({"from_iri": a, "to_iri": b, "relation": rel, "source_record": source, "valid_from": self.now, "source_system": "swmm:inp", "ingestion_activity": self.import_iri,
                                           **({"props": json.dumps(props)} if props else {})})

    # -- value conversion --------------------------------------------------------
    def convert(self, value, rng: str):
        e = self.engine
        if value is None:
            return None
        enum_cls = getattr(e, rng, None) if rng in self.sv.all_enums() else None
        if enum_cls is not None:
            try:
                return enum_cls(int(value)).name if not hasattr(value, "name") else value.name
            except Exception:
                return None
        if hasattr(value, "name") and hasattr(value, "value") and not isinstance(value, (str, bytes)):
            return value.name
        kind = {"Node": "node", "Link": "link", "Subcatchment": "subcatchment", "Gage": "gage"}.get(type(value).__name__)
        if kind:
            return self.model_iri(kind, value.id)
        if isinstance(value, (dt.datetime, dt.date)):
            return value.isoformat()
        if isinstance(value, dt.timedelta):
            return f"PT{int(value.total_seconds())}S"
        if hasattr(value, "tolist"):
            value = value.tolist()
        if isinstance(value, dict):
            return {str(k): self.convert(v, "") for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.convert(v, "") for v in value]
        if isinstance(value, float):
            return None if (math.isnan(value) or math.isinf(value)) else value
        if isinstance(value, (bool, int, str)):
            return value
        raise TypeError(f"Unmapped native value type {type(value).__name__}")

    def read_path(self, obj, path: str, kind: str):
        parts = path.split(".")[1:] if path.split(".")[0] == kind else path.split(".")
        for p in parts:
            obj = getattr(obj, p)
        return obj

    def read_class(self, obj, cls: str, kind: str) -> dict:
        out = {}
        for s in self.sv.class_induced_slots(cls):
            ann = annotations(s)
            if s.inlined and s.range in self.sv.all_classes() and not ann.get("catalog_path"):
                sub = {}
                for ss in self.sv.class_induced_slots(s.range):
                    p = annotations(ss).get("catalog_path")
                    if p:
                        try:
                            v = self.convert(self.read_path(obj, p, kind), ss.range)
                            if v is not None:
                                sub[ss.name] = v
                        except Exception:
                            self.unread[p] += 1
                if sub:
                    out[s.name] = sub
                continue
            path = ann.get("catalog_path")
            if not path:
                continue
            try:
                v = self.convert(self.read_path(obj, path, kind), s.range)
                if isinstance(v, (list, dict)) and not s.multivalued:
                    v = json.dumps(v)          # records and other opaque values are stored as JSON text
                elif isinstance(v, list) and any(isinstance(x, (list, dict)) for x in v):
                    self.unread[path + " (nested value; the catalog type maps to a flat list)"] += 1
                    continue
                if v is not None:
                    out[s.name] = v
            except Exception:
                self.unread[path] += 1
        return out

    # -- units -------------------------------------------------------------------
    def setup_units(self, s) -> None:
        us = str(s.unit_system).upper().endswith("US")
        fu = s.flow_units
        fname = fu.name if hasattr(fu, "name") else self.engine.FlowUnits(int(fu)).name
        self.flow_name = fname
        self.length_name = "ft" if us else "m"
        self.len_m = 0.3048 if us else 1.0
        self.area_ha = 0.40468564224 if us else 1.0
        self.depth_mm = 25.4 if us else 1.0
        self.flow_m3s = FLOW_TO_M3S[fname]
        self.notes.append(f"unit system {'US' if us else 'SI'}, flow units {fname}; topology and observations converted to SI (m, ha, mm, m3/s); model.* rows keep model units")

    def L(self, v):
        return None if v is None else round(v * self.len_m, 6)

    # -- load ---------------------------------------------------------------------
    def load_static(self) -> None:
        e = self.engine
        s = e.Solver(str(self.inp), None, None)
        s.open()
        try:
            self.setup_units(s)
            try:
                self.crs = s.spatial.crs or None
            except (AttributeError, getattr(e, "CRSError", RuntimeError)):
                self.notes.append("Native coordinates have no declared CRS")
            self.add("Agent", {"iri": self.loader_iri, "label": "loaders.swmm_to_rows", "agent_kind": "SOFTWARE", "external_identity": "water-ontology/loaders/swmm_to_rows.py"})
            self.add_sources()
            self.add("Revision", {"iri": self.rev_iri, "label": self.inp.name, "revision_id": self.rev_id, "input_sha256": self.sha, "bundle_sha256": self.bundle_digest, "mapping_release": self.mapping,
                                  "source_system": "local-file (no platform revision manifest)", "dc_created": self.now,
                                  "dc_rights": self.input_rights, "dc_license": self.input_license, "was_attributed_to": self.loader_iri,
                                  "dc_conforms_to": [f"openswmm-catalog-r{json.loads((HERE / 'schema/model.lock.json').read_text())['catalog_revision']}"]})
            self.add("WaterSystem", {"iri": self.sys_iri, "label": self.system_id, "current_revision": self.rev_iri, "valid_from": self.now, "source_system": "swmm:inp"})
            self.edge(self.sys_iri, "REPRESENTED_BY", self.rev_iri)
            common = {"lifecycle_status": "UNKNOWN", "valid_from": self.now, "source_system": "swmm:inp"}
            for kind, collection, cls in (("pollutant", s.pollutants, "SwmmPollutant"),
                                          ("landuse", s.quality.landuses, "SwmmLanduse"),
                                          ("pattern", s.patterns, "SwmmPattern"), ("table", s.tables, "SwmmTable")):
                for i in range(len(collection)):
                    obj = collection[i]
                    eid = obj.id
                    record = self.source_for(kind, eid)
                    mid = self.model_iri(kind, eid)
                    self.add(cls, {"iri": mid, "element_id": eid, "label": eid, "revision": self.rev_iri,
                                   "source_record": record, **self.read_class(obj, cls, kind)})
                    if kind == "pollutant":
                        constituent = self.asset_iri(eid, "constituent")
                        self.add("Constituent", {"iri": constituent, "label": eid, "source_record": record})
                        for quantity in ("CONCENTRATION", "MASS_LOADING"):
                            self.add("ObservedProperty", {"iri": f"{constituent}/{quantity.lower()}",
                                     "label": f"{eid} {quantity.lower().replace('_', ' ')}",
                                     "property_kind": quantity, "pollutant": constituent, "source_record": record})
            xs_ok = set(self.sv.get_enum("XSectShape", strict=False).permissible_values) if "XSectShape" in self.sv.all_enums() else set()

            for i in range(len(s.gages)):
                g = s.gages[i]
                m = self.model_iri("gage", g.id)
                self.add("SwmmGage", {"iri": m, "label": g.id, "element_id": g.id, "source_record": self.source_for("gage", g.id), "revision": self.rev_iri, **self.read_class(g, "SwmmGage", "gage")})
                a = self.asset_iri(g.id, "gage")
                self.asset_source[a] = self.source_for("gage", g.id)
                rt = self.convert(g.rain_type, "GageRainType")
                self.add("RainGage", {"iri": a, "label": g.id, "alt_ids": [f"swmm:{g.id}"], "source_record": self.source_for("gage", g.id), **common,
                                      "rain_format": rt if rt in ("INTENSITY", "VOLUME", "CUMULATIVE") else None,
                                      "recording_interval": f"PT{int(g.rain_interval)}S", "geometry": self.point(lambda: s.spatial.gage_coord(i))})
                self.edge(a, "REPRESENTED_BY", m); self.edge(a, "PART_OF_SYSTEM", self.sys_iri)

            for i in range(len(s.nodes)):
                n = s.nodes[i]
                t = self.convert(n.type, "NodeType")
                mcls, tcls = NODE_CLASS[t]
                m = self.model_iri("node", n.id)
                self.add(mcls, {"iri": m, "label": n.id, "element_id": n.id, "source_record": self.source_for("node", n.id), "revision": self.rev_iri, **self.read_class(n, mcls, "node")})
                a = self.asset_iri(n.id)
                self.asset_source[a] = self.source_for("node", n.id)
                row = {"iri": a, "label": n.id, "alt_ids": [f"swmm:{n.id}"], "source_record": self.source_for("node", n.id), **common, "system": self.sys_iri,
                       "invert_elevation": self.L(n.invert_elev), "max_depth": self.L(n.max_depth), "initial_depth": self.L(n.initial_depth),
                       "surcharge_depth": self.L(n.surcharge_depth), "geometry": self.point(lambda: s.spatial.node_coord(i))}
                if n.invert_elev is not None and n.max_depth:
                    row["rim_elevation"] = self.L(n.invert_elev + n.max_depth)
                if tcls == "StorageUnit":
                    row["storage_shape"] = self.convert(n.storage.shape,"StorageShape")
                if tcls == "Outfall":
                    row["is_subnetwork_controller"] = True
                    try:
                        row["outfall_type"] = self.convert(n.outfall.type, "OutfallType")
                        row["tide_gate"] = n.outfall.flap_gate
                    except Exception:
                        pass
                self.add(tcls, row)
                self.edge(a, "REPRESENTED_BY", m); self.edge(a, "PART_OF_SYSTEM", self.sys_iri)

            for i in range(len(s.links)):
                l = s.links[i]
                t = self.convert(l.type, "LinkType")
                mcls, tcls = LINK_CLASS[t]
                m = self.model_iri("link", l.id)
                self.add(mcls, {"iri": m, "label": l.id, "element_id": l.id, "source_record": self.source_for("link", l.id), "revision": self.rev_iri, **self.read_class(l, mcls, "link")})
                a = self.asset_iri(l.id, "link")
                self.asset_source[a] = self.source_for("link", l.id)
                row = {"iri": a, "label": l.id, "alt_ids": [f"swmm:{l.id}"], "source_record": self.source_for("link", l.id), **common, "system": self.sys_iri,
                       "inlet_offset": self.L(l.offset_up), "outlet_offset": self.L(l.offset_dn), "geometry": self.line(s, i, l)}
                if tcls == "Pump":
                    row.update(startup_depth=self.L(l.pump.startup_depth), shutoff_depth=self.L(l.pump.shutoff_depth))
                elif tcls == "Weir":
                    row.update(weir_type=self.convert(l.weir.type,"WeirType"), crest_height=self.L(l.weir.crest_height),
                               discharge_coefficient=l.weir.discharge_coeff)
                elif tcls == "Orifice":
                    row["orifice_type"] = self.convert(l.orifice.type,"OrificeType")
                if tcls == "Conduit":
                    x = l.xsect
                    shape = normalize_code("XSectShape", self.convert(x.shape, "XSectShape"))
                    if shape not in xs_ok:
                        self.notes.append(f"link {l.id}: cross-section shape {shape} is not in the topology XSectShape enum; left null"); shape = None
                    row.update({"length": self.L(l.length), "roughness": l.roughness, "xsect_shape": shape,
                                **geometry_values(shape, (x.g1, x.g2, x.g3, x.g4), self.len_m)})
                    if shape in ("CIRCULAR", "FORCE_MAIN", "FILLED_CIRCULAR") and x.g1:
                        row["diameter"] = round(x.g1 * self.len_m * 1000, 1)
                self.add(tcls, row)
                self.edge(a, "REPRESENTED_BY", m); self.edge(a, "PART_OF_SYSTEM", self.sys_iri)
                self.edge(self.asset_iri(l.from_node.id), "CONNECTS_TO", a)
                self.edge(a, "CONNECTS_TO", self.asset_iri(l.to_node.id))

            for i in range(len(s.subcatchments)):
                c = s.subcatchments[i]
                m = self.model_iri("subcatchment", c.id)
                fields = self.read_class(c, "SwmmSubcatchment", "subcatchment")
                for section, slot in (("COVERAGES", "coverage"), ("LOADINGS", "loadings")):
                    selected = self.keyed.get((section, c.id.casefold()), [])
                    if selected:
                        fields[slot] = [f"{r.tokens[1]}={r.tokens[2]}" for r in selected]
                self.add("SwmmSubcatchment", {"iri": m, "label": c.id, "element_id": c.id, "source_record": self.source_for("subcatchment", c.id), "revision": self.rev_iri, **fields})
                a = self.asset_iri(c.id, "subcatchment")
                self.asset_source[a] = self.source_for("subcatchment", c.id)
                infil = None
                try:
                    infil = normalize_code("InfiltrationModel", self.convert(c.infiltration.model, "InfilModel"))
                except Exception:
                    pass
                self.add("Subcatchment", {"iri": a, "label": c.id, "alt_ids": [f"swmm:{c.id}"], "source_record": self.source_for("subcatchment", c.id), "valid_from": self.now, "source_system": "swmm:inp",
                                          "lifecycle_status": "UNKNOWN", "system": self.sys_iri,
                                          "area": round(c.area * self.area_ha, 6), "width": self.L(c.width), "slope": c.slope,
                                          "impervious_fraction": round(c.imperv_pct / 100.0, 6), "n_impervious": c.n_imperv, "n_pervious": c.n_perv,
                                          "depression_storage_impervious": round(c.ds_imperv * self.depth_mm, 4), "depression_storage_pervious": round(c.ds_perv * self.depth_mm, 4),
                                          "infiltration_model": infil if infil in self.sv.get_enum("InfiltrationModel").permissible_values else None,
                                          "geometry": self.polygon(lambda: s.spatial.subcatchment_polygon(i))})
                self.edge(a, "REPRESENTED_BY", m); self.edge(a, "PART_OF_SYSTEM", self.sys_iri)
                out = c.outlet
                if out is not None:
                    kind = type(out).__name__
                    self.edge(a, "DRAINS_TO", self.asset_iri(out.id, "subcatchment" if kind == "Subcatchment" else "node"))
                if c.gage is not None:
                    self.edge(a, "USES_GAGE", self.asset_iri(c.gage.id, "gage"))
        finally:
            s.close()

    # -- geometry -------------------------------------------------------------------
    @staticmethod
    def point(f):
        try:
            x, y = f()
            return None if (x is None or math.isnan(x)) else f"POINT ({x} {y})"
        except Exception:
            return None

    def line(self, s, i, l):
        try:
            a, b = s.spatial.node_coord(l.from_node.index), s.spatial.node_coord(l.to_node.index)
            mid = s.spatial.link_vertices(i)
            pts = [a] + [tuple(p) for p in (mid.tolist() if hasattr(mid, "tolist") else (mid or []))] + [b]
            return "LINESTRING (" + ", ".join(f"{x} {y}" for x, y in pts) + ")"
        except Exception:
            return None

    @staticmethod
    def polygon(f):
        try:
            p = f()
            pts = p.tolist() if hasattr(p, "tolist") else list(p)
            if len(pts) < 3:
                return None
            if pts[0] != pts[-1]:
                pts.append(pts[0])
            return "POLYGON ((" + ", ".join(f"{x} {y}" for x, y in pts) + "))"
        except Exception:
            return None

    # -- local run -------------------------------------------------------------------
    def run_local(self, work: Path) -> None:
        e = self.engine
        run_id = uuid4().hex
        work = work / run_id
        work.mkdir(parents=True, exist_ok=False)
        s = e.Solver(str(self.inp), str(work / "model.rpt"), str(work / "model.out"))
        started = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
        s.run()
        ended = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
        try:
            run_iri, att_iri = f"wsys:run/{run_id}", f"wsys:attempt/{run_id}/1"
            src = "local-engine (not hydrocouple.platform; no manifests)"
            ver = __import__("importlib.metadata").metadata.version("openswmm")
            engine_iri = f"wsys:agent/software/openswmm/{quote(ver,safe='')}"
            if all(agent["iri"] != engine_iri for agent in self.rows[self.table("Agent")]):
                self.add("Agent", {"iri":engine_iri,"label":f"OpenSWMM {ver}","agent_kind":"SOFTWARE",
                                   "external_identity":f"openswmm=={ver}"})
            self.add("Run", {"iri": run_iri, "label": run_id, "run_id": run_id, "of_revision": self.rev_iri, "workflow": "swmm.simulation@local",
                             "was_generated_by": self.import_iri, "was_attributed_to": self.loader_iri,
                             "generated_at": started, "source_system": src})
            self.add("Attempt", {"iri": att_iri, "label": f"{run_id}/1", "attempt_id": f"{run_id}-1", "of_run": run_iri,
                                 "was_associated_with": engine_iri, "used": [self.rev_iri], "started_at": started, "ended_at": ended,
                                 "generated_at": ended, "source_system": src, "dc_conforms_to": [f"openswmm=={ver}"]})
            known = {p["iri"] for p in self.rows[self.table("ObservedProperty")]}
            for name,unit in (("depth","unit:M"),("flow","unit:M3-PER-SEC")):
                pid = f"wsys:prop/{name}"
                if pid not in known:
                    self.add("ObservedProperty", {"iri":pid,"label":name,"property_kind":name.upper(),"default_unit":unit})
            start_time = s.start_datetime.isoformat()
            end_time = s.end_datetime.isoformat() if hasattr(s.end_datetime, "isoformat") else str(s.end_datetime)

            def obs(asset, prop, value, unit, agg, source, native_unit, factor):
                if value is None or (isinstance(value, float) and math.isnan(value)):
                    return
                self.add("Observation", {"iri": f"wsys:obs/{run_id}/1/{asset.split('/')[-1]}/{prop}-{agg.split('/')[0]}", "entity_iri": asset, "property_iri": f"wsys:prop/{prop}",
                                         "interval_start": start_time, "interval_end": end_time, "result_time": ended,
                                         "source_record": source, "native_unit": native_unit, "conversion_factor": factor, "value": round(float(value), 6), "unit": unit, "quality_flag": "GOOD",
                                         "result_kind": "SIMULATION", "aggregation": agg, "was_generated_by": att_iri, "generated_at": ended, "source_system": src})
            for i in range(len(s.nodes)):
                n = s.nodes[i]
                obs(self.asset_iri(n.id), "depth", n.stats.max_depth * self.len_m, "unit:M", "max/run", self.source_for("node", n.id), self.length_name, self.len_m)
            for i in range(len(s.links)):
                l = s.links[i]
                obs(self.asset_iri(l.id, "link"), "flow", l.stats.max_flow * self.flow_m3s, "unit:M3-PER-SEC", "max/run", self.source_for("link", l.id), self.flow_name, self.flow_m3s)
            output = work / "model.out"
            if output.is_file():
                output_digest = file_digest(output)
                self.rows[self.table("Attempt")][-1]["distributions"] = [
                    {"access_url": f"artifacts/{output_digest}.out", "integrity_checksum": {"algorithm": "http://spdx.org/rdf/terms#checksumAlgorithm_sha256", "digest": output_digest},
                     "media_type": "application/octet-stream", "byte_size": output.stat().st_size}]
                self.add("ResultArtifact", {"iri": f"{att_iri}/output", "was_generated_by": att_iri,
                         "was_derived_from": [self.rev_iri], "generated_at": ended, "artifact_sha256": output_digest,
                         "distributions": self.rows[self.table("Attempt")][-1]["distributions"]})
                self.output_artifacts[f"{output_digest}.out"] = output
                self.output_series(output, s, att_iri, ended)
            mb = s.mass_balance
            self.notes.append(f"local run: runoff continuity error {mb.runoff_continuity_error:.4g}%, routing continuity error {mb.routing_continuity_error:.4g}%")
        finally:
            try:
                s.close()
            except Exception:
                pass

    def output_series(self, output, solver, attempt, generated):
        """Report-period outfall pollutographs plus any selected node depth/link flow series."""
        e = self.engine
        outfalls = {n.id for n in solver.nodes if self.convert(n.type, "NodeType") == "OUTFALL"}
        with e.OutputReader(str(output)) as reader:
            times = reader.period_times
            units = getattr(reader, "pollutant_units", None)
            if units is None:
                declared_units = {r.tokens[0].casefold(): r.tokens[1] for r in self.records
                                  if r.section == "POLLUTANTS" and len(r.tokens) > 1}
                units = [declared_units.get(name.casefold()) for name in reader.pollutant_ids]
            for eid in sorted(outfalls.intersection(reader.node_ids)):
                for i, pollutant in enumerate(reader.pollutant_ids):
                    unit = units[i]
                    if not unit:
                        self.notes.append(f"Output omitted {eid}/{pollutant}: concentration unit unavailable")
                        continue
                    values = reader.node_series(eid, int(e.OutNodeVar.POLLUT_BASE) + i)
                    prop = self.asset_iri(pollutant, "constituent") + "/concentration"
                    canonical = {"mg/l": "unit:MilliGM-PER-L", "ug/l": "unit:MicroGM-PER-L"}.get(unit.lower(), unit)
                    self.add_series(attempt, "node", eid, prop, values, times, unit, canonical, 1, generated)
            for kind, eid in self.series:
                if kind == "node":
                    values = reader.node_series(eid, e.OutNodeVar.DEPTH)
                    spec = "wsys:prop/depth", self.length_name, "unit:M", self.len_m
                elif kind == "link":
                    values = reader.link_series(eid, e.OutLinkVar.FLOW)
                    spec = "wsys:prop/flow", self.flow_name, "unit:M3-PER-SEC", self.flow_m3s
                else:
                    raise ValueError(f"Unsupported result series kind {kind}")
                prop, native, unit, factor = spec
                self.add_series(attempt, kind, eid, prop, values, times, native, unit, factor, generated)

    def add_series(self, attempt, kind, eid, prop, values, times, native, unit, factor, generated):
        asset = self.asset_iri(eid, kind)
        for index, (instant, value) in enumerate(zip(times, values, strict=True)):
            if not math.isfinite(value):
                continue
            self.add("Observation", {"iri": f"{attempt}/{kind}/{quote(eid,safe='')}/{quote(prop,safe='')}/{index}",
                     "entity_iri": asset, "property_iri": prop, "value": float(value) * factor,
                     "phenomenon_time": str(instant), "result_time": generated, "unit": unit,
                     "native_unit": native, "conversion_factor": factor, "quality_flag": "GOOD",
                     "result_kind": "SIMULATION", "was_generated_by": attempt,
                     "source_record": self.source_for(kind,eid), "generated_at": generated})

    def write(self, out: Path) -> None:
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=out.parent) as td:
            stage = Path(td)
            digests = {}
            for table, rows in sorted(self.rows.items()):
                target = stage / f"{table}.jsonl"
                with target.open("w", encoding="utf-8") as stream:
                    for row in rows:
                        stream.write(json.dumps(row, sort_keys=True, allow_nan=False) + "\n")
                digests[target.name] = file_digest(target)
            artifacts = dict(self.output_artifacts)
            for role, name, digest in self.files:
                source = self.inp if role == "primary" else self.inp.parent / name
                artifacts[digest + ".input"] = source
            (stage / "artifacts").mkdir()
            for filename, source in artifacts.items():
                dest = stage / "artifacts" / filename
                shutil.copyfile(source, dest)
                if file_digest(dest) != filename.split(".")[0]:
                    raise ValueError(f"Input or result changed during export: {source.name}")
            inputs = self.rows[self.table("InputRecord")]
            counts = Counter(record.section for record in self.records)
            exported = Counter(r["section"] for r in inputs)
            if exported != counts or len({r["line_number"] for r in inputs}) != len(self.records):
                raise ValueError("Input record reconciliation failed")
            coverage = {section: {"source_records": count, "exported": exported[section],
                                  "binding": "configured" if section in self.profile["sections"] else "source_only"}
                        for section, count in sorted(counts.items())}
            summary = {"inp": self.inp.name, "inp_sha256": self.sha, "revision": self.rev_iri,
                       "system": self.sys_iri, "bundle_sha256": self.bundle_digest,
                       "mapping_release": self.mapping, "coverage": coverage, "row_sha256": digests,
                       "artifacts": sorted(artifacts),
                       "engine": __import__("importlib.metadata").metadata.version("openswmm"),
                       "rows": {t: len(r) for t, r in sorted(self.rows.items())},
                       "catalog_properties_not_readable_with_this_engine": dict(sorted(self.unread.items())),
                       "notes": self.notes}
            (stage / "SUMMARY.json").write_text(json.dumps(summary, indent=2) + "\n")
            # Readers verify the content hashes. Publish the matching manifest last.
            with FileLock(str(out.parent / ("." + out.name + ".export.lock"))):
                out.mkdir(exist_ok=True)
                (out / "artifacts").mkdir(exist_ok=True)
                for item in (stage / "artifacts").iterdir():
                    os.replace(item, out / "artifacts" / item.name)
                prev = out / "SUMMARY.json"
                old = set(json.loads(prev.read_text())["rows"]) if prev.exists() else set()
                for filename in digests:
                    os.replace(stage / filename, out / filename)
                for table in old - set(summary["rows"]):
                    if Path(table).name != table:
                        raise ValueError("Invalid prior export table name")
                    (out / (table + ".jsonl")).unlink(missing_ok=True)
                os.replace(stage / "SUMMARY.json", prev)
        print(json.dumps({"tables":len(self.rows),"rows":sum(summary["rows"].values()),
                          "unreadable_properties":len(self.unread)}))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inp")
    ap.add_argument("--utility", default="demo")
    ap.add_argument("--system", default="system")
    ap.add_argument("--run", action="store_true", help="also run the model locally and record simulated statistics")
    ap.add_argument("-o", "--out", default="rows")
    ap.add_argument("--work", default=".tmp/run")
    ap.add_argument("--input-rights", help="Supplied access or sharing terms for the model revision")
    ap.add_argument("--input-license", help="Supplied SPDX identifier or license URL for the model revision")
    ap.add_argument("--profile", type=Path, help="Additive versioned INP section profile")
    ap.add_argument("--series", nargs=2, metavar=("KIND","ID"), action="append", default=[],
                    help="Include a node depth or link flow hydrograph in a local run")
    a = ap.parse_args()
    if a.series and not a.run:
        ap.error("--series needs --run")
    ld = Loader(Path(a.inp), a.utility, a.system, a.profile)
    ld.input_rights, ld.input_license = a.input_rights, a.input_license
    ld.series = list(dict.fromkeys(map(tuple, a.series)))
    ld.load_static()
    if a.run:
        ld.run_local(Path(a.work))
    ld.write(Path(a.out))


if __name__ == "__main__":
    main()
