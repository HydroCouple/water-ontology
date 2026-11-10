# Site drainage example

`model.inp` is `site_drainage_example.inp` from the OpenSWMM engine's test data (HydroCouple/openswmm.engine, Apache-2.0): 7 subcatchments, 11 junctions, 11 conduits, 1 outfall, 1 rain gage.

`rows/` is what `make example` produced from it with the PyPI engine (openswmm 6.0.0a4.dev1): one JSON Lines file per Delta table, 191 rows in 18 tables. `rows/SUMMARY.json` lists row counts, the catalog properties this engine build could not supply, and the unit conversions applied.

- `model.*` rows keep the model's own units (US, CFS). `topology.*` rows and observations are converted to SI.
- The revision is identified by the SHA-256 of the `.inp`. There is no hydrocouple.platform revision manifest behind it.
- The run is a local engine run, recorded with `source_system = local-engine`. It stands in for a platform attempt until one is ingested; it has no attempt manifest.

`make check-example` loads the rows into DuckDB and runs every generated assertion. `tests/test_example.py` also breaks copies of the rows on purpose and checks that the intended rule fires.
