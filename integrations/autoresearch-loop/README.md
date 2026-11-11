# EnvOpt-inspired Runtime Navigation

这是给独立安装的 `autoresearch-loop` 的可移植补丁，不改变 Skill Hub 的独立
skills 分发方式，也不会把整套 controller 打入 ChatGPT 插件。

补丁对应 loop 的 ArtifactStore / Task Context Pack 接口。源码与测试维护在
[controllable-autoresearch-agent](https://github.com/EinheitCxy/controllable-autoresearch-agent/tree/main/my_skills/autoresearch-loop)。
本目录保存配套集成说明和可移植补丁；更新源码不会自动更新已安装副本或远程插件。

## 功能与使用

- `runtime-index-build --root PROJECT --spec SPEC.json [--parent-id INDEX]`：
  将审计确认的运行入口、命令模板、固定产物关系及其源文件注册为不可变快照。
- `runtime-context --root PROJECT --index-id INDEX --query QUERY`：
  按当前任务筛选条目，默认最多 5 条、6000 个序列化字符，并返回应合入任务的
  `input_artifact_ids`。Coordinator 负责调用，worker 先读筛选结果再按需读文件。
- 筛选与 `task-open` 都检查所选源文件的新鲜度；缺失依赖、可变 context、超限
  context 会被拒绝。索引更新不改写已开始任务。
- 显式重建或筛选可为失效的生成快照创建子版本，保留原来的不可用或损坏记录。
- 命令模板只作为数据。执行仍走现有 RunSpec、权限、预算和 evaluator。

完整 JSON 示例、内部调用步骤和配对成本评测方法见
[Runtime Navigation](https://github.com/EinheitCxy/controllable-autoresearch-agent/blob/main/my_skills/autoresearch-loop/references/runtime-navigation.md)。

## 应用补丁

将 `envopt.patch` 的绝对路径交给 `git apply`。在目标 loop 目录（上游通常为
`my_skills/autoresearch-loop`，本机安装通常为 `~/.codex/skills/autoresearch-loop`）中，
先运行 `git apply --check /absolute/path/to/envopt.patch`，通过后再运行
`git apply /absolute/path/to/envopt.patch`。这些命令也支持没有 `.git` 的安装目录。
若检查失败，先核对目标版本，保留差异并人工适配；不要强制覆盖。

补丁只修改 controller 相关的 9 个文件，使用现有 runtime dependency，不增加模型
调用或新 Python runtime 依赖。安装后新会话应重新读取更新的 `SKILL.md`。

## 验证

在包含原有 8 个兄弟 worker skills 的目录布局中，于 loop 根目录执行：

```bash
uv run --with pytest --with 'pyyaml>=6,<7' python -m pytest tests/test_runtime_navigation.py -q
uv run --with pytest --with 'pyyaml>=6,<7' python -m pytest tests -q
```

新增测试验证索引复用与版本、条目与字符边界、命令参数保留、相关输入、源文件变化、
快照失效恢复、不可变任务上下文，以及 CLI 到 task-open 的连接。

本机原版本已单独复现
`test_restart_recovery_is_idempotent_at_every_durable_phase[pid-persisted]`
在 `os.killpg` 上的 `PermissionError`；该 executor 行为不在本次变更范围内。
本次新增 40 项测试通过；全套测试 589 项通过，另有上述原版本同样存在的失败。

这项实现不等于完整自动 EnvOpt optimizer，fixture 测试也不证明实际 token/cost
收益。通过相同 model/harness 的配对任务及独立任务验证后，才能报告成本改善。
