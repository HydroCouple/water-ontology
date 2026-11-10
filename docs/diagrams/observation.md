# observation

Sensors, observed properties, datastreams and the observations, forecasts and setpoints they carry. Aligned with W3C SOSA/SSN and OGC SensorThings. Observations are one tall table keyed by (entity, property, time) with the unit as an IRI; a Sensor is ATTACHED_TO the asset it measures (Edge), and a Datastream binds one sensor to one property of one feature of interest.

```mermaid
classDiagram
  class Datastream {
    Sensor sensor*
    ObservedProperty observed_property*
    IRI feature_of_interest*
    string sampling_interval
    string staleness_threshold
    string datasource_tag
  }
  class Observation {
    Datastream datastream
    IRI entity_iri*
    IRI property_iri*
    datetime phenomenon_time*
    datetime result_time
    float value*
    IRI unit*
    QualityFlag quality_flag*
    ResultKind result_kind*
    datetime issued_at
    string horizon
    float lower
    float upper
    float interval_level
    ... 1 more
  }
  class ObservedProperty {
    PropertyKind property_kind*
    IRI default_unit
    IRI pollutant
    string staleness_threshold
  }
  class Sensor {
    SensorKind sensor_kind
    string serial_number
    string datasource_tag
    string sampling_interval
  }
  class Setpoint {
    IRI target_entity*
    IRI target_property*
    float value*
    IRI unit*
    datetime effective_from*
    datetime effective_to
    Agent set_by
    Agent approved_by
  }
  Entity <|-- Datastream
  Datastream --> "1" Sensor : sensor
  Datastream --> "1" ObservedProperty : observed_property
  DerivedEntity <|-- Observation
  Observation --> "1" Datastream : datastream
  Entity <|-- ObservedProperty
  Asset <|-- Sensor
  DerivedEntity <|-- Setpoint
  Setpoint --> "1" Agent : set_by
  Setpoint --> "1" Agent : approved_by
```

## Classes

| Class | Parent | Description |
| --- | --- | --- |
| `Datastream` | Entity | One sensor observing one property of one feature of interest over time; the unit of telemetry ingestion and the thing a staleness threshold applies to. |
| `Observation` | DerivedEntity | One value of one property for one entity at one time. Measurements, forecasts, simulation results and aggregates share this shape, distinguished by result_kind, so they can be compared in one query. was_generated_by is the ingestion job, forecast model version or simulation attempt. |
| `ObservedProperty` | Entity | A measurable property such as flow, depth or TSS concentration, with its physical kind and default unit. Pollutants from a SWMM model map here. |
| `Sensor` | Asset | A physical or virtual device that observes a property of an asset or area. Mounted on its feature of interest by an ATTACHED_TO edge. |
| `Setpoint` | DerivedEntity | A target value for a controllable property (pump speed, gate opening, tank level band) effective over a period. Agents only propose setpoints; set_by and approved_by record who made them real. |
