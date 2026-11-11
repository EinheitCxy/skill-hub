---
name: experiment-audit
description: "Inspect how an ML project actually runs, profile end-to-end latency and GPU/host memory peaks, or diagnose a training, checkpoint, evaluation, or packaging issue from repository evidence. Use for repository walkthroughs, runbooks, and operational diagnosis; not generic feature implementation."
---

# Experiment Audit

## Outcome

Give a repository-specific answer grounded in inspected files, commands, logs, or artifacts. Label facts, inference, guidance, and unknowns separately. Keep the response limited to the user's operational mode: `orient`, `train`, `resume`, `evaluate`, `debug`, `profile`, or `package`.

## Start Small

1. Restate the requested outcome and whether it authorizes reading, diagnosis, execution, modification, or packaging. Explanation and diagnosis are read-only by default.
2. Read applicable local instructions, then inspect only the code, config, log, checkpoint, or evaluator needed for the chosen mode.
3. Use `uv run python <skill-dir>/scripts/collect_repo_snapshot.py <path> --mode <mode>` only when the repository root, Git state, or likely entrypoints are unclear. It inventories names; it does not prove behavior. For `profile`, use the helper’s existing `orient` mode only if this inventory is needed.
4. Report the answer for the selected mode. Do not turn an orientation request into a runbook or a diagnosis into a fix unless asked.

Read [operation-modes.md](references/operation-modes.md) for the chosen mode. Read the following only when their topic is in scope:

- [profiling.md](references/profiling.md) for representative inputs, cache states, stage boundaries, GPU synchronization, end-to-end timing, and memory peak definitions;
- [ml-execution-contract.md](references/ml-execution-contract.md) for commands, configuration, checkpoints, evaluation, or runtime;
- [evidence-and-command-integrity.md](references/evidence-and-command-integrity.md) before reporting a result, failure, or cause;
- [validation-and-handoff.md](references/validation-and-handoff.md) before running checks or building a package;
- [subskills-and-tools.md](references/subskills-and-tools.md) only when a specific framework, current documentation, or debugging method is needed.

## Mode Output

Return only the fields needed to make the next action safe.

| Mode | Default answer |
| --- | --- |
| `orient` | What the project does, relevant execution chain, key files, and unknowns. |
| `train` / `resume` | Authoritative command, required state, outputs, and overwrite or compatibility risks. |
| `evaluate` | Loader and evaluator contract, metric meaning, result path, and release condition. |
| `debug` | Earliest supported cause, evidence, one discriminating check, and fix only if requested. |
| `profile` | Measured scope and inputs, cold/warm timing, stage attribution, GPU/host peaks with explicit definitions, and evidence limits. |
| `package` | Selected source state, inclusions and exclusions, artifact check, and reproduction record. |

For a full operational handoff, also include repository identity, fixed/editable/generated boundaries, prerequisites, evidence status, validation results, unrun checks, and the next safe action.

## Non-Negotiable Evidence

- Prefer current source and config over stale README examples; report conflicts.
- Attach material claims to a file location, command output, log, Git fact, artifact, or primary documentation.
- Do not say a command passed, failed, or was blocked without its actual result.
- Keep correctness, performance, and end-to-end evidence distinct; keep hardware, precision, data cap, and timing scope with runtime claims.
- Diagnose the earliest evidence-backed divergence before changing parameters. Preserve unrelated working-tree changes.

When a requested change alters operational behavior, update the repository README, record checks actually run, and remove temporary fixtures.
