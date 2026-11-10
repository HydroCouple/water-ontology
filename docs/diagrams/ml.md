# ml

Machine-learning models as governed, explainable assets. A model's inputs and target are FeatureBindings to ontology classes and properties, so an agent can say in domain terms what a model consumes and predicts, the verifier can check that a model applies to the entity it is being asked about, and every Prediction carries provenance to the exact model version, training data and inputs. The registry of record is MLflow with Unity Catalog Models; this module is the semantic layer over it, not a replacement.

```mermaid
classDiagram
  class DriftCheck {
    MLModelVersion of_version*
    datetime checked_at*
    string drift_metric
    float drift_value
    float threshold
    DriftStatus drift_status*
  }
  class EvaluationMetric {
    string metric_name*
    float metric_value*
    float baseline_value
    DataSplit split
  }
  class FeatureBinding {
    string feature_name*
    IRI source_class*
    IRI source_property*
    string relation_path
    FeatureAggregation feature_aggregation
    string window
    string lag
    string transform
    IRI feature_unit
    string feature_table
  }
  class MLModel {
    MLTask task*
    string uc_model_name
    IRI applies_to_class
    IRI applies_to_scope
    FeatureBinding target*
    string horizon
    string resolution
    Agent owner
  }
  class MLModelVersion {
    MLModel of_model*
    integer version_number*
    ModelStage stage*
    MLFramework framework*
    string framework_version
    MLFramework framework_origin
    string mlflow_flavor
    string mlflow_run_id
    string mlflow_experiment
    string model_uri
    string uc_alias
    string serving_endpoint
    FeatureBinding inputs*
    TrainingDataset trained_on*
    ... 6 more
  }
  class Prediction {
    MLModelVersion of_version*
    IRI target_entity*
    IRI target_property*
    datetime valid_time*
    datetime issued_at
    float value*
    IRI value_unit*
    float lower
    float upper
    float interval_level
    float probability
    string input_snapshot
    boolean out_of_scope
  }
  class TrainingDataset {
    string source_tables*
    datetime time_start
    datetime time_end
    integer entity_count
    integer row_count
    string split_strategy
    string feature_table
  }
  DerivedEntity <|-- DriftCheck
  DriftCheck --> "1" MLModelVersion : of_version
  Entity <|-- MLModel
  MLModel *-- "1" FeatureBinding : target
  MLModel --> "1" Agent : owner
  DerivedEntity <|-- MLModelVersion
  MLModelVersion --> "1" MLModel : of_model
  MLModelVersion *-- "*" FeatureBinding : inputs
  MLModelVersion --> "1" TrainingDataset : trained_on
  MLModelVersion *-- "*" EvaluationMetric : metrics
  MLModelVersion --> "1" Revision : emulates
  DerivedEntity <|-- Prediction
  Prediction --> "1" MLModelVersion : of_version
  DerivedEntity <|-- TrainingDataset
```

## Classes

| Class | Parent | Description |
| --- | --- | --- |
| `DriftCheck` | DerivedEntity | A scheduled comparison of live inputs or predictions against the training distribution or recent observations (Lakehouse Monitoring); DRIFTED versions are demoted from CHAMPION by the registry workflow. |
| `EvaluationMetric` |  | One metric on one split, with the baseline value it must beat. |
| `FeatureBinding` |  | Binds one model input or target to the ontology - which class, which property, how aggregated, over what window, reached through which relations. The MLflow signature names the column; this says what the column means. |
| `MLModel` | Entity | A named modelling problem with a stable identity across versions, e.g. "6-hour CSO overflow forecast for the Elm Street basin". Says what is predicted, for which class of entity and where. |
| `MLModelVersion` | DerivedEntity | One trained artifact of an MLModel, registered in Unity Catalog through MLflow. Carries framework, signature as FeatureBindings, training data, metrics against a baseline, and lifecycle stage. Agents call only CHAMPION versions through registered tools. |
| `Prediction` | DerivedEntity | One predicted value for one entity, property and valid time from one model version, with interval or probability, the inputs used and an out-of-scope flag. was_generated_by points at the serving call or batch job. |
| `TrainingDataset` | DerivedEntity | The exact data a version was trained and evaluated on - tables at Delta versions, time window, entities, split strategy - so training is reproducible and leakage is auditable. |
