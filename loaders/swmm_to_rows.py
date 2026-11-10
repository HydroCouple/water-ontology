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
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path

from linkml_runtime.utils.schemaview import SchemaView

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
    def __init__(self, inp: Path, utility: str, system: str):
        from openswmm import engine  # imported here so the rest of the repo works without the engine
        self.engine = engine
        self.inp = inp
        self.utility, self.system_id = utility, system
        self.d = DeltaGenerator(str(HERE / "schema" / "wsys.yaml"), "water_core")
        self.sv: SchemaView = self.d.sv
        self.rows: dict[str, list[dict]] = defaultdict(list)
        self.unread: dict[str, int] = defaultdict(int)
        self.notes: list[str] = []
        self.sha = hashlib.sha256(inp.read_bytes()).hexdigest()
        self.rev_id = self.sha[:12]
        self.rev_iri = f"wsys:revision/{self.rev_id}"
        self.sys_iri = f"wsys:system/{utility}/{system}"
        self.loader_iri = "wsys:agent/software/swmm-to-rows"
        self.now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()

    # -- naming ----------------------------------------------------------------
    def table(self, cls: str) -> str:
        return f"{self.d.module_of(cls)}.{snake(cls)}"

    def class_iri(self, cls: str) -> str:
        return self.sv.get_uri(self.sv.get_class(cls), expand=True)

    def add(self, cls: str, row: dict) -> None:
        row.setdefault("class_iri", self.class_iri(cls))
        self.rows[self.table(cls)].append({k: v for k, v in row.items() if v is not None})

    def model_iri(self, kind: str, eid: str) -> str:
        return f"wsys:model/{self.rev_id}/{kind}/{eid}"

    def asset_iri(self, eid: str, kind: str = "asset") -> str:
        return f"wsys:{kind}/{self.utility}/{eid}"

    def edge(self, a: str, rel: str, b: str, **props) -> None:
        self.rows["topology.edge"].append({"from_iri": a, "to_iri": b, "relation": rel, "valid_from": self.now, "source_system": "swmm:inp",
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
        if isinstance(value, (list, tuple)):
            return [self.convert(v, "") for v in value]
        if isinstance(value, float):
            return None if (math.isnan(value) or math.isinf(value)) else value
        if isinstance(value, (bool, int, str)):
            return value
        return None

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
            self.add("Agent", {"iri": self.loader_iri, "label": "loaders.swmm_to_rows", "agent_kind": "SOFTWARE", "external_identity": "water-ontology/loaders/swmm_to_rows.py"})
            self.add("Revision", {"iri": self.rev_iri, "label": self.inp.name, "revision_id": self.rev_id, "manifest_sha256": self.sha,
                                  "source_system": "local-file (no platform revision manifest)", "dc_created": self.now, "was_attributed_to": self.loader_iri,
                                  "dc_conforms_to": [f"openswmm-catalog-r{json.loads((HERE / 'schema/model.lock.json').read_text())['catalog_revision']}"]})
            self.add("WaterSystem", {"iri": self.sys_iri, "label": self.system_id, "current_revision": self.rev_iri, "valid_from": self.now, "source_system": "swmm:inp"})
            self.edge(self.sys_iri, "REPRESENTED_BY", self.rev_iri)
            common = {"lifecycle_status": "IN_SERVICE", "valid_from": self.now, "source_system": "swmm:inp"}
            xs_ok = set(self.sv.get_enum("XSectShape", strict=False).permissible_values) if "XSectShape" in self.sv.all_enums() else set()

            for i in range(len(s.gages)):
                g = s.gages[i]
                m = self.model_iri("gage", g.id)
                self.add("SwmmGage", {"iri": m, "label": g.id, "element_id": g.id, "revision": self.rev_iri, **self.read_class(g, "SwmmGage", "gage")})
                a = self.asset_iri(g.id)
                rt = self.convert(g.rain_type, "GageRainType")
                self.add("RainGage", {"iri": a, "label": g.id, "alt_ids": [f"swmm:{g.id}"], **common,
                                      "rain_format": rt if rt in ("INTENSITY", "VOLUME", "CUMULATIVE") else None,
                                      "recording_interval": f"PT{int(g.rain_interval)}S", "geometry": self.point(lambda: s.spatial.gage_coord(i))})
                self.edge(a, "REPRESENTED_BY", m); self.edge(a, "PART_OF_SYSTEM", self.sys_iri)

            for i in range(len(s.nodes)):
                n = s.nodes[i]
                t = self.convert(n.type, "NodeType")
                mcls, tcls = NODE_CLASS[t]
                m = self.model_iri("node", n.id)
                self.add(mcls, {"iri": m, "label": n.id, "element_id": n.id, "revision": self.rev_iri, **self.read_class(n, mcls, "node")})
                a = self.asset_iri(n.id)
                row = {"iri": a, "label": n.id, "alt_ids": [f"swmm:{n.id}"], **common, "system": self.sys_iri,
                       "invert_elevation": self.L(n.invert_elev), "max_depth": self.L(n.max_depth), "initial_depth": self.L(n.initial_depth),
                       "surcharge_depth": self.L(n.surcharge_depth), "geometry": self.point(lambda: s.spatial.node_coord(i))}
                if n.invert_elev is not None and n.max_depth:
                    row["rim_elevation"] = self.L(n.invert_elev + n.max_depth)
                if tcls == "Outfall":
                    row["is_subnetwork_controller"] = True
                    try:
                        row["outfall_type"] = self.convert(n.outfall.type, "OutfallType")
                    except Exception:
                        pass
                self.add(tcls, row)
                self.edge(a, "REPRESENTED_BY", m); self.edge(a, "PART_OF_SYSTEM", self.sys_iri)

            for i in range(len(s.links)):
                l = s.links[i]
                t = self.convert(l.type, "LinkType")
                mcls, tcls = LINK_CLASS[t]
                m = self.model_iri("link", l.id)
                self.add(mcls, {"iri": m, "label": l.id, "element_id": l.id, "revision": self.rev_iri, **self.read_class(l, mcls, "link")})
                a = self.asset_iri(l.id)
                row = {"iri": a, "label": l.id, "alt_ids": [f"swmm:{l.id}"], **common, "system": self.sys_iri,
                       "inlet_offset": self.L(l.offset_up), "outlet_offset": self.L(l.offset_dn), "geometry": self.line(s, i, l)}
                if tcls == "Conduit":
                    x = l.xsect
                    shape = self.convert(x.shape, "XSectShape")
                    if shape not in xs_ok:
                        self.notes.append(f"link {l.id}: cross-section shape {shape} is not in the topology XSectShape enum; left null"); shape = None
                    row.update({"length": self.L(l.length), "roughness": l.roughness, "xsect_shape": shape,
                                "geom1": self.L(x.g1), "geom2": self.L(x.g2), "geom3": x.g3, "geom4": x.g4})
                    if shape in ("CIRCULAR", "FORCE_MAIN", "FILLED_CIRCULAR") and x.g1:
                        row["diameter"] = round(x.g1 * self.len_m * 1000, 1)
                self.add(tcls, row)
                self.edge(a, "REPRESENTED_BY", m); self.edge(a, "PART_OF_SYSTEM", self.sys_iri)
                self.edge(self.asset_iri(l.from_node.id), "CONNECTS_TO", a)
                self.edge(a, "CONNECTS_TO", self.asset_iri(l.to_node.id))

            for i in range(len(s.subcatchments)):
                c = s.subcatchments[i]
                m = self.model_iri("subcatchment", c.id)
                self.add("SwmmSubcatchment", {"iri": m, "label": c.id, "element_id": c.id, "revision": self.rev_iri, **self.read_class(c, "SwmmSubcatchment", "subcatchment")})
                a = self.asset_iri(c.id, "subcatchment")
                infil = None
                try:
                    infil = self.convert(c.infiltration.model, "InfilModel")
                except Exception:
                    pass
                self.add("Subcatchment", {"iri": a, "label": c.id, "alt_ids": [f"swmm:{c.id}"], "valid_from": self.now, "source_system": "swmm:inp",
                                          "lifecycle_status": "IN_SERVICE", "system": self.sys_iri,
                                          "area": round(c.area * self.area_ha, 6), "width": self.L(c.width), "slope": c.slope,
                                          "impervious_fraction": round(c.imperv_pct / 100.0, 6), "n_impervious": c.n_imperv, "n_pervious": c.n_perv,
                                          "depression_storage_impervious": round(c.ds_imperv * self.depth_mm, 4), "depression_storage_pervious": round(c.ds_perv * self.depth_mm, 4),
                                          "infiltration_model": infil if infil in self.sv.get_enum("InfiltrationModel").permissible_values else None,
                                          "geometry": self.polygon(lambda: s.spatial.subcatchment_polygon(i))})
                self.edge(a, "REPRESENTED_BY", m); self.edge(a, "PART_OF_SYSTEM", self.sys_iri)
                out = c.outlet
                if out is not None:
                    kind = type(out).__name__
                    self.edge(a, "DRAINS_TO", self.asset_iri(out.id, "subcatchment" if kind == "Subcatchment" else "asset"))
                if c.gage is not None:
                    self.edge(a, "USES_GAGE", self.asset_iri(c.gage.id))
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
        work.mkdir(parents=True, exist_ok=True)
        s = e.Solver(str(self.inp), str(work / "model.rpt"), str(work / "model.out"))
        started = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
        s.run()
        ended = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
        try:
            run_id = f"local-{self.rev_id}"
            run_iri, att_iri = f"wsys:run/{run_id}", f"wsys:attempt/{run_id}/1"
            src = "local-engine (not hydrocouple.platform; no manifests)"
            ver = __import__("importlib.metadata").metadata.version("openswmm")
            self.add("Run", {"iri": run_iri, "label": run_id, "run_id": run_id, "of_revision": self.rev_iri, "workflow": "swmm.simulation@local",
                             "was_generated_by": self.loader_iri, "was_associated_with": self.loader_iri, "used": [self.rev_iri],
                             "started_at": started, "ended_at": ended, "generated_at": ended, "source_system": src})
            self.add("Attempt", {"iri": att_iri, "label": f"{run_id}/1", "attempt_id": f"{run_id}-1", "of_run": run_iri, "was_generated_by": run_iri,
                                 "was_associated_with": self.loader_iri, "used": [self.rev_iri], "started_at": started, "ended_at": ended,
                                 "generated_at": ended, "source_system": src, "dc_conforms_to": [f"openswmm=={ver}"]})
            self.add("ObservedProperty", {"iri": "wsys:prop/depth", "label": "depth", "property_kind": "DEPTH", "default_unit": "unit:M"})
            self.add("ObservedProperty", {"iri": "wsys:prop/flow", "label": "flow", "property_kind": "FLOW", "default_unit": "unit:M3-PER-SEC"})
            end_time = s.end_datetime.isoformat() if hasattr(s.end_datetime, "isoformat") else str(s.end_datetime)

            def obs(asset, prop, value, unit, agg):
                if value is None or (isinstance(value, float) and math.isnan(value)):
                    return
                self.add("Observation", {"iri": f"wsys:obs/{run_id}/1/{asset.split('/')[-1]}/{prop}-{agg.split('/')[0]}", "entity_iri": asset, "property_iri": f"wsys:prop/{prop}",
                                         "phenomenon_time": end_time, "result_time": ended, "value": round(float(value), 6), "unit": unit, "quality_flag": "GOOD",
                                         "result_kind": "SIMULATION", "aggregation": agg, "was_generated_by": att_iri, "generated_at": ended, "source_system": src})
            for i in range(len(s.nodes)):
                n = s.nodes[i]
                obs(self.asset_iri(n.id), "depth", n.stats.max_depth * self.len_m, "unit:M", "max/run")
            for i in range(len(s.links)):
                l = s.links[i]
                obs(self.asset_iri(l.id), "flow", l.stats.max_flow * self.flow_m3s, "unit:M3-PER-SEC", "max/run")
            mb = s.mass_balance
            self.notes.append(f"local run: runoff continuity error {mb.runoff_continuity_error:.4g}%, routing continuity error {mb.routing_continuity_error:.4g}%")
        finally:
            try:
                s.close()
            except Exception:
                pass

    def write(self, out: Path) -> None:
        out.mkdir(parents=True, exist_ok=True)
        for old in out.glob("*.jsonl"):
            old.write_text("")
        for table, rows in sorted(self.rows.items()):
            (out / f"{table}.jsonl").write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
        for old in out.glob("*.jsonl"):
            if old.stat().st_size == 0:
                old.unlink()
        summary = {"inp": self.inp.name, "inp_sha256": self.sha, "revision": self.rev_iri, "system": self.sys_iri,
                   "engine": __import__("importlib.metadata").metadata.version("openswmm"),
                   "rows": {t: len(r) for t, r in sorted(self.rows.items())},
                   "catalog_properties_not_readable_with_this_engine": dict(sorted(self.unread.items())), "notes": self.notes}
        (out / "SUMMARY.json").write_text(json.dumps(summary, indent=2) + "\n")
        print(json.dumps({"tables": len(self.rows), "rows": sum(len(r) for r in self.rows.values()), "unreadable_properties": len(self.unread)}))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inp")
    ap.add_argument("--utility", default="demo")
    ap.add_argument("--system", default="system")
    ap.add_argument("--run", action="store_true", help="also run the model locally and record simulated statistics")
    ap.add_argument("-o", "--out", default="rows")
    ap.add_argument("--work", default=".tmp/run")
    a = ap.parse_args()
    ld = Loader(Path(a.inp), a.utility, a.system)
    ld.load_static()
    if a.run:
        ld.run_local(Path(a.work))
    ld.write(Path(a.out))


if __name__ == "__main__":
    main()
