# Runtime Navigation

Use this optional preparation step when workers repeatedly search the same
repository for training/resume commands, evaluators, or fixed run artifacts.
It is an EnvOpt-inspired information representation change, not a complete
automatic environment optimizer. It launches no model and executes no command.

## Audit and build

Following repository inspection, the Coordinator records a concise JSON spec
outside `.research/`, such as `runtime-index.spec.json`. A worker may produce
this file inside its declared output directory; only the Coordinator registers
and passes the resulting artifacts. Do not infer commands from filenames.

```json
{
  "schema_version": 1,
  "entries": [
    {
      "id": "train-smoke",
      "kind": "command",
      "summary": "Audited smoke command; config defines outputs and checkpoint naming.",
      "tags": ["train", "smoke", "训练"],
      "sources": ["train.py", "configs/smoke.yaml"],
      "argv": ["uv", "run", "python", "train.py", "--config", "configs/smoke.yaml"],
      "artifact_ids": []
    },
    {
      "id": "evaluate-checkpoint",
      "kind": "entrypoint",
      "summary": "Evaluator loads the checkpoint and writes per-split metrics.",
      "tags": ["evaluate", "checkpoint", "评估"],
      "sources": ["evaluate.py"],
      "artifact_ids": []
    }
  ]
}
```

Replace example paths and argv with inspected facts. Each entry has a unique
`id`, a `kind` (`entrypoint`, `command`, `artifact`), a short `summary`, and
`sources`. `tags` and `artifact_ids` default to empty lists. Only `command`
entries accept `argv`, a list preserving argument boundaries; shell strings
are rejected. Commands remain templates until the execution worker prepares
an independently approved RunSpec.

Sources are explicitly named project-relative files, not directories or globs.
They must remain inside the project, outside controller state, and each be at
most 256 KiB. Include the source/config files whose changes invalidate the
entry; freshness does not imply a scan of transitive imports or the entire
repository. Existing access permissions still apply.

An `artifact` entry can use `sources: []` with one or more verified
`artifact_ids`. Use these IDs for **fixed** run/checkpoint/evaluation/report
relationships, describing their relationship in the summary. Do not freeze a
dynamic `latest` or `Current Best` into the index: resolve ResearchState and
BestPointer at task creation as before. Never put held-out answers into an index.

The following are internal Coordinator operations, not instructions for User
to manage controller state manually. `LOOP` below denotes the skill directory.

```bash
uv run "$LOOP/scripts/researchctl.py" runtime-index-build \
  --root /path/to/project --spec /path/to/project/runtime-index.spec.json
```

The result is a `runtime-index` artifact ID. The spec and referenced source
files are not modified. Sources become immutable `runtime-source` snapshots;
the index stores only concise navigation and registered identities. Identical
builds reuse existing artifacts. Build once per relevant source revision,
not before every worker or controller operation.

## Select before task-open

```bash
uv run "$LOOP/scripts/researchctl.py" runtime-context \
  --root /path/to/project --index-id artifact-INDEX \
  --query 'resume checkpoint' --kind command --limit 5 --max-chars 6000
```

Selection uses case-insensitive lexical matching over entry IDs, tags,
summaries, source paths and argv. IDs/tags rank above descriptive text; ties
sort by ID. Chinese tags support Chinese queries. Use a few concrete terms;
this is not a semantic search service.

Only positive matches are returned. Defaults cap the excerpt at 5 entries and
6000 **serialized characters**, not tokens. Maximum options are 10 entries and
12000 characters. The excerpt is a complete ranked prefix; entries and argv
are never truncated. If the first result cannot fit, narrow its contents or
explicitly raise the character budget. `no_matches` creates no context artifact:
perform a bounded repository lookup and amend the audited spec if useful.

Merge the returned `input_artifact_ids` with the task's other declared inputs
before `task-open`. The returned IDs include the immutable `runtime-context`
excerpt and only the selected source/linked artifacts. Do not inject the full
index, unrelated sources, reports, or conversation history. Existing task
schemas and skill-count limits are unchanged.

The worker reads the excerpt first, then only relevant source snapshots using
their paths from Context Manifest. A `sources[].path` identifies the original
project file for navigation; it does not grant permission to read or execute
that live path. The character cap bounds the excerpt, not the contents of
linked source/weight artifacts: do not eagerly load every linked file.

## Freshness and versions

Both selection and task-open verify selected source bytes against their
registered snapshots, plus artifact availability/integrity. Missing dependency
IDs fail before task creation or budget reservation. An unrelated stale entry
does not invalidate a fresh selection.

On a stale result, inspect the change, update the audited spec if the command
or meaning changed, then rebuild with `--parent-id artifact-OLD-INDEX`. New
content produces a child version; unchanged content reuses its parent. Merely
rebuilding does not prove an unchanged command still has the same semantics.
No API silently picks a mutable `latest` index or repairs an old task.
An explicit build/select verifies reused snapshots. If canonical dedup returns
an unavailable, missing or corrupt generated snapshot, the operation creates
or reuses a verified child snapshot; it never overwrites the damaged history.
An unavailable index must be rebuilt from the audited spec before selection.

Already opened tasks keep their original brief, input IDs and source snapshots.
New sources, context or task contracts require a new task identity. Execution
still obeys the approved scope, RunSpec and EvaluationSpec.

## Measure before claiming savings

Use paired original/indexed tasks with the same project snapshot, model,
reasoning effort, harness, permissions and execution budgets. Use fresh agent
contexts, account for cache/order effects, and include tasks not used to refine
the index. Check entrypoint/command/checkpoint correctness first; separately
record time to the first valid command, repeated searches, tool calls, and
provider-reported tokens/cost when available. Keep missing provider usage null.

Include index generation and refresh costs in total cost. Retain failures and
negative results. Keep this preparation only if correctness stays within a
predeclared tolerance and amortized cost improves. This implementation and its
fixture tests do not establish real-agent token savings or better ML results.

Method reference: [EnvOpt, Sections 2 and 4](https://arxiv.org/html/2611.07372v1).
