# Site drainage example

`model.inp` is `site_drainage_example.inp` from the OpenSWMM engine's test data (HydroCouple/openswmm.engine, Apache-2.0): 7 subcatchments, 11 junctions, 11 conduits, 1 outfall, 1 rain gage.

The committed OpenSWMM 6.0.0a4.dev2 run contains 962 rows in 26 tables. The export preserves every populated source section, including TSS, land use, coverage, loading and rain series. It includes 360 TSS concentrations at O1 and 23 hydraulic maxima. Source and result artifacts are content-addressed under `rows/artifacts/`. The row manifest records source and output checksums, units, and native fields the engine could not supply.

Build with `make sqlite inventory`. For run reproduction, lineage queries and release registration, see the [SWMM and SQLite guide](../../docs/swmm-sqlite.md).
