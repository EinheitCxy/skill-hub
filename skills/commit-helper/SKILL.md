---
name: commit-helper
description: 将混杂的 Git 改动按可独立审查的功能、安全地拆分为多个提交。Use when the user asks to organize, split, stage, or commit a working tree that contains multiple features, fixes, experiments, generated artifacts, or pre-staged changes.
---

# Commit Helper

## 目标

按功能和依赖关系拆分改动，使每个提交说明一个可审查的意图，并保留不属于该提交的工作。提交不是按文件夹机械分组，也不因“工作区干净”而牺牲已有改动。

## 首先建立初态

在任何暂存、提交或推送操作前，记录当前分支、worktree、HEAD、已暂存/未暂存/未跟踪内容及其归属线索。把现有暂存区当作用户状态，尤其注意同一文件中已暂存和未暂存的混合 hunk。

不要默认执行 `git add .`、`git reset`、`git stash`、`git clean` 或改写历史。不要覆盖其他人的改动。若无法判断已有暂存内容属于哪个功能、是否可拆分，读取 `references/staged-and-mixed-hunks.md` 并向用户确认归属或拆分方式。

## 分支与 worktree

先在当前 worktree 内评估改动，并明确其对应分支。只有用户要求隔离开发或当前工作区无法安全并行时，才创建或使用另一个 worktree；不能把不同 worktree 的状态混为同一个提交计划。

如果目标分支、remote 或提交基线不清楚，先报告不确定性并查明它们，再准备提交。不要假定当前分支可以发布。

记录只描述事实，不将分支或 worktree 的名称推断为用户授权。

## 划分功能边界

1. 列出每项改动的路径、hunk、用途与依赖。
2. 区分源码、测试、文档、实验中间数据、模型缓存、日志和可发布图表；分类规则见 `references/artifact-classification.md`。
3. 按一个可验证功能、修复或独立准备步骤建立提交组。若 B 必须依赖 A，先提交 A；不把无关格式化、缓存或实验残留混入。
4. 对同文件的多项功能，按 hunk 而不是整文件归组；为每组写出明确的 paths 与 hunks 清单。

## 暂存与逐提交核查

只有在该功能归属明确时，才精确暂存其路径或 hunk。每次拟提交前，核对暂存 diff 是否只表达该功能、测试/文档是否与其一致，以及未暂存和未跟踪内容是否仍被保留。详细检查表见 `references/commit-checklist.md`。

不实际运行提交或推送，除非用户明确要求执行。已有明确授权时可按授权范围连续完成必要的提交与推送步骤，不机械地为每一步重复询问；推送前仍确认目标分支和将发布的提交集。除非用户明确授权，不修改历史、不强推。

## 输出

先给出初态和分类结论，再给出按依赖排序的提交计划。每个计划项必须包含：目的、涉及 paths/hunks、排除内容、必要验证与建议提交信息。执行后逐项报告已提交内容和保留的工作。
