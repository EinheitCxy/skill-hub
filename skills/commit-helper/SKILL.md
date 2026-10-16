---
name: commit-helper
description: Use when organizing, splitting, staging, or committing mixed Git changes, including multiple features, experiments, generated artifacts, or pre-staged work.
---

# Commit Helper

Group changes by reviewable purpose and dependency. Preserve unrelated work and existing staging.

## Inspect

Record the branch, worktree, HEAD, and staged, unstaged, and untracked changes. Resolve unclear ownership, target remote, branch, or base before staging. Branch names do not grant authorization.

Use the current worktree unless isolation is requested or needed for safe parallel work. Do not default to `git add .`, reset, stash, clean, or history rewriting. The approved merge workflow below permits temporary stashing.

For mixed staged/unstaged hunks or unclear ownership, read [staged-and-mixed-hunks.md](references/staged-and-mixed-hunks.md) and ask when needed.

## Split and commit

1. Map each change to its purpose, paths, hunks, and dependencies.
2. Classify artifacts using [artifact-classification.md](references/artifact-classification.md). Exclude unrelated formatting, caches, logs, and experiment leftovers.
3. Group by feature, fix, or prerequisite; order dependencies first. Split shared files by hunk.
4. Stage only the intended paths or hunks. Check [commit-checklist.md](references/commit-checklist.md), the actual commit diff, and preservation of remaining work.

Commit and push only as authorized. Existing task authorization covers necessary steps without repeated approval. Before pushing, verify the remote, branch, and outgoing commits. Force pushes and history rewriting require explicit authorization.

## Merge and sync main

After authorized commits and required checks, if on a named non-`main` branch, ask whether to merge it into remote `main`; name the branch and remote. Prior explicit approval for that branch suffices. Wait for approval before proceeding.

1. Push as needed, complete the remote merge using the project's workflow, and verify success. Merge the existing PR when present. Only then sync local `main`.
2. Before switching or syncing, stash uncommitted changes with `git stash push -u` in each affected worktree. Record each stash, original branch, and staging state; confirm the stash succeeded.
3. In the worktree holding local `main`, run `git fetch <remote>` and `git merge --ff-only <remote>/main`. If no worktree holds `main`, switch to it first. Fetch the merged result from remote `main`, not the feature branch.
4. Restore only the stashes created here, on their original branches and worktrees. Use `git stash pop --index <stash>` to preserve staging. Never move feature-branch changes onto `main` by accident.
5. Verify local `main` matches remote `main` and all saved changes and staging are restored. On failure, divergence, or stash conflicts, stop dependent steps, preserve remaining stashes, and report recovery locations; do not reset or rebase automatically.

## Report

Give the initial state, classification, and ordered commit plan. Each group includes purpose, paths/hunks, exclusions, required checks, and commit message. After execution, report commits, preserved work, and remote merge, local sync, and stash restoration status when applicable.
