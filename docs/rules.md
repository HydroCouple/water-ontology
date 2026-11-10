# Ontology assertions

| Rule | Severity | Meaning |
| --- | --- | --- |
| `edge_endpoint_exists` | ERROR | Both ends of every edge are known entities. |
| `edge_domain_connects_to` | ERROR | CONNECTS_TO only connects: Link -> Node; Node -> Link. |
| `edge_domain_drains_to` | ERROR | DRAINS_TO only connects: Subcatchment -> Node; Subcatchment -> Subcatchment; LIDControl -> Node. |
| `edge_domain_captures_to` | ERROR | CAPTURES_TO only connects: Inlet -> Node. |
| `edge_domain_uses_gage` | ERROR | USES_GAGE only connects: Subcatchment -> RainGage. |
| `edge_domain_contained_in` | ERROR | CONTAINED_IN only connects: Asset -> Structure; LIDControl -> Subcatchment; Sensor -> Structure. |
| `edge_domain_attached_to` | ERROR | ATTACHED_TO only connects: Sensor -> Asset; Inlet -> Street. |
| `edge_domain_belongs_to_zone` | ERROR | BELONGS_TO_ZONE only connects: Asset -> Zone; Subcatchment -> Zone; Zone -> Zone. |
| `edge_domain_feeds` | ERROR | FEEDS only connects: Node -> Zone; WaterSystem -> WaterSystem. |
| `edge_domain_isolated_by` | ERROR | ISOLATED_BY only connects: Conduit -> Orifice; Zone -> Orifice; Conduit -> Pump. |
| `edge_domain_part_of_system` | ERROR | PART_OF_SYSTEM only connects: Asset -> WaterSystem; Subcatchment -> WaterSystem; Zone -> WaterSystem; Aquifer -> WaterSystem; Mesh2D -> WaterSystem. |
| `edge_domain_represented_by` | ERROR | REPRESENTED_BY only connects: Asset -> SwmmElement; Subcatchment -> SwmmSubcatchment; RainGage -> SwmmGage; WaterSystem -> Revision. |
| `edge_domain_coupled_to` | ERROR | COUPLED_TO only connects: ModelComponent -> ModelComponent; WaterSystem -> Mesh2D; WaterSystem -> Aquifer. |
| `link_two_endpoints` | ERROR | Every in-service Link has exactly one upstream node (Node CONNECTS_TO Link) and one downstream node (Link CONNECTS_TO Node). |
| `subcatchment_drains_somewhere` | ERROR | Every Subcatchment has at least one DRAINS_TO edge. |
| `subcatchment_has_gage` | WARNING | Every Subcatchment has at least one USES_GAGE edge. |
| `sensor_is_attached` | ERROR | Every Sensor has at least one ATTACHED_TO edge. |
| `asset_in_a_system` | WARNING | Every Asset has at least one PART_OF_SYSTEM edge. |
| `represented_by_unique` | ERROR | An asset is REPRESENTED_BY at most one model element per revision. |
| `event_resolved_has_resolution` | ERROR | A RESOLVED event has a RESOLVED_BY link. |
| `event_confirmed_by_someone` | ERROR | CONFIRMED, IN_PROGRESS and RESOLVED events record who confirmed them. |
| `event_never_confirmed_by_ai` | ERROR | An AI agent may detect an event but never confirm it. |
| `event_has_location_or_impact` | WARNING | Every event is LOCATED_AT or AFFECTS something. |
| `event_times_ordered` | ERROR | occurred_at <= detected_at <= ended_at where present. |
| `event_link_domain_affects` | ERROR | AFFECTS links an event to: Asset, Cell2D, Zone. |
| `event_link_domain_located_at` | ERROR | LOCATED_AT links an event to: Asset, Subcatchment. |
| `event_link_domain_evidence_for` | ERROR | EVIDENCE_FOR links an event to: Event, Observation, Prediction. |
| `event_link_domain_caused_by` | ERROR | CAUSED_BY links an event to: Event. |
| `event_link_domain_part_of` | ERROR | PART_OF links an event to: Event. |
| `event_link_domain_resolved_by` | ERROR | RESOLVED_BY links an event to: Setpoint. |
| `event_link_domain_triggered` | ERROR | TRIGGERED links an event to: Event, Setpoint. |
| `event_link_domain_explained_by` | ERROR | EXPLAINED_BY links an event to: AgentFinding, Attempt. |
| `prediction_target_matches_model_class` | ERROR | A prediction's target entity is an instance of its model's applies_to_class. |
| `only_one_champion` | ERROR | A model has at most one CHAMPION version. |
| `observation_unit_matches_property` | WARNING | An observation's unit is its property's default unit (after normalisation). |
