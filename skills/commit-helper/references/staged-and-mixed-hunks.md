# Staged and Mixed Hunks

Record the branch, HEAD, worktree, staged and unstaged diffs, and untracked files. Do not reset, stash, or stage everything to start over.

Assign staged and unstaged hunks separately. Include existing staged work only when it belongs to this commit; preserve the rest. If ownership or a split within one hunk is unclear, explain the ambiguity and ask for attribution or approval to split it manually.

A normal commit includes the entire index: precise `git add` does not exclude previously staged changes. When the index mixes features, use a separate index, or adjust and restore staging within existing authorization. Verify preservation before proceeding; otherwise provide a reviewable plan.

After each commit, inspect its actual diff and verify remaining staged, unstaged, and untracked work, including staging state.
