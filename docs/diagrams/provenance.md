# provenance

Completes the PROV-O picture (who or what produced each derived thing) and adds Dublin Core descriptive metadata and DCAT alignment for dataset-like classes. PROV-O carries lineage, Dublin Core carries description, DCAT carries discoverability; nothing here duplicates what Unity Catalog or MLflow already record, it names it in a standard way.

```mermaid
classDiagram
  class Activity {
    Agent was_associated_with
    IRI used
    datetime started_at
    datetime ended_at
  }
  class Agent {
    AgentKind agent_kind*
    string external_identity
    Agent acts_on_behalf_of
  }
  class Attributed {
    Agent was_attributed_to
    string dc_title
    datetime dc_created
    datetime dc_modified
    Agent dc_creator
    string dc_license
    string dc_conforms_to
    string dc_rights
  }
  class Dataset {
    Distribution distributions
    Agent dc_publisher
    IRI dc_source
  }
  class Distribution {
    string access_url
    string media_type
    integer byte_size
    string checksum
    integer delta_version
    string dc_license
  }
  Activity --> "1" Agent : was_associated_with
  Entity <|-- Agent
  Attributed --> "1" Agent : was_attributed_to
  Attributed --> "1" Agent : dc_creator
  Dataset *-- "*" Distribution : distributions
  Dataset --> "1" Agent : dc_publisher
```

## Classes

| Class | Parent | Description |
| --- | --- | --- |
| `Activity` |  | Mixin for activities (runs, attempts, state changes, pipeline executions). |
| `Agent` | Entity | Someone or something responsible for an entity or activity (PROV-O Agent). People, organisations, service principals, AI agents, ML model versions and other software all appear here so attribution is uniform. |
| `Attributed` |  | Mixin for anything PROV-attributed and Dublin-Core-described. |
| `Dataset` |  | Mixin for dataset-like entities that should be discoverable - training data, revisions, datastreams, the repo's own releases. |
| `Distribution` |  | One concrete form of a dataset-like entity - a Delta table at a version, a file in a Volume, a model artifact. |
