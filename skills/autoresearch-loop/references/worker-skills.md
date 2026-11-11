# Autoresearch Worker Skills

A scannable map of the eight single-level worker skills that the
`autoresearch-loop` Coordinator dispatches. Each worker consumes registered
inputs, writes only its own task-local `report.md`, and returns a bounded
receipt (path, summary, status, artifact IDs).

## Worker roster

| Skill | Role | Phase | Produces |
|---|---|---|---|
| `research-literature-review` | Literature discovery and screening worker | 1 | evidence cards, rejected-source log, citation-verified artifacts |
| `research-paper-analysis` | Single-source deep-analysis worker | 2 | claim-evidence map, method spec, reproduction plan |
| `research-hypothesis-generation` | Candidate-generation worker | 3 | falsifiable candidate pool (exploit / explore / falsify) |
| `research-experiment-design` | Experiment-design worker | 4 | lock-ready Evaluation Contract (`EvaluationSpec`) |
| `research-experiment-execution` | Implementation-and-run worker | 5 | changed-file manifest, command receipts, execution evidence |
| `research-experiment-evaluation` | Independent Evaluator (advisory) | 6 | EvaluationReport + non-binding verdict recommendation |
| `research-evidence-critique` | Adversarial critique worker | 7 | graded concerns, cheapest decisive falsification, recommendation |
| `research-result-synthesis` | Final-report synthesis worker | 8 | Claim-Evidence Contract, citation verification, final report |

## Dispatch flow

`autoresearch-loop` (the Coordinator) is the sole dispatcher. It opens each
worker task with a finite Task Budget Allocation, disjoint editable paths, a
`brief.md`, and one designated `report.md`. Phase order:

```
autoresearch-loop (Coordinator)
        |
        v
literature-review -> paper-analysis -> hypothesis-generation ->
experiment-design -> experiment-execution -> experiment-evaluation ->
evidence-critique -> result-synthesis
```

## Coordinator and worker responsibilities

- The Coordinator alone dispatches workers, records gate receipts, and accepts
  worker outputs. Workers do not call each other and do not self-dispatch.
- Workers are single-level leaf agents: they do not spawn subagents and do not
  mutate global state, gates, budgets, journals, or artifact indexes.
- Each worker owns one task directory and one `report.md`; it cannot edit
  another skill's artifacts or promote the Verified Best Candidate.
- The Coordinator is intentionally NOT a row in the worker roster above, because
  it owns the loop rather than executing a research phase. See
  [Coordinator](../SKILL.md) for the Coordinator contract, gate authority,
  and cycle rules.
