# Relations

Allowed (from → to) pairs per relation, from `schema/mappings/edge_domains.yaml`. These become SQL assertions on `topology.edge` and `event.event_link`.

## EdgeRelation

```mermaid
flowchart LR
  Link[Link] -- CONNECTS_TO --> Node[Node]
  Node[Node] -- CONNECTS_TO --> Link[Link]
  Subcatchment[Subcatchment] -- DRAINS_TO --> Node[Node]
  Subcatchment[Subcatchment] -- DRAINS_TO --> Subcatchment[Subcatchment]
  LIDControl[LIDControl] -- DRAINS_TO --> Node[Node]
  Inlet[Inlet] -- CAPTURES_TO --> Node[Node]
  Subcatchment[Subcatchment] -- USES_GAGE --> RainGage[RainGage]
  Asset[Asset] -- CONTAINED_IN --> Structure[Structure]
  LIDControl[LIDControl] -- CONTAINED_IN --> Subcatchment[Subcatchment]
  Sensor[Sensor] -- CONTAINED_IN --> Structure[Structure]
  Sensor[Sensor] -- ATTACHED_TO --> Asset[Asset]
  Inlet[Inlet] -- ATTACHED_TO --> Street[Street]
  Asset[Asset] -- BELONGS_TO_ZONE --> Zone[Zone]
  Subcatchment[Subcatchment] -- BELONGS_TO_ZONE --> Zone[Zone]
  Zone[Zone] -- BELONGS_TO_ZONE --> Zone[Zone]
  Node[Node] -- FEEDS --> Zone[Zone]
  WaterSystem[WaterSystem] -- FEEDS --> WaterSystem[WaterSystem]
  Conduit[Conduit] -- ISOLATED_BY --> Orifice[Orifice]
  Zone[Zone] -- ISOLATED_BY --> Orifice[Orifice]
  Conduit[Conduit] -- ISOLATED_BY --> Pump[Pump]
  Asset[Asset] -- PART_OF_SYSTEM --> WaterSystem[WaterSystem]
  Subcatchment[Subcatchment] -- PART_OF_SYSTEM --> WaterSystem[WaterSystem]
  Zone[Zone] -- PART_OF_SYSTEM --> WaterSystem[WaterSystem]
  Aquifer[Aquifer] -- PART_OF_SYSTEM --> WaterSystem[WaterSystem]
  Mesh2D[Mesh2D] -- PART_OF_SYSTEM --> WaterSystem[WaterSystem]
  Asset[Asset] -- REPRESENTED_BY --> SwmmElement[SwmmElement]
  Subcatchment[Subcatchment] -- REPRESENTED_BY --> SwmmSubcatchment[SwmmSubcatchment]
  RainGage[RainGage] -- REPRESENTED_BY --> SwmmGage[SwmmGage]
  WaterSystem[WaterSystem] -- REPRESENTED_BY --> Revision[Revision]
  ModelComponent[ModelComponent] -- COUPLED_TO --> ModelComponent[ModelComponent]
  WaterSystem[WaterSystem] -- COUPLED_TO --> Mesh2D[Mesh2D]
  WaterSystem[WaterSystem] -- COUPLED_TO --> Aquifer[Aquifer]
```

| Relation | From | To | Meaning |
| --- | --- | --- | --- |
| `CONNECTS_TO` | Link | Node | Hydraulic connectivity from the upstream end to the downstream end as modelled |
| `CONNECTS_TO` | Node | Link | Hydraulic connectivity from the upstream end to the downstream end as modelled |
| `DRAINS_TO` | Subcatchment | Node | Runoff routing from a Subcatchment to a Node or another Subcatchment |
| `DRAINS_TO` | Subcatchment | Subcatchment | Runoff routing from a Subcatchment to a Node or another Subcatchment |
| `DRAINS_TO` | LIDControl | Node | Runoff routing from a Subcatchment to a Node or another Subcatchment |
| `CAPTURES_TO` | Inlet | Node | An Inlet captures street flow to a Node |
| `USES_GAGE` | Subcatchment | RainGage | A Subcatchment receives rainfall from a RainGage |
| `CONTAINED_IN` | Asset | Structure | Structural containment such as a flow meter inside a chamber |
| `CONTAINED_IN` | LIDControl | Subcatchment | Structural containment such as a flow meter inside a chamber |
| `CONTAINED_IN` | Sensor | Structure | Structural containment such as a flow meter inside a chamber |
| `ATTACHED_TO` | Sensor | Asset | A device mounted on another asset such as a level sensor on a manhole |
| `ATTACHED_TO` | Inlet | Street | A device mounted on another asset such as a level sensor on a manhole |
| `BELONGS_TO_ZONE` | Asset | Zone | Membership of a derived sewershed or drainage area or CSO basin |
| `BELONGS_TO_ZONE` | Subcatchment | Zone | Membership of a derived sewershed or drainage area or CSO basin |
| `BELONGS_TO_ZONE` | Zone | Zone | Membership of a derived sewershed or drainage area or CSO basin |
| `FEEDS` | Node | Zone | A node or storage that supplies a zone or downstream system |
| `FEEDS` | WaterSystem | WaterSystem | A node or storage that supplies a zone or downstream system |
| `ISOLATED_BY` | Conduit | Orifice | A conduit or zone whose isolation depends on this gate or valve |
| `ISOLATED_BY` | Zone | Orifice | A conduit or zone whose isolation depends on this gate or valve |
| `ISOLATED_BY` | Conduit | Pump | A conduit or zone whose isolation depends on this gate or valve |
| `PART_OF_SYSTEM` | Asset | WaterSystem | Membership of a WaterSystem |
| `PART_OF_SYSTEM` | Subcatchment | WaterSystem | Membership of a WaterSystem |
| `PART_OF_SYSTEM` | Zone | WaterSystem | Membership of a WaterSystem |
| `PART_OF_SYSTEM` | Aquifer | WaterSystem | Membership of a WaterSystem |
| `PART_OF_SYSTEM` | Mesh2D | WaterSystem | Membership of a WaterSystem |
| `REPRESENTED_BY` | Asset | SwmmElement | The SwmmElement in a given Revision that models this asset |
| `REPRESENTED_BY` | Subcatchment | SwmmSubcatchment | The SwmmElement in a given Revision that models this asset |
| `REPRESENTED_BY` | RainGage | SwmmGage | The SwmmElement in a given Revision that models this asset |
| `REPRESENTED_BY` | WaterSystem | Revision | The SwmmElement in a given Revision that models this asset |
| `COUPLED_TO` | ModelComponent | ModelComponent | A HydroCouple exchange between two model components or domains |
| `COUPLED_TO` | WaterSystem | Mesh2D | A HydroCouple exchange between two model components or domains |
| `COUPLED_TO` | WaterSystem | Aquifer | A HydroCouple exchange between two model components or domains |

## EventLinkRelation

```mermaid
flowchart LR
  Event[Event] -- AFFECTS --> Asset[Asset]
  Event[Event] -- AFFECTS --> Zone[Zone]
  Event[Event] -- AFFECTS --> Cell2D[Cell2D]
  Event[Event] -- LOCATED_AT --> Asset[Asset]
  Event[Event] -- LOCATED_AT --> Subcatchment[Subcatchment]
  Observation[Observation] -- EVIDENCE_FOR --> Event[Event]
  Inspection[Inspection] -- EVIDENCE_FOR --> Event[Event]
  Prediction[Prediction] -- EVIDENCE_FOR --> Event[Event]
  Event[Event] -- EVIDENCE_FOR --> Event[Event]
  Event[Event] -- CAUSED_BY --> Event[Event]
  Event[Event] -- PART_OF --> Event[Event]
  Event[Event] -- RESOLVED_BY --> WorkOrder[WorkOrder]
  Event[Event] -- RESOLVED_BY --> Setpoint[Setpoint]
  Event[Event] -- TRIGGERED --> Setpoint[Setpoint]
  Event[Event] -- TRIGGERED --> Event[Event]
  Event[Event] -- EXPLAINED_BY --> Attempt[Attempt]
  Event[Event] -- EXPLAINED_BY --> AgentFinding[AgentFinding]
```

| Relation | From | To | Meaning |
| --- | --- | --- | --- |
| `AFFECTS` | Event | Asset | Asset or zone or cell or customer set impacted |
| `AFFECTS` | Event | Zone | Asset or zone or cell or customer set impacted |
| `AFFECTS` | Event | Cell2D | Asset or zone or cell or customer set impacted |
| `LOCATED_AT` | Event | Asset | The asset or location where it occurred |
| `LOCATED_AT` | Event | Subcatchment | The asset or location where it occurred |
| `EVIDENCE_FOR` | Observation | Event | An observation, anomaly or report supporting this event (link from evidence to event) |
| `EVIDENCE_FOR` | Inspection | Event | An observation, anomaly or report supporting this event (link from evidence to event) |
| `EVIDENCE_FOR` | Prediction | Event | An observation, anomaly or report supporting this event (link from evidence to event) |
| `EVIDENCE_FOR` | Event | Event | An observation, anomaly or report supporting this event (link from evidence to event) |
| `CAUSED_BY` | Event | Event | Another event or condition that caused this one |
| `PART_OF` | Event | Event | Child event of a larger one (e.g. flooding within a storm) |
| `RESOLVED_BY` | Event | WorkOrder | Work order or control action that resolved it |
| `RESOLVED_BY` | Event | Setpoint | Work order or control action that resolved it |
| `TRIGGERED` | Event | Setpoint | A control action or alarm this event triggered |
| `TRIGGERED` | Event | Event | A control action or alarm this event triggered |
| `EXPLAINED_BY` | Event | Attempt | A model attempt or finding that explains it |
| `EXPLAINED_BY` | Event | AgentFinding | A model attempt or finding that explains it |
