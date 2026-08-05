---
name: audit-ml-project
description: "Inspect how an ML project actually runs: repository structure, training and resume contracts, evaluation, checkpoints, metrics, commands, runtime evidence, and handoff artifacts. Use when the user asks for an ML codebase walkthrough, runbook, checkpoint evaluation, command or GPU-log diagnosis, fixed-versus-editable boundaries, or a clean handoff package. Default to read-only evidence gathering; do not use for generic feature implementation or claims unsupported by actual project evidence."
---

# Audit an ML Project

## Objective

Produce a repository-specific operational packet grounded in inspected files and captured commands. Distinguish what is verified, inferred, guidance-only, or still unknown.

## Load References Selectively

- Read [operation-modes.md](references/operation-modes.md) to select `orient`, `train`, `resume`, `evaluate`, `debug`, or `package`.
- Read [ml-execution-contract.md](references/ml-execution-contract.md) before describing entrypoints, configuration, checkpoints, evaluators, or generated artifacts.
- Read [evidence-and-command-integrity.md](references/evidence-and-command-integrity.md) before reporting that a command ran, passed, failed, or was blocked.
- Read [validation-and-handoff.md](references/validation-and-handoff.md) before executing validation or producing a package.
- Read [subskills-and-tools.md](references/subskills-and-tools.md) when debugging, consulting current framework documentation, or working with a specific ML stack.

## Workflow

### 1. Confirm Scope and Mutation Boundary

1. Restate the requested operational outcome.
2. Determine whether the request is explanation, diagnosis, execution, modification, or packaging.
3. Default to read-only for introduction, review, explanation, and diagnosis.
4. Require an explicit request before changing code, configuration, checkpoints, running jobs, or packaging files.
5. Preserve unrelated user changes and dirty worktrees.

### 2. Locate the Real Repository

1. Read all applicable `AGENTS.md` or equivalent instructions.
2. Identify the actual code-bearing repository root and nested repositories.
3. Inspect Git branch, HEAD, and worktree state from the correct root.
4. Read README, package metadata, environment files, configs, entrypoints, and evaluator definitions.
5. Prefer current configuration and source behavior over stale README examples when they conflict.

Start with the read-only snapshot helper:

```bash
uv run python scripts/collect_repo_snapshot.py <path> --mode <mode>
```

The helper inventories names and Git facts; it does not replace reading the relevant files.

### 3. Map the Execution Contract

Trace the applicable chain:

```text
data -> preprocessing -> model -> objective -> optimizer -> checkpoint
checkpoint + config -> loader -> inference engine -> evaluator -> metrics -> report
```

Record:

- fixed infrastructure;
- user-editable surface;
- generated artifacts;
- external dependencies and hardware assumptions;
- configuration precedence;
- file and checkpoint naming;
- metric direction and release condition.

### 4. Verify Against Actual Evidence

Attach each claim to:

- file and line;
- actual command and captured output;
- log excerpt;
- Git fact;
- generated artifact;
- official current documentation when library behavior is involved.

Never claim a command was run, passed, denied, or sandbox-blocked without the corresponding invocation and output.

### 5. Execute Only the Requested Mode

- `orient`: explain architecture, data flow, key files, and reading order.
- `train`: identify the authoritative training entrypoint, environment, config, smoke path, full-run path, and observability.
- `resume`: verify checkpoint completeness, config compatibility, optimizer/scheduler state, output directory, and overwrite risk.
- `evaluate`: verify loader contract, config and weight layout, evaluator, metrics, device, outputs, and release gate.
- `debug`: reproduce or inspect the exact command and logs; isolate the earliest supported cause before changing parameters.
- `package`: freeze the intended source state, exclude generated state, build a recoverable artifact, and inspect its contents.

Do not silently expand one mode into another.

### 6. Validate Proportionally

1. Run repository-specific checks from the actual repository root.
2. Use `uv` for Python commands.
3. Separate smoke tests, correctness tests, performance benchmarks, and full training.
4. Separate cold start, compilation, and steady state when relevant.
5. For GPU issues, inspect exact CLI caps, tensor shapes, precision, competing processes, and memory evidence before tuning.
6. For packaging, compare the artifact against the selected source state and inspect the archive listing.

### 7. Update Documentation After Changes

When implementation or operational behavior changes:

- update the repository README with the current command and artifact contract;
- record tests actually run and their results;
- delete temporary fixtures;
- state unrun checks explicitly.

## Operational Packet

Return:

1. verified repository identity and state;
2. selected mode and outcome;
3. architecture or execution chain;
4. fixed, editable, and generated surfaces;
5. exact commands and prerequisites;
6. verified facts, inference, and unknowns;
7. risks and mismatches;
8. validation checklist and results;
9. next safe action.

For diagnosis-only requests, stop after identifying the root cause unless the user also asked for a fix.
