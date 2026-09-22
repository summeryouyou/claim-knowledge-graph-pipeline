# Claim 知识谱系设计

建立以 claim 为中心的理论演化流程，从而显式表现相关主题的发展脉络。

需求输入
1、有一定时间跨度的论文，包含标题和摘要
2、原始参考文献记录，程序解析为隐藏的内部引用骨架

知识谱系的关键数据结构
1、paper

```yaml
paper_id: P001
record_id: TASK:...
title: "..."
year: 2024
doi: "..."
abstract: "..."
```

Paper 只承担来源和结构功能

支持实证研究、零结果、失败复现、探索性分析、仿真与方法比较、重分析、综述及理论模型等文章类型，不预设某一研究领域。

2、claim

Claim 是作者承担立场、原则上可以被证据支持或反驳的完整命题。

```yaml
claim_id: C001
paper_id: P001

normalized_text: "完整、原子、可证伪的规范化命题"

claim_type: empirical | interpretation | theoretical | methodological | synthesis

claim_status: affirmative | absence | no_evidence | conditional

scope:
  population: "研究对象"
  modality: "本研究的信息、数据或感觉模态"
  paradigm: "任务或研究情境"
  measurement_level: "本研究的观察、测量、分析或模型层级"
  conditions: "使命题成立的关键条件"

evidence_basis:
  - confirmatory
  - exploratory
  - replication
  - simulation
  - reanalysis
  - literature_synthesis
  - theoretical_argument

evidence_spans:
  - text: "直接支持该命题的原文"
    location: "abstract"
```

 Claim 属性说明

| 属性                | 操作性定义                                                             |
| ----------------- | ----------------------------------------------------------------- |
| `claim_id`        | Claim 的唯一标识符。每条独立命题对应一个 ID，删除后不重复使用。                              |
| `paper_id`        | 提出或报告该 Claim 的论文 ID，不是该论文引用或讨论的其他论文。                              |
| `normalized_text` | 在不增加原文未表达内容的前提下，将作者承担的观点改写为完整、原子、可证伪的陈述句，保留对象、关系、方向、否定、关键条件和原文语气。 |
| `claim_type`      | Claim 在论文论证中的内容类型，根据命题本身及其证据来源判断。                                 |
| `claim_status`    | Claim 对目标关系或效应所作判断的逻辑状态，尤其用于区分“效应不存在”和“当前未发现证据”。                  |
| `scope`           | Claim 成立或适用的研究范围。只记录可能影响命题真值或跨论文可比性的条件，原文未说明时填 `null`。            |
| `evidence_basis`  | 作者形成该 Claim 所依据的证据或论证方式。允许一条 Claim 对应多种依据。                        |
| `evidence_spans`  | 原文中直接支持 `normalized_text` 的一个或多个连续文本片段，并记录其所在位置。                  |

 `claim_type`

| 类型               | 定义                                  |
| ---------------- | ----------------------------------- |
| `empirical`      | 当前研究通过实验、观察或分析直接获得的结果。              |
| `interpretation` | 作者根据当前研究结果推导出的含义、机制或解释。             |
| `theoretical`    | 作者提出的一般理论、机制、模型或可检验假设。              |
| `methodological` | 关于研究方法、测量指标、分析程序或统计策略的适用性、性能或局限的命题。 |
| `synthesis`      | 作者综合多项既有研究后形成的总体判断，常见于综述、观点或理论文章。   |

 `claim_status`

|状态|定义|
|---|---|
| `affirmative` |作者主张某种关系、效应、机制或现象存在。|
| `absence` |作者明确主张某种关系、效应或现象不存在。|
| `no_evidence` |当前研究未检测到支持某种关系或效应的证据，但不主张其不存在。|
| `conditional` |作者主张某种关系或效应只在特定人群、任务、状态或条件下出现。|

若条件是命题结论的核心，例如“仅在低表现状态下出现振荡”，标为 `conditional`；普通实验范围写入 `scope`，不因此标为 `conditional`。

 `scope`

|子属性|定义|
|---|---|
| `population` |Claim 涉及的人群、物种、系统、材料、研究对象或特定样本群体。|
| `modality` |Claim 涉及的感觉、信息或数据模态；不适用或原文未说明则null。|
| `paradigm` |产生该结论的任务范式或研究情境。|
| `measurement_level` |Claim 对应的观察、测量或分析层级及证据形式，依本领域与摘要明示内容填写。|
| `conditions` |使命题成立的关键边界条件，如线索有效性、注意负荷、任务难度或内部状态。|

 `evidence_basis`

|类型|定义|
|---|---|
| `confirmatory` |来自论文围绕主要问题或假设开展的核心分析，不自动表示研究已经预注册。|
| `exploratory` |来自作者明确标注的事后、探索性或非预设分析。|
| `replication` |来自对既有研究结果的明确重复检验。|
| `simulation` |来自模拟数据、计算实验或模型运行结果。|
| `reanalysis` |来自对既有数据或已发表数据的重新分析。|
| `literature_synthesis` |来自对多项既有研究证据的归纳和综合。|
| `theoretical_argument` |来自概念推理、理论推演或模型论证，而非直接数据分析。|

 `evidence_spans`

每条 Claim 保存 1–3 段最短且充分的原文证据：

```yaml
evidence_spans:
  - text: "原文连续片段"
    location: "abstract"
```

证据片段应共同支持完整 Claim；不能只支持其中一部分，也不能用研究目的或假设陈述作为实证结果的证据。

3、relation

Relation 表示一个 Claim 对另一个 Claim 的知识作用。最终图只显示 Claim 节点和这些关系边；Paper 引用只作为隐藏的召回与审计信号。

```yaml
relation_id: R0001
source_claim_id: C002
relation_type: EXTENDS
target_claim_id: C001
directness: indirect
scope_overlap: medium
confidence: 0.78
annotation_status: candidate
candidate_id: K_C001_C002
```

### 关系类型

| 类型 | 操作定义 |
|---|---|
| `SUPPORTS` | 新 Claim 提供与旧 Claim 一致的证据、复现或论证，提高其可信度。 |
| `EXTENDS` | 保留旧 Claim 的核心内容，并推广到新任务、人群、模态、测量层级或现象。 |
| `QUALIFIES` | 保留旧 Claim 的部分核心内容，但限制其适用范围、强度、条件或解释。 |
| `CHALLENGES` | 新 Claim 提供反例、零结果、失败复现、方法学质疑或不一致结果。 |
| `ALTERNATIVE_TO` | 对同一现象提出与旧 Claim 竞争、但不一定直接证伪旧说的解释或机制。 |
| `MECHANISM_FOR` | 来源 Claim 提出、识别或细化能够产生或实现目标 Claim 所述现象的机制。 |

跨论文关系默认使用“较新 Claim → 较早 Claim”。同年论文优先由引用方向确定；没有引用或明确知识依赖方向时不强行建立有向关系。同篇论文按知识依赖定向，例如解释或机制 Claim 指向它所解释的实证 Claim。规则提供初判，Agent 必须核查定点复核计划；规则或 ID 的平局处理不证明真实论证先后。

### 类型边界

- 重复观察旧命题预测的结果用 `SUPPORTS`；把命题带到新范围用 `EXTENDS`。
- 新结果说明旧命题只在更窄条件成立用 `QUALIFIES`；表明旧命题可能不成立用 `CHALLENGES`。
- 提出竞争解释用 `ALTERNATIVE_TO`；说明现象如何产生或实现用 `MECHANISM_FOR`。
- 主题、频段或方法相同本身不构成关系；必须能说清来源 Claim 如何改变、推广、限制、反驳或解释目标 Claim。

### 关系属性

- `directness`：`direct` 表示摘要明确表达两端之间的知识作用；`indirect` 表示标注者根据两个 Claim、范围与来源信号推断。
- `scope_overlap`：`high` 表示核心现象和关键条件基本一致；`medium` 表示核心现象相同但任务、模态、人群或测量层级不同；`low` 表示仅部分构念重叠。
- `confidence`：规则初判可比较且可定向的关系，分数 `0.82–1.00` 进入正式层；低于 `0.82` 保留为待复核候选；未满足可比性或方向要求则判为 `unrelated`。不另设未实现的 `0.50` 截断。分数不是校准的正确概率。
- `annotation_status`：`accepted`、`candidate` 或 `rejected`。

在本工具中，非引用论文之间允许建立 `indirect` 关系，以恢复跨引用社区的知识连续性；这类边必须有清楚的语义作用说明，置信度一般不高于直接证据充分的关系。引用关系、两跳引用、语义相似和范围重叠都只用于召回，不能自动证明关系成立。

4、时间泳道式演化视图

- 最终可视化只显示 Claim 节点，不显示 Paper 节点或论文引用边；Paper 信息仅作为节点出处元数据。
- 数据层关系保持“较新 Claim → 较早 Claim”的知识作用方向。为了让读者从左到右阅读发展史，视图层将边反向绘制为“较早 Claim → 较新 Claim”，并在图例中明确说明这一转换。
- 横轴为发表年份；纵向泳道为呈现用主题分组，不改变 Claim 本身的科学分类，也不限制跨泳道关系。
- `accepted` 关系使用实线，`candidate` 关系使用低透明度虚线；颜色表示关系类型。
- 视图必须保留分支、多父节点汇聚、跨泳道连接和同年知识依赖，不能把图强制化为树或森林。
- SVG 是完整、可缩放的主交付物；同时保存节点、边、布局、主要演化路径和连通性分析，供人或 LLM 复核。

程序约束与 Agent 行为以根目录 `PIPELINE_AGENT.md` 为执行规范。领域词表及泳道由本任务 Agent 生成于 `知识图谱构建过程/领域配置.json`，不内置任何领域的科学答案。同年/同篇显示方向不表示发表月份先后。状态完成不等于关系金标准。
