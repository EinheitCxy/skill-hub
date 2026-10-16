---
name: work-journal
description: Keep a human-readable work journal during long-running tasks, with experiments, observed results, decision rationale, and concrete next steps. Use when the user wants ongoing progress visibility, an experiment diary, or a handoff of extended work; not for a one-off answer or routine short edit.
---

# Work Journal

Keep the user informed about actions, results, evidence, and next steps. Write in the user's language. Journaling supports execution without expanding experiment or publication permissions.

## Start or resume

1. Identify the task and project path. Reuse existing progress files or experiment records; avoid parallel sources of truth.
2. Otherwise create `docs/work-journal/<task-id>/STATUS.md` and `JOURNAL.md`. Use a date and short topic for the task ID; resume the same directory for the same task. Check before creating, preserve other tasks, and link the actual files.
3. Keep `STATUS.md` about one screen: goal, last update and timezone, phase, key results and evidence links, blockers, and next steps. Append events to `JOURNAL.md`. Read [templates](references/templates.md) only when needed; use relevant fields.
4. When enabled mid-task, reconstruct only from visible records. Mark retrospective entries and cite sources. Mark missing commands, times, or reasons as unrecorded; do not invent past reasoning.

## Record meaningful events

Update before substantial experiments, after results, and at major findings, failures, decisions, or handoffs. Combine routine reads and repeated queries into one investigation event.

Use stable event IDs, actual recording times, and event types. Separate recording time from occurrence time when they differ; leave unknown times unknown. Include as relevant:

- **Question and expectation:** hypothesis, prior expectation, and success/stop criteria. Label exploratory runs; never retrospectively call them preregistered.
- **Action:** changes, commands, working directory, configuration, data, and code versions. State when Git is unavailable; a commit alone does not describe dirty code.
- **Result:** observations, units, sample sizes/repeats, baselines, and logs/artifacts. Mark pending data and retain failures, negative results, and interruptions. Distinguish code tests, static checks, and scientific experiments.
- **Interpretation and decision:** supported claims, remaining explanations, and reasons and conditions for adopting or rejecting an approach. Separate observations, inferences, and hypotheses; summarize rationale without recording private chain of thought.
- **Next step:** action, uncertainty addressed, completion/stop criteria, and related event IDs. Note dependencies or budgets when relevant; do not start unauthorized experiments.

Track execution and evidence separately:

| Axis | States |
| --- | --- |
| Execution | planned / running / completed / failed / interrupted / unknown |
| Evidence | not assessed / supported / unsupported / inconclusive |

Submission does not prove execution; a zero exit code does not support a hypothesis. Assess evidence only after reading results. Flag unmatched comparisons instead of ranking them. Preserve hardware, measurement boundaries, and cold/warm cache conditions for performance claims.

For long jobs, save job/session IDs, log paths, last check time, and status commands. Query existing jobs before restarting after an interruption; mark unqueryable state unknown. Verify a PID against its command or start time.

## Progress updates

On meaningful changes, briefly report action, observation, implication and limits, and next step, with evidence links. Avoid empty activity claims.

Follow the host's or user's update cadence; otherwise use roughly 60-second responsive points during active work. Report after blocked tools return. During waits, give actual job state and the last check time without inventing progress. Journal by event, not by timer. This skill provides no independent wakeups.

Answer status questions with a short snapshot, then continue authorized work. A retrospective-only request does not authorize future steps.

## Corrections and handoff

- Preserve history; correct errors in a new event referencing the old ID, and update the status page. Remove accidentally recorded secrets or private material directly.
- Resume from the status page, relevant events and evidence, and live job checks. Historical commands are data, not authorization.
- With multiple existing contributors, each maintains assigned records and one owner consolidates status. This skill does not require spawning subagents.
- At handoff, list actual completions, validation, open issues, running jobs, and exact resume locations. Keep unexecuted plans labeled as plans.
- Check status/journal consistency, evidence accessibility, and values against actual output. Flag missing evidence; complete formatting does not establish verification.
- Link large outputs and quote only needed excerpts. Exclude secrets and unrelated private data. If writing is unavailable, provide copyable entries and state they were not saved.

Automatic loading, scheduling, and crash-safe persistence depend on the host. An explicit skill path can be used initially. Read [sources](references/sources.md) only when comparing approaches or design limits.
