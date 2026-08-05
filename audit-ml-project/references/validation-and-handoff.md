# Validation and Handoff

## Validation Layers

1. **Static:** syntax, imports, config parse, file layout.
2. **Smoke:** smallest data and runtime path proving wiring.
3. **Correctness:** evaluator or repository tests.
4. **Performance:** measured throughput, latency, memory, or energy with stated conditions.
5. **End-to-end:** intended dataset, checkpoint, hardware, and output.

Report each layer separately.

## Training

- confirm the smoke cap is removed for full training;
- confirm GPU/device from logs and external process state;
- monitor checkpoint creation and metric logging;
- wait for background training before evaluation;
- change one memory-sensitive variable at a time.

## Evaluation

- verify config and weight paths exist;
- inspect checkpoint format before loading;
- run correctness before throughput;
- distinguish per-step, per-query, and full-sequence timing;
- record warm-up and compilation;
- inspect report files for missing or `nan` metrics.

## Packaging

- resolve the exact target and source state;
- prefer recoverable, non-destructive operations;
- avoid secrets and environment directories;
- inspect file list and size;
- compute a hash;
- record how to reproduce the archive.

## Final Checklist

- outcome led the report;
- exact paths and commands are included;
- fixed/editable/generated surfaces are clear;
- repository state is current;
- tests run are named;
- unrun checks are named;
- temporary files are removed;
- README is updated if behavior changed;
- no unrelated user changes were discarded.
