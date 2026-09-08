# ML Execution Contract

## Repository Identity

Record:

- resolved code-bearing root;
- nested repository relationship;
- branch and HEAD;
- dirty paths;
- applicable instruction files;
- package and environment metadata.

Never use a parent archive or document workspace Git status as proof of a nested repository state.

## Configuration Precedence

Prefer:

1. explicit user command;
2. authoritative experiment protocol;
3. current config files;
4. source defaults;
5. README examples;
6. historical notes.

Report conflicts instead of silently choosing.

## Fixed, Editable, Generated

| Surface | Meaning |
| --- | --- |
| Fixed | Dataset, evaluator, metric, schema, infrastructure, or contract not authorized for change |
| Editable | Files or parameters the workflow permits changing |
| Generated | Checkpoints, reports, logs, caches, compiled artifacts, temporary outputs |

Changing a fixed surface changes the experiment, not merely the implementation.

## Checkpoint Contract

Identify:

- model-only or full training state;
- plain state dictionary or wrapped dictionary;
- required companion config;
- multi-file components;
- supported extension and discovery order;
- map location and dtype;
- strict versus non-strict loading;
- optimizer/scheduler/scaler requirements;
- output naming and overwrite behavior.

## Evaluation Contract

Identify:

- evaluator command;
- inputs and device;
- correctness metric;
- performance metric;
- warm-up and compile behavior;
- output files;
- pass threshold;
- automatic versus opt-in triggering.

Do not equate a generated metrics file with valid metrics when fields can remain `nan` or empty.

## Runtime Contract

Carry:

- hardware;
- precision;
- batch size;
- input size or horizon;
- data cap;
- number of steps;
- compilation state;
- competing processes;
- timing scope.

Explain theoretical scaling separately from measured runtime.
