# Skill Hub

可复用的 Codex skills 集合。

## 原生安装到 Codex

在 Codex 中使用内置 `$skill-installer`，按需安装单个 skill：

```text
$skill-installer install https://github.com/EinheitCxy/skill-hub/tree/main/skills/learning-loop-coach
```

把 URL 最后的目录名替换为 `audit-ml-project`、`build-evidence-review` 或 `narrow-research-direction`，即可安装其他 skill。安装后重启 Codex 以加载新 skill。

## 原生安装到 Claude Code

本仓库包含 Claude Code marketplace manifest。进入 Claude Code 后执行：

```text
/plugin marketplace add EinheitCxy/skill-hub
/plugin install skill-hub@skill-hub
```

也可以在终端用一条非交互命令完成同样的操作：

```bash
claude plugin marketplace add EinheitCxy/skill-hub && claude plugin install skill-hub@skill-hub --scope user
```

安装后可用 `/reload-plugins` 重新加载；skill 会以 `skill-hub:<skill-name>` 形式命名空间化。

仓库当前为 private，执行者需要拥有 `EinheitCxy/skill-hub` 的 GitHub 读取权限并配置 GitHub credentials。若公开分发，将仓库改为 public 后，上述命令即可供无仓库权限的用户使用。

## 跨 Agent 兼容安装

如果希望用统一 CLI 同时安装到多个 agent，也可以使用 [Vercel `skills` CLI](https://github.com/vercel-labs/skills)：

```bash
npx skills add EinheitCxy/skill-hub --skill '*' -a codex -a claude-code -g --copy -y
```

## 当前 skills

这些 skill 按“学习、项目审计、证据综述、研究方向决策”组织，目录名就是可调用的 skill 名称。

### `learning-loop-coach`

把“随机提问”转化为结构化学习闭环：

- 学习阶梯和 20 小时核心计划
- 最多五个高价值资源及使用顺序
- 一题一问的主动回忆测验
- Feynman 解释反馈循环
- 单页速查表和复习安排
- 基于证据的“暂时掌握 / 待补强”状态

Skill 入口：[`skills/learning-loop-coach/SKILL.md`](skills/learning-loop-coach/SKILL.md)

### `audit-ml-project`

审计 ML 项目真实运行方式，输出基于文件、命令、配置、checkpoint、评估器、日志和打包规则的可核验操作手册。

Skill 入口：[`skills/audit-ml-project/SKILL.md`](skills/audit-ml-project/SKILL.md)

### `build-evidence-review`

从明确的 Zotero、文件夹、文献表、代码或学术检索语料构建可审计的证据型综述，覆盖证据卡片、引用、条件化比较和渲染验证。

Skill 入口：[`skills/build-evidence-review/SKILL.md`](skills/build-evidence-review/SKILL.md)

### `narrow-research-direction`

把分散的研究想法收敛为一个有证据支撑的主 thesis、最多一个 fallback，并配套重叠审计和可证伪的 go/no-go gates。

Skill 入口：[`skills/narrow-research-direction/SKILL.md`](skills/narrow-research-direction/SKILL.md)

## 验证

使用官方 skill validator 检查 frontmatter 和命名约束：

```bash
uv run --with pyyaml python /Users/ahs/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/learning-loop-coach
```

Claude Code marketplace manifest 可在仓库根目录验证：

```bash
claude plugin validate .
```
