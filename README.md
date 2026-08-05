# Skill Hub

可复用的 Codex skills 集合。

## 当前 skill

### `learning-loop-coach`

把“随机提问”转化为结构化学习闭环：

- 学习阶梯和 20 小时核心计划
- 最多五个高价值资源及使用顺序
- 一题一问的主动回忆测验
- Feynman 解释反馈循环
- 单页速查表和复习安排
- 基于证据的“暂时掌握 / 待补强”状态

Skill 入口：[`learning-loop-coach/SKILL.md`](learning-loop-coach/SKILL.md)

## 验证

使用官方 skill validator 检查 frontmatter 和命名约束：

```bash
uv run --with pyyaml python /Users/ahs/.codex/skills/.system/skill-creator/scripts/quick_validate.py learning-loop-coach
```
