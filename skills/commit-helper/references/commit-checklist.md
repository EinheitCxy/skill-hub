# Commit Checklist

Before each commit, verify:

1. The staged diff expresses one purpose and matches the planned paths/hunks.
2. Dependencies are complete in this commit or earlier commits.
3. Tests, docs, and publishable outputs match the change; caches, logs, and intermediate data are excluded unless required.
4. Other staged, unstaged, and untracked work is preserved with clear ownership.
5. The message explains the behavior or purpose, rather than a generic "update".

Before pushing, verify the remote, branch, and outgoing commits. Stay within authorization; force pushes and history rewriting require explicit approval.
