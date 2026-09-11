# Skill Hub

十个按需使用的学习、研究、开发与写作工作流 skills。默认只完成当前问题；完整课程、正式综述和决策包仅在明确需要时展开。

## 选择 skill

| 调用名称 | 用途 | 默认输出 |
| --- | --- | --- |
| `teacher` | 学习、练习、测验与源码讲解 | 按请求给学习练习，或源码调用链、形状变化和代码位置 |
| `autoresearch` | 审计 ML 运行、评估、报错及性能 | 当前模式的结论与证据；profiling 区分端到端耗时和显存/RAM 峰值口径 |
| `literature-review` | 多篇文献的证据比较与正式综述 | 围绕问题的比较、来源和证据缺口 |
| `paper-review` | 讲解已提供论文的机制与实验 | 问题、方法、证据和局限 |
| `choose-research` | 比较研究方向并作取舍 | 一个主方向、关键重叠、首个证伪实验和失败响应 |
| `commit-helper` | 将混杂改动整理为功能提交 | 归属明确的提交边界、逐提交核对、按授权 push |
| `results-validator` | 分析已产生的反常实验结果 | 核对条件、重算统计、竞争解释及区分实验 |
| `humanizer` | 保留原意与作者语气，去除 AI 写作痕迹 | 粘贴文本给初稿、简短检查与终稿；文件仅写最终正文 |
| `research-figure-creator` | 从实验数据制作可重生成的论文图 | 科学问题、统计口径、图文一致性与重生成入口 |
| `work-journal` | 长任务的持续进度、实验记录与交接 | 一页当前状态、追加式事件日志、证据与决策依据、下一步 |

入口均为 `skills/<调用名称>/SKILL.md`。详细计划、模板与专门检查项在各 skill 的 `references/` 中按条件加载，不默认逐项读取或调用其他 skills。

## 按需能力

- `autoresearch`：请求全流程耗时、显存或内存峰值时加载 `references/profiling.md`，覆盖代表输入、冷暖缓存、阶段边界、GPU 同步、统计窗口与峰值口径。诊断 trace 与最终性能测量分开。`profile` 是 skill 工作模式；现有 snapshot 脚本仅在需要项目盘点时用 `--mode orient`，不是 profiler。
- `teacher`：请求结合源码理解模型时，追踪真实调用链，解释输入输出、tensor shapes 与数学操作并链接代码位置；区分静态推导和运行观测，不强制先做课程规划或测验。

新增三项独立工作流按需使用：结果诊断不自动切换研究方向，作图不替代数据验证，分功能提交不默认加入实验中间数据或缓存。`commit-helper` 按当前会话授权决定是否提交和 push，加载 skill 本身不代表发布授权。

## 安装到 Codex

使用内置安装器，按需安装：

```text
$skill-installer install https://github.com/EinheitCxy/skill-hub/tree/main/skills/teacher
```

将 `teacher` 替换为表中其他名称。安装后重启 Codex。本地修改不会自动更新远程仓库或已安装副本。

## 安装到 Claude Code

在 Claude Code 中执行：

```text
/plugin marketplace add EinheitCxy/skill-hub
/plugin install skill-hub@skill-hub
```

终端也可执行：

```bash
claude plugin marketplace add EinheitCxy/skill-hub && claude plugin install skill-hub@skill-hub --scope user
```

重新加载插件或重启后，使用 `skill-hub:teacher` 等带命名空间的名称。私有仓库需要相应 GitHub 读取权限。

## Work Journal

长任务开始时使用：`用 work-journal 持续记录这项任务：每次实验、结果、决策依据和下一步都留下记录，并在关键变化时向我简报。`

尚未安装时，可让 agent 读取本地 `skills/work-journal/SKILL.md` 并按其执行。安装方式沿用上文，将名称替换为 `work-journal`；远程安装需先将新增目录发布到仓库。Claude Code 插件安装后使用 `skill-hub:work-journal`。

默认在任务项目的 `docs/work-journal/<task-id>/` 保存 `STATUS.md` 和 `JOURNAL.md`；已有日志则复用。前者回答当前进展，后者保留实验、失败、证据、决策更正和接续入口。“中间思考”记录为简短的假设、证据与取舍摘要。运行完成和假设得到支持分别标记，不混为一谈。

可随时问：`现在做过哪些实验？哪些结论有证据，下一步验证什么？` 技能不包含后台定时器或宿主 hooks；更新依赖 agent 在当前任务中执行，未进行实际长任务效果评测。

## Humanizer

调用 `humanizer`（Claude Code 中为 `skill-hub:humanizer`），提供待改写文本或文件路径；可附写作样本以匹配个人语气。例如：`用 humanizer 润色这段文字，保留全部事实和引用：……`。

基于 [blader/humanizer](https://github.com/blader/humanizer) 的 3.0.0 版本，固定上游提交 `9862685f575c65a8247f90369951df1b3416e3d6`。完整保留 25 类模式、示例、例外及来源说明；仅合并识别、改写、核验流程。技能目录仅含 `SKILL.md` 和上游 MIT `LICENSE`，独立项目的 README、发布配置、CI、校验脚本及可选 UI 元数据不随包导入。安装沿用本仓库统一入口，将上述 Codex 安装路径末尾替换为 `humanizer` 即可。

## 名称迁移

| 原名称 | 当前名称 |
| --- | --- |
| `learning-loop-coach` | `teacher` |
| `audit-ml-project` | `autoresearch` |
| `build-evidence-review` | `literature-review` |
| `paper-review` | `paper-review` |
| `narrow-research-direction` | `choose-research` |

不提供旧名称别名。已有安装需单独更新并移除不再使用的旧副本；本仓库不自动修改全局安装。其他插件可能也提供 `literature-review`，调用时优先明确 skill 路径或插件命名空间。

## 精简原则

- 保留来源、实验条件、事实与推断区分，以及可证伪性要求。
- 完整课程、七章论文报告、语料台账和方向决策包按请求展开。
- HTML 在文档交付需要公式渲染或明确要求时生成，普通讲解不自动导出。
- 打包按任务检查内容清单和来源状态，采用最小充分验证。
- 不把文件缩短比例视为实际 token 节省；真实成本需同任务对照运行测量。

`paper-review` 的 HTML 渲染脚本依赖 Pandoc，仅在需要公式文档交付时使用。

## 验证

安装了 Codex 官方 skill validator 时，可对各目录执行：

```bash
uv run --with pyyaml python /Users/ahs/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/teacher
```

脚本回归测试：

```bash
uv run python -m unittest discover -s skills/autoresearch/scripts -p 'test_*.py'
uv run --with bibtexparser python -m unittest discover -s skills/literature-review/scripts -p 'test_*.py'
uv run python -m unittest discover -s skills/choose-research/scripts -p 'test_*.py'
```

Claude Code 可用时，运行 `claude plugin validate .` 检查插件清单。正式报告的内容与渲染仍需按对应 skill 的交付要求验证。
