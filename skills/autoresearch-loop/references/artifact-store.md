# Evidence Registry Contract (internal: ArtifactStore)

## Purpose and owner

The Evidence Registry (`ArtifactStore`) is the evidence plane. It gives every research input, receipt,
log, metric, report, and model output an immutable identity plus verifiable
provenance. The Evidence Registry alone owns registration and availability updates;
workers produce files but cannot invent artifact IDs.

The append-only machine index is `.research/artifacts/index.jsonl`. Internal
controller operations are `artifact-register`, `artifact-resolve`,
`artifact-list`, `artifact-verify`, and `artifact-unavailable`.

## Registration record

Caller metadata requires:

- `type`: semantic evidence class;
- `producer`: owning skill or controller component; and
- `storage`: `snapshot` or `external`.

Optional provenance includes `subagent`, `skill_versions`, `research_id`,
`cycle_id`, `task_id`, `candidate_id`, `run_id`,
`evaluation_cycle_checkpoint_id`, `report_id`, `prompt_hash`,
`provenance_tags`, and `comparability_tags`. A caller may declare `sha256` and
`size_bytes`; registration rejects disagreement with locally observed bytes.

The stored record adds `artifact_id`, canonical `path`, `size_bytes`, SHA-256,
`parent_artifact_id`, `availability`, `registered_at`, and
`unavailable_reason`.

## Immutability and versions

A `snapshot` copies one regular file into
`.research/artifacts/<artifact-id>/`. Its content hash and size are verified
after the copy. The stored bytes and record identity are immutable.

Changing evidence creates a new artifact. `register_artifact_version` requires
the previous identity and records it as the `parent artifact`; it never
overwrites the parent.

Registration is idempotent when the content hash and complete provenance
identity match an existing record. The controller returns that canonical ID
instead of copying another snapshot. A changed provenance identity (including
parent, task, prompt, or tags) remains a distinct artifact. Existing index rows
are append-only and are not removed during deduplication.

`mark_artifact_unavailable` appends a later availability record for the same
identity. It does not change the content hash, provenance, or history.

## External and large artifacts

Use `storage: external` for a model weight, remote dataset, or other large
artifact that should not be copied. A locally available external path is
hashed. An unavailable URI must supply a valid SHA-256 and non-negative
`size_bytes`; verification then reports unavailable rather than pretending the
bytes were checked.

External reference does not mean trusted. Evaluation or promotion must require
locally verifiable evidence when its contract demands `available: true` and
`valid: true`.

## Allowed operations

- Register one new immutable snapshot or external reference.
- Register a new version linked to an existing parent.
- Resolve by artifact identity.
- List by the supported `task_id`, `candidate_id`, `run_id`, or `type` filters.
- Verify current bytes against the registered hash and size.
- Append a reasoned unavailable status.
- For run settlement, verify unique positional artifact IDs, exact types,
  snapshot storage, run identity, SHA-256, and size before charging bytes.

Deleting an index line, reusing an ID for different bytes, editing a snapshot,
or changing metadata in place is forbidden.

## Invalid examples

```json
{"type": "metrics", "storage": "snapshot"}
```

Invalid: `producer` is missing.

```json
{
  "type": "model-weight",
  "producer": "executor",
  "storage": "external",
  "path": "https://example.invalid/weights.bin"
}
```

Invalid when unavailable: the external reference lacks declared hash and size.

Also invalid: registering a directory as a snapshot, declaring a false hash,
using duplicate artifact IDs as storage evidence, assigning a run artifact to
another `run_id`, or treating `availability: unavailable` as verified.

## Verification checklist

- Resolve the identity from `index.jsonl`; never trust a path alone.
- Check expected type, producer, task/candidate/run identities, and provenance.
- For snapshot evidence, require `available is true` and `valid is true`.
- Recompute SHA-256 and size for every promotion, completion, or settlement
  boundary that consumes the artifact.
- Confirm a version's `parent_artifact_id` resolves and remains unchanged.
- Confirm external evidence clearly distinguishes available, unavailable, and
  invalid states.
- Confirm storage accounting includes only unique, newly registered run
  snapshots and applies only the approved exclusion policy.
