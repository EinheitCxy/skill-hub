# Research State Snapshot Contract (internal: ResearchState)

## Purpose and owner

The Research State Snapshot (`ResearchState`) is the small, revisioned
control-plane snapshot for one research project. The Coordinator is its sole
workflow owner. Workers may read
the task-local context derived from it, but must not edit global state.

The machine source is `.research/state.json`. `researchctl status`,
`researchctl transition`, and `researchctl checkpoint` are internal
Coordinator transport; agents should use controller functions rather than edit
the JSON file.

The local controller files are the authority for facts, permissions, budget,
and recovery. Codex is the first Coordinator implementation, not the state
store. A long conversation, hidden model context, or remembered intention
cannot replace a revisioned machine record.

## Exact document

Schema version 1 requires exactly:

- `schema_version`: integer `1`;
- `revision`: non-negative compare-and-swap version;
- `research_id`: stable project identity;
- `current_stage`: an ID declared in `.research/research.yaml`;
- `previous_state_hash`: null at revision 0, otherwise the prior state
  SHA-256;
- `last_transition_id`: receipt identity for the last committed mutation;
- `pending_gate`: null or the blocking gate identity;
- `initial_baseline`: immutable baseline candidate identity after bootstrap;
- `provisional_best` and `current_best`: null or a BestPointer;
- `active_evaluation_spec`: null or the artifact- and gate-bound evaluation
  anchor.

A BestPointer contains `candidate_id`, `report_artifact_id`,
`evaluation_cycle_checkpoint_id`, and `promoted_at`. The bootstrap Current Best
may have null report/checkpoint IDs; every evaluated pointer requires both.

The evaluation anchor contains `evaluation_spec_id`, `artifact_id`,
`artifact_sha256`, `gate_id`, and `gate_payload_hash`.

## Allowed mutations

All writes pass through the workflow mutation kernel under the workflow lock.
The caller supplies `expected_revision`; a mismatch is a compare-and-swap
conflict, not permission to reload and silently overwrite.

Allowed owners are:

- project initialization: create revision 0 once;
- baseline bootstrap: set Initial Baseline and bootstrap Current Best together;
- evaluation lock: replace the active Evaluation Contract (`EvaluationSpec`) anchor after its gate;
- evaluation promotion: set/clear Provisional Best or advance Current Best;
- stage transition: change `current_stage` along a declared `next` edge; and
- workflow recovery: resume the same hash-bound mutation intent.
- pending-intent operations: `intent-inspect` is read-only; `intent-retry`
  delegates to the owning resume path; `intent-quarantine` atomically moves the
  intent into `.research/quarantine/` and appends a reason receipt. It never
  deletes an intent.

Workers, evaluators, execution adapters, and report writers cannot directly
mutate `state.json`.

## Checkpoint and commit order

For a transition or state mutation:

1. Load and validate the exact schema and active research graph.
2. Require the caller's `expected_revision`.
3. Hash the source state and graph and reject conflicting pending intents.
4. Prepare a deterministic mutation intent and the next state.
5. Write the checkpoint before changing the live state.
6. Append the matching journal receipt.
7. Atomically write the validated next state with `revision + 1`,
   `previous_state_hash`, and `last_transition_id`.
8. Mark the intent committed.

Recovery resumes those durable phases. It must not synthesize a new receipt or
skip the checkpoint because the desired state appears plausible.

## Invalid examples

```json
{"revision": 4, "current_stage": "made-up-stage"}
```

Invalid: fields are missing and the stage is not in the active graph.

```json
{
  "schema_version": 1,
  "revision": 2,
  "provisional_best": "candidate-7"
}
```

Invalid: a BestPointer is not a string and must bind report/checkpoint
provenance.

Invalid operations also include decrementing `revision`, editing a stale
snapshot without compare-and-swap, changing Initial Baseline after bootstrap,
or advancing state before its checkpoint and receipt exist.

## Verification checklist

- Load through `load_research_state`; do not trust raw JSON parsing alone.
- Confirm exact fields, schema version, non-negative revision, and declared
  current stage.
- Confirm nonzero revisions chain to a lowercase 64-character SHA-256.
- Confirm every evaluated BestPointer resolves both provenance IDs.
- Confirm the active EvaluationSpec artifact and gate hashes still match.
- Confirm the latest journal receipt and checkpoint agree with
  `last_transition_id`, source hash, target hash, and revision.
- For a write, require the expected revision and one committed mutation intent.
