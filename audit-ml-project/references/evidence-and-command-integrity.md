# Evidence and Command Integrity

## Status Vocabulary

- **Verified from file:** inspected in the current checkout.
- **Verified by command:** command invoked and output captured.
- **Verified by artifact:** output exists and was inspected.
- **Inferred:** reasoned from named evidence.
- **Guidance only:** command path identified but not executed.
- **Unknown:** evidence unavailable or ambiguous.

## Command Reporting

Before saying “ran,” “passed,” “failed,” “denied,” or “blocked”:

1. show that the command was invoked;
2. capture exit code or tool status;
3. retain the relevant output;
4. distinguish program error, dependency error, permission failure, timeout, and external service failure.

Treat sandbox, hook, classifier, or permission causes as hypotheses until the tool output identifies them.

## Root-Cause Discipline

1. Read the exact command and earliest relevant error.
2. Compare actual inputs and state with the repository contract.
3. Separate symptom, proximate cause, and underlying cause.
4. Run one discriminating check.
5. Diagnose before modifying.

## Claims to Avoid

- “The GPU was unused” without device or utilization evidence.
- “The whole dataset trained” when a sample cap was active.
- “Evaluation ran automatically” without the trigger path.
- “The checkpoint is complete” without inspecting required components.
- “The package is clean” without inspecting its contents.
- “The test passed” when only static inspection was performed.

## Current Documentation

When behavior depends on a library, framework, SDK, or cloud API, consult current official documentation. Keep repository-specific evidence primary for how the project actually uses it.
