# operations

Work the utility does on its assets: work orders and inspections. Minimal on purpose; it exists so events can be resolved by real work (RESOLVED_BY) and inspection results can be evidence for events. Extended when CMMS data is onboarded.

```mermaid
classDiagram
  class Inspection {
    IRI target_asset*
    InspectionMethod inspection_method*
    datetime inspected_at*
    ConditionGrade structural_grade
    ConditionGrade operational_grade
    WorkOrder under_work_order
    string report_uri
  }
  class WorkOrder {
    WorkOrderType work_type*
    WorkOrderStatus work_status*
    IRI target_asset*
    string cmms_id
    string work_summary
    integer priority
    datetime requested_at
    datetime scheduled_for
    datetime completed_at
    Agent assigned_to
    Agent proposed_by
    Agent approved_by_agent
  }
  Entity <|-- Inspection
  Inspection --> "1" WorkOrder : under_work_order
  Entity <|-- WorkOrder
  WorkOrder --> "1" Agent : assigned_to
  WorkOrder --> "1" Agent : proposed_by
  WorkOrder --> "1" Agent : approved_by_agent
```

## Classes

| Class | Parent | Description |
| --- | --- | --- |
| `Inspection` | Entity | A condition inspection of an asset with its grades; may be EVIDENCE_FOR an event such as a blockage. |
| `WorkOrder` | Entity | A unit of work on an asset. Events are RESOLVED_BY completed work orders. AI agents may propose work orders (proposed_by) but a person approves them. |
