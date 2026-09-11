# 社区来源与定位

检索核对日期：2026-09-11。下列为设计参考，未复制上游技能实现；未做上游安装或长任务效果评测，不以搜索排序或热度证明可靠性。

| 来源 | 已核对能力 | 本技能的取舍 |
| --- | --- | --- |
| [OthmanAdi/planning-with-files](https://github.com/OthmanAdi/planning-with-files/blob/master/skills/planning-with-files/SKILL.md) | task_plan.md、findings.md、progress.md 分开保存计划、发现和进度；有宿主 hooks | 复用已有记录，默认只保留状态页和事件日志；不引入 hooks |
| [osteele/research-notebook](https://github.com/osteele/research-notebook) | 连接研究问题、实验、发现和发表主张；记录运行前预测与判据 | 保留证据和判据，暂不引入论文主张台账及计算后端契约 |
| [eins78/agent-skills 的 lab-notes](https://github.com/eins78/agent-skills/blob/main/skills/lab-notes/SKILL.md) | 追加式 running log、结构化/探索式模式、实验 verdict | 保留失败及决策历史，允许探索性记录，减少启动时的问答步骤 |

相邻技能：`results-validator` 分析反常结果的原因；`work-journal` 记录工作过程及当时有证据支持的判断，不自动展开完整结果审计。`autoresearch` 在本仓库负责 ML 运行契约与 profiling，也不等同于持续工作日志。
