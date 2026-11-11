# Autoresearch Loop（自动研究循环）

`autoresearch-loop` 是一个可控的 ML 研究协调者。它将一个想法、论文或应用 ML 目标，
贯穿文献调研、基线复现、候选分支、有界执行、独立评估、迭代与经过引文验证的综合。

持久的项目本地 `.research/` 目录是事实、权限、预算、证据与恢复的权威来源。Codex 是
第一个协调者；一段冗长的对话永远不是系统状态。

## 使用

请 Codex 使用 `$autoresearch-loop` 来启动、恢复、检视或治理一个研究项目。协调者会
检视仓库、创建或校验 `.research/`，并请用户批准：

- **变更范围**（内部：`Modification Envelope`）：可编辑与只读路径、命令、依赖、
  模型、网络访问与外部动作；以及
- **资源预算**（内部：`Budget Envelope`）：wall/CPU/GPU 时间、存储、候选、重试、
  调试工作与并行子智能体。

Provider 的 token 计数与美元成本并非资源预算维度。当 provider 暴露它们时，控制器可
作为可选的审计元数据保留；缺失的值从不阻塞 `task-complete`，也绝不被凭空捏造的零
替代。

### 通俗名称

控制器在括号中保留稳定的机器名，使既有项目仍能加载；而工作流使用描述其用途的名称：

| 面向用户的名称 | 内部字段或接口 | 含义 |
|---|---|---|
| 变更范围 | `Modification Envelope` | 允许哪些文件、命令、依赖、模型与外部动作 |
| 资源预算 | `Budget Envelope` | 项目级的时间、算力、存储与候选上限 |
| 评估契约 | `EvaluationSpec` | 锁定的数据、指标、种子、样本、回归规则与评估器 |
| 研究计划 | `research-charter` / Research Charter | 问题、范围、成功标准与约束 |
| 证据注册表 | `ArtifactStore` | 不可变工件、哈希与谱系 |
| 任务上下文包 | `Context Manifest` | 一个工作技能可读取的有界文件与证据 |
| 任务预算分配 | `Budget Slice` | 为单个任务预留的有限份额 |
| 实验运行契约 | `RunSpec` | 单次运行的精确命令、环境、输入与输出 |
| 并行工作进程上限 | `loop-subagent-bound` | 同时子智能体任务的最大数 |
| 候选审查中 | `Provisional Best` | 已通过门禁但仍需复现或稳健性证据的候选 |
| 已验证最佳候选 | `Current Best` | 有资格作为本 cycle 比较点的候选 |

在两项限制均被记录之前，执行无法开始。此后对范围、预算、评估契约、模型族、敏感数据、
破坏性动作或发布的更改，都会暂停以等待另一次人工门禁。

并非每个 `gate_after` 都暂停用户。此类阶段声明
`gate_authority: coordinator | user`：协调者门禁校验锁定的契约与阶段工件，追加一条
绑定阶段/版本/图/工件哈希的回执，并继续。用户门禁留给真正的边界：首次或扩展的限制、
锁定的评估契约或图/范围变更、依赖/模型/网络、敏感或不可逆动作、相互冲突的证据，或
发布。缺失的 provider 用量并非完成或结算门禁。

`needs_user` 是最后手段，保留给真正只能由用户决定的事项（发布、首次或扩展批准、敏感
或不可逆动作，或需要人工判断的冲突证据）。协调者自行解决可恢复的状态：派发一个子
智能体去修复或调试某条记录、请求一次常规的协调者门禁、剥离未知用量，或推理后继续。
协调者只调度、接受并记录门禁；所有执行都交给有界的工作技能，绝不交给协调者亲自运行
的实验或诊断。自校验限制为每阶段一次 preflight 与一次 postflight；一个新的环境就绪
任务会取代同一目标的陈旧 `needs_user` 任务，而非阻塞其上。

默认的 `research-charter` 控制面阶段是一个协调者门禁，在其 Charter 工件校验通过后
直接继续到文献调研；执行仍需两项已批准的 envelope。

用户无需手工调用 `researchctl`。它是协调者内部用于确定性状态变更的传输通道。

## 从 Skill Hub 安装

可选的 [Runtime Navigation](references/runtime-navigation.md) 将已核对的运行入口、
命令模板和固定产物关系整理成项目索引。协调者通过 `runtime-index-build` 注册索引，
通过 `runtime-context` 筛选当前任务所需条目，并把返回的 artifact IDs 合入任务输入。
默认最多 5 条、6000 个序列化字符；筛选和打开任务时检查所选源文件是否变化。
旧任务保留原快照。命令模板不会被执行，真实 agent 成本收益仍需配对任务验证。

安装 Skill Hub 的 `skills/` 集合，其中包含 Coordinator、8 个内部 worker 和 `literature-review`。`$autoresearch-loop` 是面向用户的协调者；其 `skills/` 下的八个内部目录是它在
内部加载的工作技能。

### Codex

```bash
git clone https://github.com/EinheitCxy/skill-hub.git
cd skill-hub
mkdir -p ~/.codex/skills
cp -R skills/* ~/.codex/skills/
mkdir -p ~/.codex/agents
cp skills/autoresearch-loop/agents/research-*.toml ~/.codex/agents/
```

重启 Codex，使其发现协调者与工作技能。

### Claude Code

先安装并认证 Claude Code，然后运行：

```bash
git clone https://github.com/EinheitCxy/skill-hub.git
cd skill-hub
mkdir -p ~/.claude/skills
cp -R skills/* ~/.claude/skills/
```

开启一个新的 Claude Code 会话，使其发现协调者与工作技能。

请安装真实目录而非符号链接：任务提示词哈希会刻意拒绝包含 symlink 组件的
`SKILL.md` 路径。

然后从将拥有研究状态的 ML 项目开始：

```text
Use $autoresearch-loop to turn this idea or paper into a controlled research
project. Inspect the repository first, propose the Change Scope and Resource
Budget, and wait for my approval before execution.
```

## 内嵌的 Planner ScholarAPI

ScholarAPI 被打包在主协调者内部，路径为
`autoresearch-loop/skills/planner/skills/scholarapi`；它不是第十个顶层
`skills` 能力，也不是用户本地的 agent 设置。协调者在每次
`research-literature-review` 任务之前，以内嵌的 Planner 执行一次有界的 ScholarAPI
preflight。仅当变更范围允许网络访问、存在 `SCHOLARAPI_API_KEY`，且计划声明了有限的
查询、页数与结果上限时，Planner 才选择 ScholarAPI。否则它记录 `not_selected`，
文献工作技能只使用已批准的备用来源；这并非一个用户门禁。

Planner 在任何情况下都登记一个 `planner-decision` 工件。当它选择 ScholarAPI 时，
还会在把 ID 交给工作技能的任务上下文包之前，登记一个不可变的 `literature-metadata`
工件。该元数据是不可信的候选材料；文献工作技能仍需从一手来源验证录用状态、出版状态、
DOI 与科学声明。

### 安全地提供 `SCHOLARAPI_API_KEY`

使用仅会话有效的环境变量。在将启动 Codex 或 Claude Code 的 shell 中，输入密钥但不
显示、不写入 shell 历史，然后从同一 shell 启动一个**新**的 agent 会话：

```bash
read -rs SCHOLARAPI_API_KEY
export SCHOLARAPI_API_KEY
codex
```

对于 Claude Code，把最后一行换成 `claude`。子进程继承该密钥；既有的
Codex/Claude Code 会话不会继承，因此设置后需重启它们。当父 shell 不再需要该密钥时，
移除它：

```bash
unset SCHOLARAPI_API_KEY
```

切勿把密钥放入命令参数、`.env` 文件、shell 启动文件、`research.yaml`、`.research/`、
工件、报告、任务 brief 或 Git 提交。保留的溯源包含查询/过滤/分页元数据、检索时间、
ID、DOI/来源 URL 与结果计数--绝不包含密钥。若密钥缺失，Planner 记录
`not_selected` 并只使用其他已批准来源；它不会发起网络请求，也不会要求你把密钥粘贴
到对话中。

### ScholarAPI 请求规则

preflight 使用打包在
`skills/planner/skills/scholarapi/scholarapi.py` 的官方 ScholarAPI v1 只读封装。它
遵循以下规则：

- `GET /search` 是按相关性排序的候选发现的默认方式；
- `GET /list` 仅在已批准的计划显式请求有限索引顺序枚举、穷尽扫描或监控切片时才被
  允许；
- 每个请求都声明至少一个查询与有限的页/结果上限；`--limit` 被校验到 `1..1000`；
- `/search` 用作为 `--cursor` 回传的 `next_cursor` 继续，而 `/list` 用作为
  `--indexed-after` 回传的 `next_indexed_after` 继续；
- 封装只以 `X-API-Key` 头发送密钥，在可用时暴露 HTTP 状态与 `X-Request-Id`，且从不
  打印无界的错误正文或密钥。

该封装通常由 Planner 而非工作技能调用。如需本地冒烟检查，使用打包脚本与一个仅会话
有效的密钥：

```bash
uv run --no-project python \
  skills/planner/skills/scholarapi/scholarapi.py search \
  --q "contrastive unpaired image translation" --limit 10
```

该命令必须运行在一个已具备 `SCHOLARAPI_API_KEY` 的进程中；切勿把密钥放在命令行。
封装返回带请求元数据、检索时间、记录、来源 ID/DOI/URL、可用性标志与分页状态的有界
JSON。Planner 把该原样响应存为 `literature-metadata` 工件。文献工作技能只能将其
用作候选线索清单，且在创建证据卡片或引文背书的声明前必须重新打开一手来源。校验、
传输、HTTP、畸形 JSON 与上限错误会停止 ScholarAPI 选择；它们不会触发无界重试，也
不会悄悄放宽已批准的来源策略。

## 架构

系统保留六个稳定接口：

| 接口 | 归属 |
|---|---|
| 研究状态快照（`ResearchState`） | 带版本的控制快照；由协调者写入 |
| 证据注册表（`ArtifactStore`） | 不可变证据、哈希、谱系与可用性 |
| 任务与阶段工作流（`Task/Stage Protocol`） | 数据定义的阶段、有界任务、报告与转移 |
| 实验运行器（`EnvironmentExecutor`） | 已批准的依赖准备与进程执行 |
| 独立评估器（`Evaluator`） | 独立的锁定评估与咨询性裁决 |
| 候选搜索策略（`SearchPolicy`） | 候选多样性、分支、Best 规则与停止决策 |

阶段 ID 与转移存放在 `research.yaml`，而非 Python 枚举或 runner。候选搜索策略不执行
实验。实验运行器不启动 LLM。评估器不能修复候选或变更 Best。在预算预留之前，除非某
任务的阶段处于活动、其主技能与能力技能由图声明、所有所需输入工件类型均已校验、且
该阶段允许再增一个并行任务，否则任务开启失败。初始化、`validate` 与图修订请求/应用
都会对每个声明的主技能与能力 `SKILL.md` 做 preflight；一个缺失或冲突的技能会在
`.research/` 创建之前、或某后续阶段变得不可达之前中止。

## 持久项目布局

```text
.research/
├── research.yaml
├── state.json
├── budget.json
├── budget-migrations.jsonl
├── reservation-detachments.jsonl
├── gates.jsonl
├── journal.jsonl
├── worker-events.jsonl
├── autoresearch-record.jsonl
├── artifacts/
│   ├── index.jsonl
│   └── aliases.jsonl
├── cycles/
│   └── <cycle-id>/
│       ├── evaluation-comparison.json
│       └── tasks/
│           └── <task-id>/
│               ├── brief.md
│               ├── task.json
│               └── report.md
├── runs/
└── checkpoints/
```

状态与事务文件由控制器管理。JSONL 账本仅追加。登记的快照与已完成的任务报告不可变；
更正会创建一个新的工件版本。

## 协调者生命周期

1. 校验仓库指令与当前 `.research/` 状态。
2. 记录研究计划与两项用户批准的限制。
3. 搜索并筛选文献；优先选择直接相关、且通过官方会议论文集、出版商页面或 DOI 记录
   验证状态的已录用论文。
4. 可选地分析所提供的论文，随后审计数据集身份、划分与泄漏。
5. 登记基线候选，锁定其评估与运行计划，校验环境，并复现初始基线（Initial Baseline）。
6. 生成可证伪的 exploit、explore 与 falsify 候选。
7. 在评估契约中锁定项目特定的指标、数据、划分、种子、样本、回归、评估器哈希与
   稳健性。
8. 将本 cycle 具体的已验证最佳候选绑定到一个不可变的比较检查点。
9. 实现候选、独立评估已登记的证据、质疑混淆因素，并让协调者选择下一个图分支。
10. 调试、精炼、消融、复现、转向、晋升或停止。
11. 对每次转移做检查点，并将一份经过引文验证的声明-证据报告归档到发布门禁之后。

默认生命周期是 `ml-applied` 图中的数据。项目可通过一次已批准的图修订更改阶段，而
无需更改协调者代码。

### 安全的图修订

切勿就地编辑活动的 `.research/research.yaml`。协调者先请求一个绑定当前图哈希、提议
图哈希、状态版本与原因的 `graph` 门禁；在用户记录批准后，它将图作为一次可恢复事务
应用。该事务拒绝处于开放状态的任务与会移除当前阶段的提案，写入一个源检查点，将
ResearchState 推进一个版本，并在一条回执中记录前后两个图哈希。

若被中断，协调者用该门禁 ID 恢复 `graph-revise`；持久事务已包含提议的图文本，因此
它不依赖原始 YAML 文件仍可用。`validate` 与 `status` 都在报告健康前校验同一图/
状态/检查点锚点。

## 工作技能

每个图阶段声明 `task_owner: coordinator|subagent`。协调者自有任务记录
`subagent_model: null`，从不接收工作事件，也从不占用 `loop-subagent-bound` 槽位。
子智能体拥有的任务获得一份任务本地 `brief.md`、一个主技能、至多两个能力技能、已校验
的任务上下文包工件、一份有限的任务预算分配、互不重叠的可编辑路径与一份 `report.md`。

工作技能是单层叶子子智能体，不能派生子智能体或变更全局状态。

只有标记 `control_plane: true` 的协调者阶段可在变更与预算执行批准之前开启。默认为
`false`；其余每个协调者或子智能体任务都需要已批准的执行 envelope 并获得一份预算
预留。打包的图只把 `intake` 与 `research-charter` 标记为控制面阶段。控制面协调者
任务即使在执行批准之后也从不获得预算预留，并以无 provider 模型用量完成
（`cost_usd` 与 `tokens` 为 `null`，`duration_ms` 为 `0`）。

对子智能体拥有的任务，不可变的 `work_type` 直接指定模型：`simple -> luna`、
`planning -> sol`、`thinking -> terra`、`execution -> terra`。这些值是分层别名
（`luna`=haiku、`sol`=sonnet、`terra`=opus）；`thinking` 与 `execution` 共享 opus 分层。
该选择出现在任务本地 `brief.md`
中，并绑定进提示词哈希。

### 子智能体 Persona

八个内部 worker persona 以薄壳形式随仓库发布在
`skills/autoresearch-loop/agents/`（分层与职责见下表）。每个 persona
按任务 brief 的 Primary Skill File 读取内部 worker `SKILL.md`（唯一真相源），继承全部子智能体工具，仅禁用 `Agent` 以保证
worker 为单层叶子智能体。

Skill Hub 的 Claude Code 插件已在 manifest 声明八个 persona；手动安装时可复制：

```bash
mkdir -p ~/.claude/agents
cp skills/autoresearch-loop/agents/research-*.md ~/.claude/agents/
```

Coordinator 在 `researchctl task-open` 之后主动通过 Agent 工具派发每个 persona
（`subagent_type=research-<role>`、`model` 取自 work_type 别名、`prompt` = 任务
`brief.md`）。插件模式下使用宿主暴露的 `skill-hub:research-<role>` 名称，不依赖
仓库本地的 Claude 设置。worker 通过 `disallowedTools: Agent` 保持单层叶子；persona
修改作用于随后打开的任务。

| Persona | Model | 职责 |
|---|---|---|
| `research-literature-review` | haiku | 搜索、筛选、排除并验证先验工作 |
| `research-paper-analysis` | sonnet | 将单一来源映射为声明、方法、局限与复现工作 |
| `research-hypothesis-generation` | opus | 提出可证伪的 exploit、explore 与 falsify 节点 |
| `research-experiment-design` | sonnet | 锁定数据、对照、指标、门禁与预算 |
| `research-experiment-execution` | opus | 实现并运行一个已批准的候选 |
| `research-experiment-evaluation` | sonnet | 独立评估已登记的证据 |
| `research-evidence-critique` | opus | 质疑机制、泄漏、基线与混淆因素 |
| `research-result-synthesis` | opus | 构建经过引文验证的声明-证据报告 |

并行扇出受限于可用独立任务、图限制、硬件与已批准的并行工作进程上限。图与已批准的
资源预算是相互独立的上限；实际扇出取两者中较小值。`ml-applied` 图上限为 8；初始
预算提案为 4。

一个处于开放状态的直接工作任务预留一个 `loop-subagent-bound` 槽位。候选的预算切片
与本地实验运行从不预留该槽位；它们只消耗各自的候选与执行资源。

完成之前，声明的阶段输出是独立登记并已校验的 ArtifactStore 记录。只登记影响结论、
运行日志、输出与报告的输入；不要把每个计划、元数据桩或回执都登记为证据。
`report.md` 引用这些 ID；完成时仅快照不可变的 `task-report`。下一阶段消费的是真实
的 ArtifactStore ID（如 `research-request` 或 `evaluation-reports`），而非同一份
Markdown 报告的类型别名。

工件注册对相同内容哈希与完整谱系身份（含类型、生产者、任务/候选、提示词、标签与
父项）是幂等的。重放返回既有的规范工件 ID；不同的谱系仍创建一条新记录。历史索引行
从不删除。相同的公共控制快照 likewise 在检查点创建时被复用；转移与图修订检查点仍
分别绑定其各自的恢复原因。

每个生成的任务 brief 包含精确的报告 frontmatter 契约。工作技能的初始报告使用其
绑定的 `task_id`、`candidate_id` 与 `prompt_hash`，但保留 `artifact_ids: []`：输入
工件 ID 只属于任务 Spec，绝不能出现在报告 frontmatter 中。工作技能返回后，协调者
登记并校验声明的输出，然后在 `task-complete` 之前用这些 ID 调用
`researchctl task-report-finalize`。该锁定的原子化终结操作校验任务绑定的输出 ID、
覆盖声明输出、仅语义性地修改那个 frontmatter 字段，并返回前后报告哈希；协调者在此
交接期间不得改动工作技能的报告正文。在请求任何绑定报告哈希的结算门禁之前先做终结。

完成会拒绝其引用工件类型未覆盖每个声明 `stage.outputs` 类型的报告。

七个必备报告分节保持有序且完整。任务完成前，一份报告可在 `## Artifact Links` 之后
追加 `## Decision Addendum - YYYY-MM-DD`，以记录后续的用户决定或证据更正。日期必须
有效，任意标题仍被拒绝，附录必须引用已登记的证据，而非充当门禁或工件别名。

## 上下文与模型调用记录

工作技能启动钩子只能加载任务的 `brief.md`、具名技能与任务上下文包工件。完整聊天
历史、完整账本、无关报告与未登记的原始文件都是被禁止的提示词输入。

每次模型调用向以下文件追加一条紧凑记录：

```text
.research/autoresearch-record.jsonl
```

该行记录调用 ID、时间、用量、cycle/任务/候选、技能、模型、时长、provider 报告的
token 与成本、提示词哈希、报告路径与状态。它从不存储完整提示词、完整响应或思维链。
完整输出与想法保留在任务本地 Markdown 与登记的工件中。

Provider token 与成本只是可选的审计元数据。一个任务没有它们也能完成；一个预留任务
依据其实测模型调用时长结算其 wall-time 配额。控制器从不凭空捏造缺失的 provider
用量。

工作技能生命周期刻意与模型用量分离。工作技能启动前，协调者向
`.research/worker-events.jsonl` 追加 `dispatched`；返回时追加 `report-ready` 或
`terminal`。恢复时，`worker-status` 在其投影之前先做对账，对
`report-ready`/`terminal` 的工作技能，协调者直接继续到完成/升级，而非反复等待。
终态投影只保留其简明状态与原因。账本不含完整报告、token、成本、预算或状态副本。

## 候选与晋升规则

候选图保留候选种类、父项、机制、主要变更、预期观测、证伪条件、评估契约、预算、工件、
科学深度与调试深度。

一次已批准的评估门禁被记录为 split v1 envelope，把阶段证据（`stage_gate`）绑定到
静态评估契约载荷哈希（`evaluation_spec`）；阶段证据与契约哈希不能彼此独立漂移。
遗留的直接评估门禁仅为恢复而保持可读。

在三个槽位下，候选搜索策略选择：

1. exploit 已验证最佳候选；
2. explore 一个实质不同的机制；以及
3. falsify 最强声明或执行其决定性消融。

初始基线是不可变的起点。每个 cycle 检查点化具体的已验证最佳候选。新候选对照该
检查点比较，而非对照候选审查中。

一个官方候选只有在有序硬门禁、项目最低增益与不可接受回归规则，以及精确 cycle 的
Pareto 成员资格全部通过之后，才进入候选审查中状态。已验证最佳候选只有在所需的
复现、多种子或稳健性证据通过之后，才获得该精确报告/检查点指针。

本技能中没有任何通用指标或阈值被硬编码。

## 建议的默认预算

初始化只提议但不批准：

```yaml
wall_seconds: 86400
cpu_seconds: 86400
gpu_seconds: 86400
storage_bytes: 2147483648
official_candidates: 3
retries: 3
debug_candidates: 3
loop-subagent-bound: 4
gpu_type: null
```

具体的任务/运行预留与超时保持有限。模型权重不计入收费存储；普通运行快照经哈希校验
后只收费一次。

## 协调者内部命令

这些命令记录 Codex 使用的确定性传输通道。它们并非面向用户的工作流。

```bash
uv run skills/autoresearch-loop/scripts/researchctl.py init --root /path/to/project --profile skills/autoresearch-loop/assets/research-project/research.yaml
uv run skills/autoresearch-loop/scripts/researchctl.py validate --root /path/to/project
uv run skills/autoresearch-loop/scripts/researchctl.py status --root /path/to/project
uv run skills/autoresearch-loop/scripts/researchctl.py gate-reject --root /path/to/project --gate-id gate-<id> --source User --reason "reason"
uv run skills/autoresearch-loop/scripts/researchctl.py gate-cancel --root /path/to/project --gate-id gate-<id> --source User --reason "reason"
uv run skills/autoresearch-loop/scripts/researchctl.py graph-revision-request --root /path/to/project --graph /path/to/proposed-research.yaml --reason "explain the approved change"
uv run skills/autoresearch-loop/scripts/researchctl.py graph-revise --root /path/to/project --graph /path/to/proposed-research.yaml --gate-id gate-<approved-id>
uv run skills/autoresearch-loop/scripts/researchctl.py worker-reconcile --root /path/to/project
uv run skills/autoresearch-loop/scripts/researchctl.py worker-status --root /path/to/project
uv run skills/autoresearch-loop/scripts/researchctl.py reservation-detach --root /path/to/project --reservation-id <id> --reason "provider usage unknown"
uv run skills/autoresearch-loop/scripts/researchctl.py intent-inspect --root /path/to/project
uv run skills/autoresearch-loop/scripts/researchctl.py intent-retry --root /path/to/project --intent-id <id>
uv run skills/autoresearch-loop/scripts/researchctl.py intent-quarantine --root /path/to/project --intent-id <id> --reason "operator triage"
uv run skills/autoresearch-loop/scripts/researchctl.py task-report-finalize --root /path/to/project --task-id <task-id> --artifact-id artifact-<registered-output-id>
uv run skills/autoresearch-loop/scripts/researchctl.py artifact-alias-bind --root /path/to/project --type <logical-output-type> --artifact-id artifact-<id>
uv run skills/autoresearch-loop/scripts/researchctl.py artifact-alias-resolve --root /path/to/project --type <logical-output-type>
```

该脚本使用 PEP 723，以 `pyyaml>=6,<7` 作为唯一的第三方运行时依赖。控制器还暴露
内部的门禁、预算、任务、工件、候选、评估、晋升、环境、运行、工作事件与恢复操作。
Codex 调用这些命令；它们并非必需的用户工作流。在被中断的 `graph-revise` 之后，同一
命令可省略 `--graph`：此时持久图修订意图即为恢复权威。

`validate` 也是显式的遗留预算迁移边界。识别出的 v0 或早期 v1 的
`cost_usd`/`model_tokens` 账本会被一次性迁移到仅 wall/resource 的 v1，原始值保留在
`budget-migrations.jsonl` 中；该迁移幂等，绝不因新任务而再次触发，未知版本 fail
closed。未知的 provider 用量必须使用 `reservation-detach`，而非零结算；一个带有
待处理或已提交预算结算的预留，在该结算被隔离或结算之前不能被剥离。GPU 请求需要
主机端 NVIDIA 设备、驱动与遥测就绪；沙箱执行报告 `sandbox-no-device`。用量未知的
GPU 运行在 `run-collect` 时剥离其预留，而非登记一个无法结算的运行所不能推进的
输出。

`reservation-detach` 与 `intent-*` 恢复命令绕过完整 preflight（它们存在的目的正是
修复会被 preflight 拒绝的状态）；轻量的追加命令（`record-call`、`worker-event`、
`artifact-alias-bind`）也跳过它，以免常规追加阻塞于无关的待处理恢复。

`--root` 可指项目根或其终端 `.research` 目录。逻辑输出别名存放在
`artifacts/aliases.jsonl`；它们指向既有工件 ID，从不复制工件内容。

## 测试

测试遵循 `references/testing-strategy.md`：

- **聚焦任务**：变更后只运行所拥有的契约节点；
- **阶段检查点**：只运行一次小型集成主干；以及
- **发布全集**：仅在最终验收或发布前运行完整套件。

聚焦的协调者检查：

```bash
PYTHONDONTWRITEBYTECODE=1 UV_CACHE_DIR=/tmp/autoresearch-uv-cache UV_PYTHON=3.11 uv run --offline --with pytest --with "pyyaml>=6,<7" pytest skills/autoresearch-loop/tests/test_coordinator_skill.py -q
```

阶段 3 集成主干：

```bash
PYTHONDONTWRITEBYTECODE=1 UV_CACHE_DIR=/tmp/autoresearch-uv-cache UV_PYTHON=3.11 uv run --offline --with pytest --with "pyyaml>=6,<7" pytest skills/autoresearch-loop/tests/test_coordinator_skill.py skills/autoresearch-loop/tests/test_skill_suite.py skills/autoresearch-loop/tests/test_module_architecture.py -q
```

发布校验于 2026-08-06 执行：

```bash
PYTHONDONTWRITEBYTECODE=1 UV_CACHE_DIR=/tmp/autoresearch-uv-cache UV_PYTHON=3.11 uv run --with pytest --with "pyyaml>=6,<7" pytest -p no:cacheprovider skills/autoresearch-loop/tests -q
```

结果：`541 passed in 163.69s`。

## 直接参考

`SKILL.md` 一层深度地链接到：

- `references/research-state.md`
- `references/artifact-store.md`
- `references/task-stage-protocol.md`
- `references/environment-executor.md`
- `references/evaluator.md`
- `references/search-policy.md`
- `references/research-standards.md`
- `references/prompt-contracts.md`
- `references/testing-strategy.md`

## 当前局限

- 执行仅支持本地执行器：`uv-local` 与 `command-local`。
- 本地适配器记录网络策略，但不声称具备 OS 级隔离。
- 门禁回执记录 approve、reject 与 cancel 决定及其来源与原因，但不提供密码学用户
  身份。
- 控制器不启动 LLM；Codex 通过任务本地上下文包执行协调者/子智能体编排。
- idea-to-promotion 装置以合成的评估与复现工件证明控制器行为；它不确立科学质量或
  真实模型性能。
- 发布与外部副作用始终需要用户的显式门禁。

## 设计溯源

本设计有选择地借鉴了 [AutoResearchClaw](https://github.com/aiming-lab/AutoResearchClaw)
的工件/检查点/HITL/引文机制，以及
[AI-Scientist-v2](https://github.com/SakanaAI/AI-Scientist-v2) 的候选节点/账本/
分支/调试/并行搜索机制。
这两个项目都没有被作为固定或整体的研究框架采用。

## 包内能力池

默认流程只依赖本仓库的九个 primary skills 和 `literature-review`。旧安装中的泛用
辅助 skills 不再成为初始化前提。worker 原有的方法、统计、引文和证据契约保持完整。
只有能力 skill 确实已安装、项目图变更已批准时才添加；不静默替换缺失能力。
