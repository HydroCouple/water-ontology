# Changelog

## 0.1.0 (unreleased)
- LinkML source of truth: `core`, `topology`, `analysis` (stub), `ml`, generated `model`; release root `wsys.yaml`.
- Generators: OpenSWMM catalog → `model.yaml` (with lock and staleness check); LinkML → Delta DDL, Unity Catalog comments/tags, enum reference tables, class hierarchy table, SCD2 views, asset union view, STRUCT component views.
- Hand-curated mappings: SWMM model classes → wsys topology; Esri Utility Network → wsys.
