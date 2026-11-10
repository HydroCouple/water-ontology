# analysis

Model revisions, runs, attempts, studies and agent findings. Each curated class embeds the matching hydrocouple.platform contract (generated in analysis_platform.yaml) as its manifest, adds wsys identity and PROV links, and is what other modules reference. Nothing here invents a shape the platform does not publish.

```mermaid
classDiagram
  class AgentFinding {
    string trace_id*
    FindingKind finding_kind*
    string question
    string statement*
    IRI subjects
    IRI evidence
    string tools_used
    string assumptions
    boolean verifier_passed*
    ReviewState review_state
    Agent reviewed_by
  }
  class Attempt {
    string attempt_id*
    Run of_run
    PlatformAttemptManifest attempt_manifest
    string manifest_sha256
  }
  class Candidate {
    Study of_study
    integer candidate_index
    string parameters
    string objective_values
    boolean is_pareto
    Run of_run
  }
  class Revision {
    string revision_id*
    Composition of_composition
    PlatformRevisionManifest revision_manifest
    string manifest_sha256
  }
  class Run {
    string run_id*
    Revision of_revision
    Study of_study
    string workflow
    string tool_call_id
    string session_id
    PlatformRunRequest run_request
    PlatformJobSpec job_spec
  }
  class Study {
    string study_id*
    Revision of_revision
    string workflow
    string session_id
    PlatformStudyView study_view
  }
  DerivedEntity <|-- AgentFinding
  AgentFinding --> "1" Agent : reviewed_by
  DerivedEntity <|-- Attempt
  Attempt --> "1" Run : of_run
  Attempt *-- "1" PlatformAttemptManifest : attempt_manifest
  DerivedEntity <|-- Candidate
  Candidate --> "1" Study : of_study
  Candidate --> "1" Run : of_run
  Entity <|-- Revision
  Revision --> "1" Composition : of_composition
  Revision *-- "1" PlatformRevisionManifest : revision_manifest
  DerivedEntity <|-- Run
  Run --> "1" Revision : of_revision
  Run --> "1" Study : of_study
  Run *-- "1" PlatformRunRequest : run_request
  Run *-- "1" PlatformJobSpec : job_spec
  DerivedEntity <|-- Study
  Study --> "1" Revision : of_revision
  Study *-- "1" PlatformStudyView : study_view
```

## Classes

| Class | Parent | Description |
| --- | --- | --- |
| `AgentFinding` | DerivedEntity | What an agent surfaced to a person, with the trace, tools, evidence and assumptions behind it and the verifier's verdict. Recommendations are proposals only. |
| `Attempt` | DerivedEntity | One execution attempt of a run with its artifacts, provenance and quality (platform attempt manifest). Simulation results ingested as Observations of result_kind SIMULATION point here through was_generated_by. |
| `Candidate` | DerivedEntity | One evaluated design or policy in a study, with its decision variables and objective values. |
| `Revision` | Entity | An immutable packaged composition (files, components, findings) recorded by a hydrocouple.platform revision manifest. Every SwmmElement row belongs to exactly one Revision. |
| `Run` | DerivedEntity | A request to execute a workflow on a revision on a target (platform run request plus the job spec the control plane built from it). |
| `Study` | DerivedEntity | An ask/tell optimisation over an evaluator pool (platform study view): objectives, budget, state and the Pareto set. |
