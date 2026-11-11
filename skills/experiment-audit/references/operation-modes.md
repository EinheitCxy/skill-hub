# Operation Modes

## `orient`

Answer:

- what the repository actually implements;
- the main training and inference chains;
- core versus infrastructure files;
- configuration hierarchy;
- practical reading order;
- documentation or code drift;
- current unknowns.

Do not modify the repository.

## `train`

Verify:

- environment and dependency contract;
- data paths and caps;
- model/config selection;
- device and precision;
- smoke command versus full command;
- checkpoint and logging behavior;
- metric and stop condition;
- expected output files.

Do not infer full-dataset runtime from a capped smoke test.

## `resume`

Verify:

- checkpoint is a single weight file, a full state, or a multi-file family;
- model config and architecture compatibility;
- optimizer, scheduler, scaler, and epoch state requirements;
- naming and output directory;
- overwrite or divergence risk;
- exact continuation command.

Do not treat an inference-only generator or adapter file as a complete training checkpoint.

## `evaluate`

Verify:

- expected config and weight layout;
- loader-supported wrappers and extensions;
- device and precision;
- evaluator entrypoint;
- correctness and performance separation;
- metric names, direction, and result path;
- whether evaluation is automatic or opt-in.

State when guidance is based on code inspection but the checkpoint was not actually evaluated.

## `debug`

1. Capture the exact command and complete relevant error.
2. Identify the earliest divergence from expected behavior.
3. Check CLI caps, config precedence, input shapes, device, precision, and competing jobs.
4. Form one evidence-backed hypothesis.
5. Run the smallest discriminating check.
6. Explain the cause before offering a fix.

Do not change multiple tuning variables at once.

## `profile`

Read [profiling.md](profiling.md). Define representative inputs and the measurement window, separate cold and warm execution, synchronize asynchronous work correctly, and distinguish end-to-end timing from diagnostic stages and memory peak scopes. Report only measured results and supported source-level attribution.

## `package`

1. Identify whether the deliverable is committed source, dirty working state, generated output, or model artifact.
2. Prefer a clean repository snapshot when that matches the request.
3. Exclude environments, caches, secrets, temporary files, and runtime workspaces.
4. Build in a temporary location.
5. Inspect the archive listing against the requested contents.
6. Record source branch/commit or explicit dirty-state provenance.
