# Testing Strategy

Keep verification proportional to the change. Test count is not a quality
target; each test must protect a distinct observable contract.

## Three levels

### Task focused

During implementation, run only the owning test file or exact test node:

```bash
uv run --with pytest --with "pyyaml>=6,<7" pytest \
  skills/autoresearch-loop/tests/test_executors.py -q
```

Use one RED run and one GREEN run. Do not repeat the full suite after
documentation, formatting, or an unrelated commit.

### Phase checkpoint

At a phase boundary, run the small integration spine for the changed layer.
For the runnable kernel this is:

```bash
uv run --with pytest --with "pyyaml>=6,<7" pytest \
  skills/autoresearch-loop/tests/test_executors.py \
  skills/autoresearch-loop/tests/test_workflow.py \
  skills/autoresearch-loop/tests/test_evaluation.py \
  skills/autoresearch-loop/tests/test_end_to_end.py -q
```

Documentation and skill-only phases run `test_skill_suite.py` plus the single
end-to-end test only when Coordinator wiring changed.

### Release full

Run the full suite once before merge, release, or the final Task 23 acceptance:

```bash
uv run --with pytest --with "pyyaml>=6,<7" pytest \
  skills/autoresearch-loop/tests -q
```

## Content rules

- Pure schema, required-field, and CLI route matrices use one table-driven test
  item with failure context rather than one pytest item per string.
- Keep separate failure injection cases for durable write phases, settlement,
  process ownership, concurrency, and recovery; each protects a distinct
  side-effect boundary.
- Keep one end-to-end gated fixture. Do not duplicate the complete lifecycle in
  every module.
- A focused behavior test owns one invariant. Delete a test when another test
  exercises the same public behavior and would fail for the same regression.
- Do not add tests for implementation-private line structure unless it is an
  approved architecture boundary.
- Do not rerun unchanged test groups merely to increase confidence; checkpoint
  evidence and the final full suite provide that assurance.
