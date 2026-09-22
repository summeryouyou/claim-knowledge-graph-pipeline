---
title: Claim知识图谱流水线Agent执行规范（连续七轮）
tags:
  - 知识图谱
  - Agent执行规范
aliases:
  - Claim流水线核心指南
  - Claim流水线连续执行指南
---

# Claim 知识图谱流水线：Agent 连续执行规范

## 1. 角色、目标与优先级

你是本任务的流水线执行 Agent。完整阅读本文件、根目录 `AGENTS.md` 和 `Claim知识谱系设计.md` 后开始；每个 Agent 阶段还必须完整阅读当轮生成的任务规范及设计快照。

目标：从本任务原始论文、摘要和参考文献输入，生成只有 Claim 节点与 Claim 关系边的时间泳道式演化网络；保留全部核心论文的来源追溯、逐字证据、候选与判断、中途修订记录和机器可读图。允许分支、汇聚、跨主题与多级连接，不强制树或森林。不使用其他任务的科学答案。

用户只需替换 `data/` 三类输入。你负责自动识别核心集合、检查输入、完成 Agent 科学生成、生成本任务的领域配置、调用程序、审查各阶段结果并记录限制。日常任务不编辑算法或校验代码。领域词表与泳道通过配置文件适配，不通过换算法适配。

优先级：用户的任务约束 > 本文件与数据契约 > 当轮任务规范 > 规则建议。若约束有真实冲突，说明冲突并请求方向；不得偷偷扩大科学输入、补造摘要或改变关系语义。

## 2. 项目结构与正式入口

所有相对路径以本项目根目录为基准。目录应为：

```text
任务根目录/
├─ PIPELINE_AGENT.md                # 本核心指南，必须留在根目录
├─ AGENTS.md
├─ README.md
├─ 启动提示词.md
├─ Claim知识谱系设计.md
├─ 领域配置模板.json                 # 模板，不能直接当成已完成的领域配置
├─ requirements.txt
├─ pipeline.py                      # 唯一推荐的正式执行入口
├─ pipeline/
│  ├─ common.py
│  ├─ 01_normalize_inputs.py
│  ├─ 02_build_citation_backbone.py
│  ├─ 03_claim_generation.py
│  ├─ 04_claim_review.py
│  ├─ 05_generate_relation_candidates.py
│  ├─ 06_judge_relations.py
│  ├─ 07_assemble_and_render.py
│  └─ validate_round1.py … validate_round7.py
├─ data/                            # 用户提供本任务三个输入；初始可以为空
├─ tests/test_pipeline.py            # 仅用于工具维护，不是科学输入
└─ 知识图谱构建过程/                  # 执行时创建
   ├─ STATUS.md
   ├─ run_state.json
   ├─ 领域配置.json                  # 第四轮之后由本任务Agent准备
   └─ 01_规范化输入 … 07_图谱装配
```

只能调用本项目的脚本，不调用同名旧目录或其他项目的程序。使用 `python pipeline.py`，它负责阶段顺序、输入指纹、失效检测、独立校验和完成状态。内部 01–07 与校验脚本保留直接调用能力，供工具维护使用；正式任务不得绕过入口而仅凭脚本报告宣称完成。

已验证运行环境为Python 3.11；使用其他满足依赖要求的版本时先检查兼容性。确认解释器及依赖：

```powershell
python --version
python -c "import numpy, scipy, sklearn; print(numpy.__version__, scipy.__version__, sklearn.__version__)"
```

缺依赖时按运行环境权限安装 `python -m pip install -r requirements.txt`。如果 `python` 指向错误解释器，使用有依赖的解释器绝对路径，所有轮次保持一致。不要求特定机器路径，不依赖网页渲染或 Mermaid。

初始化与状态查询：

```powershell
python pipeline.py init
python pipeline.py status
```

缺输入、重复核心名单或无法读取时，初始化失败且不能开始科学生成。已有结果换了 data/ 时，入口会拒绝混用旧结果；执行 `python pipeline.py init --new-task` 可恢复地归档旧结果并开始新任务，不删除原始输入。归档绝不是新任务的科学输入。默认输出目录最稳妥；确有需要可将 `--workspace` 放在子命令前，例如 `python pipeline.py --workspace '任务结果' init`，之后所有命令一致使用它，输出必须在本项目内且不能是根目录、data、pipeline 或归档。

## 3. 输入契约：数量与范围以核心集合为准

### 3.1 文件发现与格式

`data/` 必须有且只有一个匹配 `核心文献*篇.jsonl` 的文件，例如 `核心文献n篇.jsonl` 或 `核心文献80篇.jsonl`，另有 `文献主表.jsonl`、`参考文献关系-原始.jsonl`。核心数量 N 由核心文件实际记录数确定，不从文件名数字推断，也不由主表总量推断。核心文件不能为空。

格式统一 UTF-8 JSONL：每个非空行一个 JSON 对象，不是顶层 JSON 数组，不是 Markdown，不是 JSON 对象跨多行。空行可忽略，源行号按真实文件行记录。

### 3.2 核心名单

每条至少有 `record_id`、`rank`、`title`。`record_id` 唯一；rank 可以为整数或可转成整数的字符串，但必须无重复、完整覆盖 1…N。推荐同时保持 `year`、`doi` 与主表一致。可保留作者、类别、筛选说明及其他原始字段，但 Claim 生成不得看到这些辅助信息。

```json
{"record_id":"TASK:001","rank":1,"title":"论文原始标题","year":2020,"doi":null}
```

### 3.3 文献主表

每条核心论文在主表中必须恰好有一条可连接记录；核心字段为非空 `record_id`、`title`、`abstract`、有效 `year`，另提供 `doi`、`authors`、`source_title` 等已有元数据。DOI 可为 null；作者优先提供数组，当前字符串转换只包装成数组，不可靠拆开所有复合作者格式。

```json
{"record_id":"TASK:001","title":"论文原始标题","year":2020,"doi":null,"authors":["Li, A"],"source_title":"Journal X","abstract":"来自原始输入的摘要全文"}
```

主表可以远大于核心集合。只对入选记录严格核查必需科学字段与唯一连接；外围记录可以不完整，不因外围缺摘要阻止核心处理，不将外围记录扩入图谱。核心缺摘要/缺连接或重复连接必须停止解决，不能利用背景知识补写。

### 3.4 原始参考文献

每条核心来源引用至少有 `source_record_id`、非空 `target_reference_raw`，可有 `relation`。通过 `source_record_id` 连接来源论文；参考文献的目标此时可以尚未识别。

```json
{"source_record_id":"TASK:001","relation":"cites_raw_reference","target_reference_raw":"Wang B, 2018, JOURNAL Y, V12, P34, DOI 10.1234/example"}
```

引用文件可以包含主表中大量外围论文的引用。第一轮仅保留核心来源的原始引用，记录忽略数；第二轮只识别核心目标的内部 CITES。核心论文引用了外围论文，不会因此新增外围 Paper 或 Claim 节点。核心来源没有引用可以为空，不能补造引用；来源论文仍参与 Claim 生成与非引用候选召回。

以上示例只展示字段，不是模板自带科学答案。用户格式符合契约时直接执行。若真实字段格式不符，先报告字段与连接问题；只修复输入映射/格式，不无授权改算法。不要用已有的规范化 Paper、Claim 或关系替代原始输入来假装从头运行。

## 4. 全阶段约束、检查与交接

### 4.1 七轮与科学输入边界

| 轮次 | Agent可见科学内容 | 主产物 | 严禁提前进行 |
|---|---|---|---|
| 1 | 本任务原始名单、主表与引用 | Paper、原始引用与追溯 | Claim或关系生成 |
| 2 | 第一轮数据；引用队列中的原文与候选元数据 | 隐藏CITES与解析审计 | 因引用直接断言Claim关系 |
| 3 | 当轮任务仅 paper_id、title、abstract；设计快照 | Claim草稿 | 查看年份、引用、类别、旧答案或按连边需求改Claim |
| 4 | 当轮标题、摘要、草稿与设计快照 | 最终Claim、稳定ID | 查看年份、引用、关系或跨论文合并 |
| 5 | 最终Claim、核心Paper、年份、CITES | 领域配置与多通道候选 | 把候选当成立关系 |
| 6 | 两端Claim、范围、证据、摘要、年份与召回信息 | 完整决定、正式/待复核关系 | 将主题/引用相同等同知识作用；强制连边 |
| 7 | 本任务定稿节点关系与泳道配置 | SVG、节点边、布局和结构分析 | 偷改科学内容、树化、丢弃孤立Claim |

不得读取其他项目的 Claim、关系、图谱、归档已知答案来生成本任务科学内容。不得联网或读全文补充第三、第四轮信息。第五、六轮也默认仅使用本任务提供的标题摘要与引用；若用户明确扩大科学输入，应另记录并确认这不再是原始的摘要流程。

输入边界是 Agent 行为约束及任务字段隔离，不是操作系统级保密沙箱。接手新轮时主动保持该边界；不得因为文件可访问就读取禁止内容。

### 4.2 连续执行、完成与停点

默认连续执行全部七轮：从初始化开始，直到 `知识图谱构建过程/STATUS.md` 明确显示 `completed` 后才进行最终交付。七轮仍是七个完整的质量关口，但不是七次用户授权关口。每轮都按以下闭环执行：

1. 执行当前轮程序动作并读取其状态、任务规范和设计快照；
2. 真实完成该轮要求的 Agent 阅读、判断和输出，不把程序预填内容冒充 Agent 结果；
3. 运行 `collect`、`finalize`、`validate` 或该轮规定的等价动作；
4. 检查报告、样本、数量、覆盖、语义与警告；对可在当前范围内解决的问题修订并重跑；
5. 写入 `执行记录.md`，随后立即进入下一轮，不等待用户回复“继续”。

`agent_required` 是预期的可执行状态，不是意外或停点。第二轮应继续处理引用解析队列；第三轮应完成全部 Claim 草稿批次；第四轮应完成全部独立复核批次；第六轮应读取 `review_plan`，完成定点复核、必要覆盖和确认记录。任务准备完成、常规警告、存在孤立 Claim、候选未接受或图结构不够理想也不是停点。可以向用户发送简短进度更新，但不得以更新代替继续执行。

仅当遇到无法从本项目现有输入、规范和授权范围内安全解决的真实阻断时暂停，包括：必需输入缺失或损坏；身份歧义会改变核心集合且无法判定；必须由用户决定是否扩大科学输入或改变科学范围；缺少必要权限、运行环境或依赖且无法恢复；经过合理自修复后同一强制校验仍持续失败。暂停时明确记录所在轮次、阻断证据、已完成内容、已尝试处理和需要用户决定的最小问题。不得把可以修复的格式错误、预期的 Agent 队列或单纯不理想的科学结果包装成阻断。

`prepare` 只是准备任务。必须实际完成所有任务输出，才能 `collect/finalize`。第三、第四轮不能跳批、遗漏论文，不能未经阅读列复核清单。第六轮规则初判不能代替 Agent 定点复核。

检查分为：格式/身份/哈希/追溯的一致性；摘要对命题的语义支持；连线的知识作用；图形可读性。程序主要验证第一类及图结构，不提供科学正确性保证。不把规则 confidence 当校准概率，不把 completed 当人工金标准。

### 4.3 每轮落盘要求

程序主产物、任务、Agent 输出、报告与样本都保留。你另在当轮目录保存 `执行记录.md`，至少包含：输入范围和哈希、解释器与依赖、Agent/模型信息（不可知则写未知）、实际任务完成范围、执行命令与返回结果、规则/领域配置、语义抽查对象和结论、修订及剩余限制、下一步。

人工记录使用 Markdown，不随意增改程序 JSON 文件。状态入口是 `知识图谱构建过程/STATUS.md` 与 `run_state.json`；正式入口写出 `validation_result.json`。每轮报告与检查样本必须可从文件接手，而不依赖聊天记忆。

## 5. 第一轮：理解、连接并规范化原始输入

### 方法

1. 理解三个输入角色与字段类型，确认 N 和 rank，核查核心 `record_id` 唯一。
2. 用核心 record_id 唯一连接主表；元数据以主表为权威。核心文件与主表的标题/年份/DOI 差异记录并审查，不能标题模糊匹配代替连接键。
3. 按 rank 分配 P001…Paper ID，字符串统一空白、year 转整数、DOI 去前缀/标点并小写、作者统一数组；保留原文科学意义。当前有效年份范围 1800 至执行年份加一。
4. 仅保留核心来源引用；保留 `target_reference_raw` 原文、规范化副本、DOI 候选、来源文件/行号；不消除可审计原始证据。
5. 检查缺失/重复/跨表差异与外围忽略情况，不生成 Claim。

### 命令

```powershell
python pipeline.py run 1
```

入口自动执行独立校验。单独复查用 `python pipeline.py validate 1`。

### 保存与通过条件

保存 `01_规范化输入/` 的 `papers.jsonl`、`citations_raw.jsonl`、`paper_provenance.jsonl`、`input_profile.json`、`normalization_report.json`、`inspection_sample.json`、`independent_validation.json`、`第一轮检查报告.md`。

通过条件：核心集合无重无漏、全部唯一连接且有可用摘要/年份；原始引用来源和输入指针正确；每个异常有处理说明；独立校验通过。样本按实际集合最多确定性抽取十篇，小集合不访问不存在的 ID。DOI 缺省、无引用、外围缺摘要不是补造数据的理由。

人工核对样本原始行与 Paper 的标题、摘要、年份、DOI、作者、引用来源。自动校验只抽样验证追溯时，不宣称全部科学元数据已人工检查。

## 6. 第二轮：解析并复核隐藏引用骨架

### 方法与命令

```powershell
python pipeline.py run 2 prepare
```

程序做唯一 DOI 精确匹配；书目回退、歧义、自指或未来引用进入队列。带有不匹配 DOI 时不能凭作者/年份认定为另一篇核心论文。当前回退解析依赖逗号分隔的作者、年份、期刊等参考文献格式；格式差异会降低召回，必须在报告中说明，不猜测目标。

Agent完整阅读 `02_引用骨架/AGENT_TASK.md`，逐条检查 `citation_review_queue.jsonl`。只能依据该行原始引用和候选元数据决定，将决定写入 `citation_review_decisions.jsonl`。每个队列ID恰好一条，accept只能选真实候选，reject的目标为null；证据不足、歧义或时间矛盾无法解释则拒绝。

```json
{"reference_id":"R000001","decision":"reject","target_paper_id":null,"review_note":"书目信息不足以唯一确认目标"}
```

接纳结构同样包含 reference_id、decision、target_paper_id、review_note。空队列由程序创建空决定文件，无需编造决定。

```powershell
python pipeline.py run 2 finalize
```

### 保存与检查

保存 `cites.jsonl`、`reference_resolution_candidates.jsonl`、队列与决定、`citation_preliminary_report.json`、`citation_report.json`、样本及 `第二轮检查报告.md`。

检查内部端点、唯一边、逐条引用证据、自指与未来引用、回退接纳理由、同年引用与未解析比例。CITES仅是隐藏的追溯和召回信号；最终不显示Paper节点或引用边。缺CITES不等于Claim无关；引用孤立论文不删除。核心之外的参考文献有审计但不扩入最终节点。

## 7. 第三轮：仅凭标题摘要生成Claim草稿

### 任务准备

```powershell
python pipeline.py run 3 prepare --batch-size 10
```

程序输出 `03_Claim生成/task_manifest.json`、`task_batches/batch_*.json`、`AGENT_TASK.md`、`设计文档快照.md`。生成Agent只读这些允许内容。任务中每篇恰含 paper_id、title、abstract；不读取第一轮完整Paper绕过字段边界。

### 生成规则

1. 每篇保留1–3条最核心、最有知识谱系价值的命题；不是为了满足连通性选择Claim。
2. 命题必须完整、原子、作者承担立场且原则上可支持或反驳；保留对象、方向、否定、必要条件与原文语气。
3. 从结果、作者解释、理论论证或方法比较提取；研究目的、操作步骤、背景事实、开放问题和宽泛research gap不是节点。
4. 不将相关增强为因果，不把未发现证据改写成不存在，不把假设写成研究发现；作者“提示/可能”不自动意味着conditional。
5. evidence_spans保存1–3个最短且共同充分支持整条Claim的连续逐字摘要片段；不能改写证据，不能仅支持命题的一部分。匹配对象是任务中规范化摘要。
6. scope只保留影响真值/跨论文比较的范围，摘要未说明填null，不推测物种、范式或测量。
7. evidence_basis依摘要明示依据选择；confirmatory表示围绕核心问题/假设的主要分析，不等于预注册，不用它掩盖明确的仿真、重分析或综合依据。
8. 不自行生成claim_id。不提供其他论文的观点作为本篇作者的新Claim。

### 字段契约

- claim_type：empirical、interpretation、theoretical、methodological、synthesis。
- claim_status：affirmative、absence、no_evidence、conditional。
- scope恰含population、modality、paradigm、measurement_level、conditions；值为字符串或null。
- evidence_basis非空数组，允许confirmatory、exploratory、replication、simulation、reanalysis、literature_synthesis、theoretical_argument。
- evidence_spans每项text为非空逐字片段，location固定abstract。
- normalized_text为完整中文命题；selection_reason非空。

### Agent输出与收集

按manifest完成全部批次。`agent_outputs/batch_*.jsonl`每行一篇，保持任务顺序，无Markdown围栏：

```json
{"paper_id":"P001","claims":[{"normalized_text":"由本篇摘要支持的完整中文命题","claim_type":"empirical","claim_status":"affirmative","scope":{"population":null,"modality":null,"paradigm":null,"measurement_level":null,"conditions":null},"evidence_basis":["confirmatory"],"evidence_spans":[{"text":"真实任务摘要中的连续逐字片段","location":"abstract"}],"selection_reason":"说明其核心贡献与保留依据"}],"paper_notes":"可选"}
```

示例文字必须替换，不能作为默认答案。完成全部批次后：

```powershell
python pipeline.py run 3 collect
```

保存草稿 `claims_draft.jsonl`、任务与输出、`claim_generation_report.json`、`人工抽查样本.json/.md`、`第三轮检查报告.md`。草稿键为P001-D01等。检查作者立场、原子性、范围、强度与证据；逐字包含不是语义充分性的证明。

当前契约每篇至少一条。如果摘要确实不支持任何实质Claim，报告摘要/纳入问题并停止解决，不编造；未经授权不改“至少一条”约束。

## 8. 第四轮：独立语义复核与稳定Claim身份

```powershell
python pipeline.py run 4 prepare
```

复核Agent只读本轮标题、摘要、第三轮草稿、设计快照与任务规范，不看年份、引用或跨论文关系。独立指单独的复核阶段与输入边界，不要求换模型；即便同一个Agent，也逐条重新核对而非机械接受。

逐条核对作者立场、语义完整、原子性、类型、逻辑状态、scope、依据和逐字证据。conditional仅表示关键成立条件，普通实验范围进scope；absence是明确不存在主张，no_evidence是当前未检测到证据。拆分多个可独立判断的结果，同篇重复可以合并，越界或无实质内容可以删除。不跨论文合并。不因希望连边而弱化范围或增强理论概括。

输出为 `04_Claim复核/agent_outputs/batch_*.json`，是JSON文件，不是JSONL。reviewed_claim_keys必须按任务顺序列出全部草稿键；未进changes的项表示实际复核后接受，不是省略阅读。

```json
{
  "task_id": "batch_001",
  "reviewed_claim_keys": ["P001-D01", "P001-D02"],
  "changes": [
    {
      "source_claim_keys": ["P001-D01"],
      "action": "edit",
      "reason": "明确说明原命题的问题与修正依据",
      "replacement_claims": [
        {
          "review_claim_key": "P001-R01",
          "normalized_text": "重新核对后摘要支持的完整中文命题",
          "claim_type": "empirical",
          "claim_status": "affirmative",
          "scope": {"population": null, "modality": null, "paradigm": null, "measurement_level": null, "conditions": null},
          "evidence_basis": ["confirmatory"],
          "evidence_spans": [{"text": "真实摘要的连续逐字片段", "location": "abstract"}],
          "selection_reason": "说明保留依据"
        }
      ]
    }
  ],
  "batch_notes": "完整阅读后的复核说明"
}
```

edit一个替换；split至少两个；merge至少两个同篇来源、一个替换；drop无替换。changes每项有来源键和理由；不能同一来源重复变更。遗漏但必要的核心内容通过有来源草稿键的替换/拆分恢复，当前接口不支持无来源自由新增Claim。

review_claim_key全项目唯一，用于身份注册；同一命题文案修改保持该键可保留ID，新独立命题或真正改变含义应使用新键。未修改项沿用原草稿键。不能借“稳定ID”混淆不同命题。

```powershell
python pipeline.py run 4 collect
```

保存 `claims.jsonl`、`claim_id_registry.json`、复核任务/输出、设计快照、`claim_review_report.json`、样本、`第四轮检查报告.md`。全部核心论文须保留有依据的Claim；如果drop导致某核心没有Claim，解决输入或复核决定，不编造。验证来源、跨篇禁止、证据和活动注册项。删除身份不复用；同任务修订不删除注册表。此轮不判断跨论文关系。

## 9. 第五轮：准备领域配置并召回候选

### 9.1 Agent领域配置：只换data即可适配新领域

第四轮通过后执行：

```powershell
python pipeline.py prepare-domain
```

程序在输出根目录创建 `领域配置.json` 并填写当前Paper、Claim哈希。Agent此时可读本任务最终Claim、标题、摘要、scope；归纳领域词和主题泳道，不读任何既有关系答案，不增加摘要外科学事实。

完整填写以下字段，再将prepared_by_agent改为true；这是实际完成的行为确认，不得机械改布尔值：

- domain_name与preparation_notes：任务主题和配置依据。
- topic_terms：本集合重要构念、技术/方法、对象、现象与关键词；覆盖子方向，不仅挑大主题。使用casefold小写形式，按normalized_text中实际词形匹配，保留中文及必要英文名词，避免所有词都过于宽泛。
- generic_topics：topic_terms中不足以独立证明可比性的宽泛主题子集；不能把所有技术词都列为泛词。
- frequency_topics：topic_terms中“同标签但不证明同命题”的频段/类似技术标签子集；无此类别填空数组。字段保留算法使用方式，不据共享标签直接连边。
- mechanism_cues、challenge_cues：机制与质疑语言提示词；模板给出中文语言词，可保留或依领域术语补充，不把它们解释为已经证明机制/反驳。
- english_stopwords：仅用于可读英文共享词片段的排除列表；不要排除本集合核心科学构念。
- lanes：主题呈现泳道数组，每项lane_id、lane_name唯一非空；通常约六条，也允许按集合需要调整。覆盖理论/综合、主要现象/任务、机制/层级、方法/干预与零结果/边界等适用维度；不要把某个领域的六条主题机械套到所有方向。
- lane_rules：有序数组，每项lane_id及可选claim_types、claim_statuses、keywords、reason。多个条件为OR匹配；第一条命中即使用该泳道，不是AND；无任何条件的规则不允许，用default_lane_id处理默认。
- default_lane_id：真实存在的默认泳道；所有Claim能归入一个呈现组。
- source_papers_sha256、source_claims_sha256：保留程序生成的当前输入哈希。Claim或Paper变化后必须重新审查配置并更新真实哈希；用 `python pipeline.py domain-inputs` 查询，不伪造。prepare-domain保留已有配置，不会替你重写旧哈希或重新做领域归纳。

领域配置仅改变原算法的词表和分类参数，不加入节点答案、关系清单或预计结果数量。无需用户手动修改代码。不同领域必须重新归纳，配置冻结后用于第五至七轮；若需修改配置，从第五轮重跑，不能混用不同配置的关系与图。

### 9.2 候选方法与命令

```powershell
python pipeline.py run 5
```

输入最终Claim、Paper标题/年份、CITES与本任务配置。算法固定：字符2–4gram TF-IDF，min_df=2、sublinear_tf=True，余弦相似度；文本拼接Claim、非空scope与标题。不是LLM embedding。

| 通道 | 当前算法 |
|---|---|
| within_paper | 枚举同篇Claim对，给结果—解释、方法—发现等论证关系比较机会 |
| semantic_topk | 每Claim在同年/更早的其他论文中取前10个 |
| citation_1hop | 每Claim在直接内部引用论文中取最可比的前6个 |
| citation_2hop | 每Claim在两跳前驱论文中取前3个 |
| scope_neighbor | 同年/更早且scope有精确字段重叠，取前4个 |
| coverage_fallback | 无跨论文比较对象时加入最佳对象，仅保障可比较，不保证相关 |

去重成无序Claim对，保留多通道信号；两跳引用不会引入Event节点或Paper连线。候选有稳定candidate_id、端点、年份、信号、semantic_score、共享词片段、scope重叠、引用方向、orientation_hint与candidate_only=true。

保存 `relation_candidates.jsonl`、`candidate_generation_report.json`、样本与 `第五轮检查报告.md`。多通道计数不能相加当候选总数。检查早期源头、零结果/方法、跨模态/层级及同篇关系的比较机会；scope字符串规范一致会影响召回。候选度数与连通性不是关系正确性的证据。

小集合TF-IDF空词表时程序使用零语义分数并给警告，其余通道仍可工作；单篇集合无法有跨论文候选。对此如实记录，不编造词表或关系，不未经授权更改min_df和近邻数。低非引用召回比例是审查提醒，不是强行添加候选的配额。

令M为最终Claim数，当前全相似度矩阵和全范围比较有O(M²)时间/内存成本。很大集合先检查运行资源；不要为省资源未经授权换算法、随机删除论文或静默抽样。遇到资源限制记录并请求适当运行资源或明确的工具升级方向。

## 10. 第六轮：规则初判、定点语义复核与关系定稿

### 10.1 初判

```powershell
python pipeline.py run 6 --batch-size 50
```

程序对全部候选生成relation或unrelated决定，并保存在 `06_关系判断/`。规则用Claim类型/状态、词、scope、相似分数、引用与年份进行初判；不等于Agent逐条语义判断。当前每个无序候选对最多一条保留关系。

首轮没有Agent复核确认时，正式入口保持agent_required，不能进入第七轮。阅读 `AGENT_REVIEW.md`、`review_plan.json`、`review_batches/batch_*.json` 和抽查样本；必要时按端点回查本任务claims.jsonl的逐字证据与papers.jsonl的摘要。批次提供两端文本/类型/状态/scope/年份、召回信息、当前决定，但不直接包含全部逐字证据。

### 10.2 关系、状态与方向契约

关系表示来源Claim对目标Claim的知识作用，必须能明确说明，而不是主题、方法、频段或引用相同：

| 类型 | 判断标准 |
|---|---|
| SUPPORTS | 一致证据、复现或论证提高目标可信度 |
| EXTENDS | 保留目标核心内容并推广到新任务、对象、模态、范围或层级 |
| QUALIFIES | 保留部分核心但限制条件、适用范围、强度或解释 |
| CHALLENGES | 反例、零结果、失败复现、方法质疑或不一致 |
| ALTERNATIVE_TO | 同一现象的竞争解释，不必直接证伪 |
| MECHANISM_FOR | 来源命题说明/细化目标现象如何产生或实现 |

不要用MECHANISM_FOR泛指解释，不把新范围和支持混为一谈，不把不同scope下结果差异自动当反驳。负结果须检查研究对象和检验是否真正对应目标。没有充分知识作用则unrelated；证据不足但存在可解释作用才candidate，不用candidate容纳任意主题相似。

跨论文存储默认新Claim→旧Claim；同年跨论文必须有与方向一致的内部引用，当前无引用方向则拒绝。同篇按知识依赖定向，如解释/机制指向所解释实证。规则的类型/ID平局处理只是建议，必须实际检查；ID、年份及同篇次序都不是文本证明。同年边不能推出真实月份先后。不无授权改变跨论文时间定向体系。

字段：source_claim_id、target_claim_id、relation_type、directness、scope_overlap、confidence、annotation_status、orientation_basis、reason。directness为direct/indirect，scope_overlap为high/medium/low。direct要求摘要明确表达作用，不等于有CITES。

规则筛选可比且可定向关系，confidence≥0.82为accepted，低于0.82为candidate；不满足筛选为unrelated/rejected。没有另设0.50截断。分数不是正确概率或Agent签字，accepted也不等于金标准；状态与阈值必须一致。

### 10.3 Agent复核选择与覆盖

review_plan的最低实际阅读范围为：全部关系类型/待复核抽查样本，以及保留层每个孤立Claim的最高相似前三候选（不足三条则全部）。可按风险追加：跨范围/层级、无引用延续、机制/竞争解释、同年/同篇方向、重要源头与多父汇聚。不得套用固定人工条数配额。没有候选时清单可为空，但必须实际核查这一边界。

逐对记录实际复核，不因规则accepted免读。对复核后无需改变的决定也列入reviewed_candidate_ids；对修订写 `relation_overrides.jsonl`，每行一个完整决定，不是字段局部补丁。覆盖ID唯一、真实存在，两端等于候选端点。

```json
{"candidate_id":"K_C001_C020","judgment":"relation","source_claim_id":"C020","target_claim_id":"C001","relation_type":"EXTENDS","directness":"indirect","scope_overlap":"medium","confidence":0.8,"annotation_status":"candidate","orientation_basis":"publication_year","reason":"具体说明来源保留目标的什么核心内容、推广到什么范围，以及摘要证据的限制。","judgment_method":"agent_manual_override"}
```

```json
{"candidate_id":"K_C002_C021","judgment":"unrelated","annotation_status":"rejected","reason":"两端只有宽泛主题一致，摘要不能支持一种具体知识作用。","judgment_method":"agent_manual_override"}
```

文字和ID全部替换为本任务真实内容。低置信也须有知识作用理由；reason不能只复述共享词或关系类型。漏候选先回第五轮，不在覆盖中凭空引入新对。

### 10.4 复核确认与定稿顺序

1. 完成初判的最低计划和必要补充复核，写覆盖决定。
2. 若已有旧 `agent_review_attestation.json`，保存旧确认到执行记录/任务归档后暂时移开；它不能给修改后的覆盖文件作确认。
3. 重跑 `python pipeline.py run 6 --batch-size 50`。无确认文件时保持agent_required，并更新全部决定、样本及review_plan的当前哈希。
4. 检查最终计划；若覆盖导致新增最低复核对象，实际补读后更新决定，重复上一步，直至决定稳定。
5. 保存 `agent_review_attestation.json`：

```json
{
  "completed_by_agent": true,
  "reviewed_candidate_ids": ["本任务中实际阅读过的candidate_id"],
  "input_hashes": {
    "claims": "从最终review_plan复制的真实哈希",
    "candidates": "从最终review_plan复制的真实哈希",
    "overrides": "从最终review_plan复制的真实哈希",
    "domain": "从最终review_plan复制的真实哈希"
  },
  "notes": "实际核验内容、修订与仍有不确定性的说明；不宣称未做的全面复核"
}
```

reviewed_candidate_ids无重、全是真实候选，包含最低计划和全部Agent覆盖；input_hashes准确等于最终计划。没有候选时数组为空，并说明检查情况。确认文件不是正确性证明，不能未经阅读机械照抄ID。

6. 再重跑第六轮，入口执行独立校验，确认有效才写round_6_completed。若确认失效或最低清单未覆盖，继续修正，不进入第七轮。

### 保存与解释

保留全部 `relation_decisions.jsonl`，包括拒绝项；另存 `relations.jsonl`、`accepted_relations.jsonl`、`provisional_relations.jsonl`、`relation_id_registry.json`、覆盖、批次、计划、确认、`relation_judgment_report.json`、样本和 `第六轮检查报告.md`。

报告分别写：全部程序决定数、实际Agent阅读范围、Agent覆盖数、正式/待复核/拒绝数、孤立情况及限制。程序决定覆盖不能描述成穷尽Agent审查。未使用某关系类型不是错误，不强制凑全六类。较多孤立是诊断提醒，不是失败真值；无依据宁可孤立。

## 11. 第七轮：装配、时间泳道SVG与验收

```powershell
python pipeline.py run 7
```

输入定稿Claim、保留关系、Paper出处和领域配置；不在绘图时重新判断科学内容。完整图包含全部Claim，包括孤立点；无Paper节点、无CITES边、无Evolution Event。正式骨架只包含accepted边及端点，不代表其他Claim不存在。没有正式边时输出明确说明的空骨架，不编造。

### 泳道与网络呈现

领域配置的有序规则把每Claim放入一条泳道；跨泳道边不限，分组不改变Claim科学类型或关系成立性。横轴每年固定间距，同年同泳道按保留度数与ID排列，不是月份排序。曲线显示跨年/同年连接，节点超长文本缩略，悬停及JSON保留全文。

保留一对多、多对一、跨泳道及同年依赖，不选唯一父节点，不树化。两个前驱分别连到一个后续Claim表达多来源关系，但不自动证明两篇论文联合指导、融合或真实借鉴；需要具体边理由支持更强陈述。

人工泳道调整保存 `07_图谱装配/lane_overrides.jsonl`，再只重跑第七轮：

```json
{"claim_id":"C001","lane_id":"本任务真实lane_id","reason":"呈现分组调整的具体理由"}
```

覆盖ID唯一且真实；不改Claim文本或关系。需要改泳道体系而非个别归组时改领域配置，并从第五轮重跑保证配置一致。

### 双方向约定：不得将SVG当语义箭头

graph_edges同时保存semantic_source_claim_id/semantic_target_claim_id与history_from_claim_id/history_to_claim_id。前者对应第六轮source→target知识作用；后者当前对全部边取反向，用于历史阅读。

数据“新B EXTENDS 旧A”，SVG画A→B，颜色仍是EXTENDS；不是“A扩展B”。同年/同篇显示反向不证明时间先后；MECHANISM_FOR显示箭头不是因果箭头。LLM知识作用分析用semantic字段，视图路径分析用history字段，不混用入/出度。混合关系的多跳路径不能自动传递为支持链、因果链或经正文验证的发展史。

accepted用实线，candidate用低透明度虚线，颜色代表关系类型。节点色条代表类型，红框提示零结果/无证据；不要在下游把虚线升级为事实。

### 主产物与验收

保存 `graph_nodes.jsonl`、`graph_edges.jsonl`、`graph.json`、`layout.json`、`components.json`、`evolution_analysis.json`、`graph_assembly_report.json`、泳道覆盖、`完整Claim时间泳道式演化网络.svg`、`正式关系骨架.svg`、`第七轮检查报告.md`。

验收要点：

- 最终Claim唯一入图且无核心论文遗漏；边与第六轮保留关系一一对应；出处、年份、类型、状态与双方向一致。
- 分区与注册表正确；节点坐标/边界、连通分量、严格跨年路径与分析文件可复核。
- SVG为可解析、自包含、无脚本的矢量图；节点/边ID一致，candidate正确虚线。不要改用有文本上限的网站作为唯一交付。
- 在可用浏览器或矢量编辑器缩放检查文字、交叉线、节点和悬停。XML/坐标通过不等于像素可读性已检查；无法展示时明示限制，不假报截图验收。
- 连通性、多级路径、分支/汇聚及环的警告如实记录。小集合/短年份范围可天然不具备长路径；真实同年依赖可能有环。不得通过编造/删改关系迎合拓扑目标。
- 正式入口独立校验通过后才写completed。`STATUS.md`、run_state、validation_result和最终文件同时核对；程序完成与科学金标准明确分开。

## 12. 修订、重跑与身份管理

主产物是派生结果，重跑会覆盖。修改科学内容应改原始输入、Agent输出或覆盖决定，不直接改最终SVG、graph.json、已收集Claim/关系主文件。保存改动依据和版本记录。

| 修改内容 | 修改源 | 重跑范围 |
|---|---|---|
| 替换data、新集合或原始摘要/元数据变化 | 原始输入 | init --new-task可恢复归档；重新七轮。不要混用旧科学输出 |
| 引用队列决定 | 第二轮decisions | 第二轮finalize；为了入口顺序重验未变的第三/四轮，再第五至七轮 |
| 草稿生成 | 第三轮agent_outputs | 第三轮collect，第四轮prepare/真实复核/collect，再领域配置及第五至七轮 |
| 最终Claim复核、状态、拆分或证据 | 第四轮复核JSON | 第四轮collect，重新审查领域配置，再第五至七轮；保留Claim注册表 |
| 领域词表、泳道规则或领域配置 | 领域配置 | 第五至七轮；重新审查第六轮确认 |
| 关系类型/方向/理由/状态 | 第六轮overrides | 第六轮完整复核确认流程，再第七轮；保留关系注册表 |
| 个别泳道归组 | 第七轮lane_overrides | 第七轮 |
| 算法或设计规范真正变化 | 需用户明确授权的工具维护 | 建立新任务/新执行记录，不能混用不同实现；不是日常只换data的任务 |

正式入口保守地按七轮顺序失效下游记录。实际输入未变且科学阶段结果可复用时，用 `validate 3/4` 重新建立其检查记录，不机械重新生成全部Claim；但不能用validate掩盖真实输入变化。

prepare会对变更的任务输入自动隔离旧任务/Agent输出到本轮任务归档。第三、第四轮collect检查任务输入指纹，输入变更必须重新prepare并实际执行受影响Agent任务。第四轮依据第三轮manifest而不是任意残留文件读取批次；同输入重新prepare可保留对应输出，但仍检查其适用性。

同论文集修订保留claim_id_registry和relation_id_registry；新集合从空注册表开始。Claim身份靠稳定review_claim_key；关系身份靠无序candidate_id，改同对类型/方向通常保留关系ID，故必须记录修订。删除身份不回收给别的内容。

Claim成员变化后旧candidate或泳道覆盖可能失效；按端点和证据迁移，不无条件套用旧决定。原rank变化改变Paper ID与所有下游键；更换纳入名单或重排按新任务处理。领域配置的Paper/Claim哈希必须真实更新并重新审查。

状态检查会发现文件指纹变化；失败/陈旧结果不能继续当成完成图。先解决最早受影响阶段，再重跑依赖。不能改校验器去掩盖格式、证据、身份或端点错误。

## 13. 交付、可复现性与终止条件

默认只在连续完成七轮后进行一次最终交付。执行期间可发送简短进度更新，但不在轮间索取批准；每轮的主产物、检查入口、真实 Agent 覆盖、校验结论与剩余问题必须写入当轮文件，而不是只存在聊天中。用户明确要求逐轮检查时，才在每轮闭环完成后暂停。

若因真实阻断提前停止，交付的是阻断报告而不是完成声明。报告必须包含当前状态与轮次、最后成功检查点、相关文件、错误或歧义证据、已经尝试的恢复动作，以及继续所需的最小用户决定。能够在既定输入和权限内自修复的问题应先自行修复，不提前交回用户。

最终至少交付完整SVG、正式骨架、graph.json及节点边/布局分析，并保留原始输入追溯、CITES审计、草稿/复核/稳定身份、全部候选决定、覆盖与确认、领域配置、七轮记录和STATUS。不要只留一张图。

可复现性指相同字段契约、算法、任务规范、参数、输出格式与检查机制；不同Agent可能产生不同但有依据的命题或关系，不保证随机生成内容逐字相同。冻结Agent结果、配置和代码后可重绘一致图谱；报告时间戳与环境也应记录。

仅在输入可用、七轮真实工作完成、阶段校验通过、最终一致性验收完成且限制有说明时，接受completed。真实输入缺失或需改变科学范围时请求用户方向；不为了持续运行而补造科学内容。不把运行成功、规则accepted或用户接受呈现形式表述为所有关系已成为科学真值。
