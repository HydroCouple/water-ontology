# core

Base classes, identifiers, lifecycle and provenance slots shared by every module. Physical assets, observations, events and analysis artifacts all specialise Entity. The LinkML source here is the single source of truth; OWL, SHACL, Delta DDL and Unity Catalog metadata are generated from it.

```mermaid
classDiagram
  class Asset {
    <<abstract>>
    LifecycleStatus lifecycle_status*
    date install_date
    string manufacturer
    string model_number
    integer criticality
  }
  class DerivedEntity {
    <<abstract>>
    IRI was_derived_from
    IRI was_generated_by*
    datetime generated_at
    Confidence confidence
  }
  class Entity {
    <<abstract>>
    IRI iri*
    IRI class_iri*
    string label
    string description
    string alt_ids
    Utility utility
    string source_system
    datetime valid_from
    datetime valid_to
  }
  class SpatialEntity {
    <<abstract>>
    WKT geometry
    string h3_cell
  }
  class Utility {
  }
  SpatialEntity <|-- Asset
  Entity <|-- DerivedEntity
  Entity --> "1" Utility : utility
  Entity <|-- SpatialEntity
  Entity <|-- Utility
```

## Classes

| Class | Parent | Description |
| --- | --- | --- |
| `Asset` | SpatialEntity | A physical component of a water system that is owned, maintained and can fail. Kept shallow on purpose; variants are attributes, not subclasses. |
| `DerivedEntity` | Entity | Anything produced by a computation, pipeline or agent. Carries PROV links so a reviewer can reproduce it. |
| `Entity` |  | Anything with a stable IRI. Root of the hierarchy. |
| `SpatialEntity` | Entity | An entity with a location or footprint. |
| `Utility` | Entity | A water utility or operating organisation. |
