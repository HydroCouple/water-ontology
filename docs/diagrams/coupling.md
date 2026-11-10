# coupling

Model components, their exchange items and the compositions that couple them, mirroring the HydroCouple SDK interfaces (IModelComponent, IComponentInfo, IArgument, IExchangeItem, IInput, IOutput, IAdaptedOutput, IValueDefinition, IDimension). A Composition is the unit hydrocouple.platform packages into a Revision and runs; a ComponentConnection is a typed link from a provider Output to a consumer Input, optionally through an AdaptedOutput, and is also asserted as a COUPLED_TO Edge for traversal.

```mermaid
classDiagram
  class AdaptedOutput {
    Output adapts_output*
    string adapter_factory
    ComponentArgument arguments
  }
  class ComponentArgument {
    string argument_name*
    string argument_value
    string argument_type
    boolean is_file
  }
  class ComponentConnection {
    Composition composition*
    Output provider*
    Input consumer*
    AdaptedOutput via_adapter
  }
  class Composition {
    ModelComponent components
    ModelComponent workflow_component
    string composition_file
  }
  class ExchangeItem {
    <<abstract>>
    ModelComponent component*
    string item_id*
    ValueKind value_kind*
    IRI quantity
    IRI item_unit
    DataItemGeometry data_geometry*
    string dimensions
    datetime time_span_start
    datetime time_span_end
    IRI wsys_feature
  }
  class Input {
    boolean multi_input
  }
  class ModelComponent {
    ComponentKind component_kind*
    string info_id
    string info_version
    string vendor
    string library_path
    ComponentArgument arguments
    ComponentStatus component_status
    Composition composition*
    IRI wsys_feature
  }
  class Output {
  }
  Output <|-- AdaptedOutput
  AdaptedOutput --> "1" Output : adapts_output
  AdaptedOutput *-- "*" ComponentArgument : arguments
  ComponentConnection --> "1" Composition : composition
  ComponentConnection --> "1" Output : provider
  ComponentConnection --> "1" Input : consumer
  ComponentConnection --> "1" AdaptedOutput : via_adapter
  Entity <|-- Composition
  Composition --> "*" ModelComponent : components
  Composition --> "1" ModelComponent : workflow_component
  Entity <|-- ExchangeItem
  ExchangeItem --> "1" ModelComponent : component
  ExchangeItem <|-- Input
  Entity <|-- ModelComponent
  ModelComponent *-- "*" ComponentArgument : arguments
  ModelComponent --> "1" Composition : composition
  ExchangeItem <|-- Output
```

## Classes

| Class | Parent | Description |
| --- | --- | --- |
| `AdaptedOutput` | Output | An output transformed in time, space or unit from a provider output by an adapter factory. |
| `ComponentArgument` |  | One IArgument of a component as configured (name, value or file path, type). |
| `ComponentConnection` |  | A typed link from a provider Output to a consumer Input, optionally through an AdaptedOutput. Also written as a COUPLED_TO Edge between the components for traversal. |
| `Composition` | Entity | The set of components and connections run together; what hydrocouple.platform packages into a Revision. A single SWMM .inp is a one-component composition. |
| `ExchangeItem` | Entity | An input or output of a component with its value definition (quantity and unit), dimensions and spatial structure. |
| `Input` | ExchangeItem | A consumer of values. |
| `ModelComponent` | Entity | A configured instance of a HydroCouple model component inside a composition - a SWMM model, a 2D mesh, a data reader, an adapter factory or a workflow. |
| `Output` | ExchangeItem | A provider of values. |
