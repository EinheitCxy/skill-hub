# Experiment Runner Contract (internal: EnvironmentExecutor)

## Purpose and owner

The Experiment Runner (`EnvironmentExecutor`) prepares approved dependencies and runs reproducible
commands. It owns process lifecycle and execution evidence, but never judges
scientific value. The Coordinator chooses a task and worker.
It must not launch an LLM. It also cannot invoke a skill, select a candidate,
or mutate the Research State Snapshot (`ResearchState`).

The first backends are `uv-local` and `command-local`. An unknown or unavailable
backend returns `status: failed` with `failure_type: unsupported_backend`.
There is no silent fallback or automatic backend switch.

## Interface

The six operations are:

1. `inspect(EnvironmentSpec)`: validate without changing the environment;
2. `prepare(EnvironmentSpec)`: execute the approved preparation command and
   register an environment receipt;
3. `run(RunSpec)`: reserve resources and run one bounded command;
4. `cancel(run_id)`: terminate only the recorded process group;
5. `collect(run_id)`: register declared outputs and settle usage once; and
6. `cleanup(run_id)`: remove only approved temporary paths that contain no
   registered artifact.

An `EnvironmentSpec` contains `environment_id`, `backend`, `cwd`,
`prepare_argv`, `dependencies`, `dependency_lock_path`,
`dependency_lock_hash`, `allowed_write_paths`, `temporary_paths`, and
`timeout_seconds`. Empty `prepare_argv` explicitly means no preparation and
still produces a successful receipt.

A `RunSpec` contains exactly:

- `run_id`, `environment_id`, `dependency_lock_hash`, and `backend`;
- `cwd` and an argv tuple, never a shell command string;
- a positive finite `timeout_seconds` and declared `resource_limits`;
- `input_paths`, `output_paths`, and positional
  `expected_artifact_types`;
- `candidate_id`, `evaluation_spec_id`, and
  `evaluation_cycle_checkpoint_id`.

`output_paths[n]` must produce `expected_artifact_types[n]`. Every path remains
inside the approved Change Scope (internal: `Modification Envelope`).

## Approval and provenance checks

Preparation and execution require an approved Change Scope and Resource Budget.
The command executable, working directory, dependencies, write paths, network
declaration, timeout, and cleanup action must match those envelopes.

Before spawning, the controller also verifies:

- the prepared environment receipt, dependency lock hash, and backend;
- the official candidate and its active locked Evaluation Contract (`EvaluationSpec`);
- the immutable evaluation-cycle checkpoint and captured comparison target;
- input path integrity and output declarations; and
- an available finite budget reservation for the run.

The executor uses `subprocess.Popen(argv, shell=False, ...)`. It does not
reinterpret argv, install undeclared dependencies, broaden write scope, or ask
another backend to try the command.

GPU runs are host-backed only. A run declaring `gpu_seconds > 0` or `gpu_type`
must execute where `/dev/nvidia*`, the NVIDIA driver, and GPU telemetry are
visible. The default agent sandbox may hide those devices; that is reported as
`sandbox-no-device` and is not treated as host GPU failure or a reason to use
CPU fallback. Provider usage that cannot be measured locally is optional audit
metadata; a reservation may be detached through the control plane when measured
settlement is not available, but settlement with absent or estimated usage is
accepted.

## Durable lifecycle and receipts

The restart-safe phases are `reserved`, `spawned`, `pid-persisted`,
`communicated`, `artifacts-registered`, `settlement-prepared`, and `terminal`.
Reservation happens before spawn. PID, process-group identity, argv,
environment, and checkpoint provenance are durable before waiting for process
completion.

Terminal status is `completed`, `failed`, `timeout`, or `cancelled`. Timeout,
cancellation, and interrupted recovery retain partial stdout and stderr.
Nonzero exit is evidence, not permission to repair or rerun silently.

Collection registers:

- `command-receipt`;
- `command-stdout`;
- `command-stderr`; and
- every declared output snapshot with its expected artifact type.

All carry run, candidate, EvaluationSpec, and evaluation-cycle provenance.
Storage accounting charges unique verified run snapshots once, then settles
the reservation once. Retrying `run` or `collect` resumes the same spec hash
instead of creating a second execution.

## Invalid examples

```json
{"backend": "uv-local", "argv": ["python", "train.py"]}
```

Invalid: `uv-local` argv must start with `uv`.

```json
{"argv": "uv run train.py", "timeout_seconds": 0}
```

Invalid: argv is not a tuple/list of arguments and timeout is not positive.

Also invalid: `shell=True`, falling back from `uv-local` to `command-local`,
launching a worker model, running outside approved paths, collecting an
undeclared output, killing an unverified PID, or settling unverified bytes.

## Verification checklist

- Inspect first; require explicit readiness before preparation.
- Match the prepared receipt and lock hash to the RunSpec.
- Require both human envelopes and the immutable evaluation-cycle checkpoint.
- Confirm `shell=False`, exact argv, positive timeout, and approved cwd.
- Confirm durable PID/process-group evidence before communication.
- Classify completion, nonzero exit, timeout, cancellation, and interruption.
- Verify every registered hash, output type, run ID, and storage charge.
- Confirm retry, collect, cancellation, and cleanup are idempotent.
