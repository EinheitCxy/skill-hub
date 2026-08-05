# Skill Hub

可复用的 Codex skills 集合。

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

Skill 入口：[`learning-loop-coach/SKILL.md`](learning-loop-coach/SKILL.md)

### `audit-ml-project`

审计 ML 项目真实运行方式，输出基于文件、命令、配置、checkpoint、评估器、日志和打包规则的可核验操作手册。

Skill 入口：[`audit-ml-project/SKILL.md`](audit-ml-project/SKILL.md)

### `build-evidence-review`

从明确的 Zotero、文件夹、文献表、代码或学术检索语料构建可审计的证据型综述，覆盖证据卡片、引用、条件化比较和渲染验证。

Skill 入口：[`build-evidence-review/SKILL.md`](build-evidence-review/SKILL.md)

### `narrow-research-direction`

把分散的研究想法收敛为一个有证据支撑的主 thesis、最多一个 fallback，并配套重叠审计和可证伪的 go/no-go gates。

Skill 入口：[`narrow-research-direction/SKILL.md`](narrow-research-direction/SKILL.md)

## 验证

使用官方 skill validator 检查 frontmatter 和命名约束：

```bash
uv run --with pyyaml python /Users/ahs/.codex/skills/.system/skill-creator/scripts/quick_validate.py learning-loop-coach
```
