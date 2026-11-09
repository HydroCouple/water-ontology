# Changelog

## 0.1.0 (unreleased)
- LinkML source of truth: `core`, `topology`, `analysis` (stub), `ml`, generated `model`; release root `wsys.yaml`.
- Generators: OpenSWMM catalog → `model.yaml` (with lock and staleness check); LinkML → Delta DDL, Unity Catalog comments/tags, enum reference tables, class hierarchy table, SCD2 views, asset union view, STRUCT component views.
- Topology refactored to SWMM/OpenSWMM element semantics; distribution classes moved to a stub module; REPRESENTED_BY links assets to model elements.
- ML module: MLModel, MLModelVersion (scikit-learn, PyTorch, Lightning first-class), FeatureBinding, TrainingDataset, Prediction, DriftCheck.
- Hand-curated mappings: SWMM model classes → wsys topology; Esri Utility Network → wsys.
