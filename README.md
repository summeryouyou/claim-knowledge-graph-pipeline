# Claim Knowledge Graph Pipeline

> 从论文元数据、摘要与原始参考文献出发，构建可追溯、可复核的 Claim（科学命题）演化知识图谱。
>
> An auditable seven-stage pipeline for building claim-centric scholarly knowledge graphs.

## 为什么做这个项目

传统文献网络通常把“论文”作为节点、把“引用”作为边，但论文之间真正发生演化的是更细粒度的科学命题：后来的研究可能支持、扩展、限定、挑战某个既有结论，也可能提出替代解释或作用机制。

本项目将核心论文的标题、摘要和参考文献记录加工为时间泳道式 Claim 网络，并保留从原始输入到最终图谱的完整审计链。最终图只展示 Claim 节点和 Claim 之间的知识作用；Paper 与引用关系作为来源信息和候选召回信号保留在后台。

## 核心能力

- **Claim 中心建模**：将论文摘要中的主要贡献整理为完整、原子、可核查的命题，而不是只做关键词共现。
- **六类知识作用**：支持 `SUPPORTS`、`EXTENDS`、`QUALIFIES`、`CHALLENGES`、`ALTERNATIVE_TO` 和 `MECHANISM_FOR`。
- **七轮质量关口**：每轮都有明确的数据边界、阶段产物、独立校验与失效检测。
- **证据可追溯**：每条 Claim 保留论文来源、摘要逐字证据、范围、类型和证据基础。
- **人机协作审查**：规则负责规范化、候选召回和一致性检查；Agent 负责 Claim 生成、独立复核与关键关系判断。
- **可恢复执行**：输入和代码均带指纹；上游变化会使下游状态失效，旧任务可恢复归档。
- **双重交付**：输出自包含 SVG，同时提供 JSON/JSONL、布局、连通分量与演化路径分析。
- **领域可配置**：替换输入并由 Agent 生成领域词表、主题泳道和规则，不需要为每个领域改写算法。

## 七轮流水线

| 轮次 | 任务 | 主要产物 |
| --- | --- | --- |
| 1 | 规范化输入，锁定核心论文集合 | Paper、原始引用、来源追溯与输入报告 |
| 2 | 解析内部引用关系 | 隐藏 CITES 骨架、匹配候选与人工决定 |
| 3 | 仅根据标题和摘要生成 Claim 草稿 | Claim 草稿、逐字证据与抽查样本 |
| 4 | 独立复核 Claim 并分配稳定 ID | 最终 Claim、复核记录与身份注册表 |
| 5 | 准备领域配置并召回关系候选 | 多通道候选、召回报告与领域配置 |
| 6 | 规则初判与 Agent 定点复核 | 完整决定、正式关系、待复核关系与覆盖记录 |
| 7 | 装配图谱并生成时间泳道视图 | SVG、`graph.json`、节点、边、布局和结构分析 |

各阶段对 Agent 可见的科学字段严格分离。例如，第三轮只允许读取标题与摘要，避免引用、年份或既有关系反向影响 Claim 的选择。

## 快速开始

### 1. 获取项目并安装依赖

推荐使用 Python 3.11（项目当前验证环境为 Python 3.11）。

```bash
git clone https://github.com/summeryouyou/claim-knowledge-graph-pipeline.git
cd claim-knowledge-graph-pipeline
python -m venv .venv
```

Windows PowerShell：

```powershell
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

macOS / Linux：

```bash
source .venv/bin/activate
python -m pip install -r requirements.txt
```

### 2. 准备三份 JSONL 输入

在 `data/` 中准备以下三类文件：

```text
data/
├─ 核心文献n篇.jsonl
├─ 文献主表.jsonl
└─ 参考文献关系-原始.jsonl
```

JSONL 要求每个非空行都是一个完整 JSON 对象，编码为 UTF-8。核心论文数量取自核心文件的实际行数，不从文件名推断。

核心名单至少包含 `record_id`、`rank` 和 `title`：

```json
{"record_id":"TASK:001","rank":1,"title":"论文原始标题","year":2020,"doi":null}
```

文献主表中，每篇核心论文必须能通过 `record_id` 唯一连接，并具有标题、摘要和有效年份：

```json
{"record_id":"TASK:001","title":"论文原始标题","year":2020,"doi":null,"authors":["Li, A"],"source_title":"Journal X","abstract":"原始摘要全文"}
```

原始参考文献记录通过 `source_record_id` 指向来源论文：

```json
{"source_record_id":"TASK:001","relation":"cites_raw_reference","target_reference_raw":"Wang B, 2018, JOURNAL Y, DOI 10.1234/example"}
```

主表和引用文件可以包含核心集合之外的记录，它们不会被自动扩入最终图谱。没有参考文献时，第三个文件仍需存在，但可以为空。

### 3. 让 Agent 连续执行七轮

本项目是 **Agent 工作流 + 确定性程序**，不是一个只需单次命令即可无人值守完成全部科学判断的分类器。第三、第四和第六轮需要 Agent 真实阅读任务批次并生成或复核语义结果。

推荐方式：在支持本地文件与命令执行的编码 Agent 中打开项目根目录，将 [`启动提示词.md`](./启动提示词.md) 的内容发送给 Agent。Agent 应完整阅读 [`PIPELINE_AGENT.md`](./PIPELINE_AGENT.md)、[`AGENTS.md`](./AGENTS.md) 与 [`Claim知识谱系设计.md`](./Claim知识谱系设计.md)，然后从初始化连续运行到 `STATUS.md` 显示 `completed`。

流水线正式入口为：

```powershell
python pipeline.py init
python pipeline.py status
```

不建议绕过 `pipeline.py` 直接运行内部阶段脚本。它负责阶段顺序、输入指纹、下游失效检测、校验和状态记录。

## 常用命令

| 命令 | 用途 |
| --- | --- |
| `python pipeline.py init` | 冻结当前输入并初始化任务 |
| `python pipeline.py status` | 查看任务状态和已校验轮次 |
| `python pipeline.py run 1` | 执行输入规范化 |
| `python pipeline.py run 2 prepare` | 准备引用解析及待复核队列 |
| `python pipeline.py run 2 finalize` | 收集引用复核决定 |
| `python pipeline.py run 3 prepare --batch-size 10` | 生成 Claim 提取任务批次 |
| `python pipeline.py run 3 collect` | 收集 Claim 草稿 |
| `python pipeline.py run 4 prepare` | 生成 Claim 独立复核任务 |
| `python pipeline.py run 4 collect` | 收集复核结果并稳定 Claim ID |
| `python pipeline.py prepare-domain` | 创建本任务的领域配置 |
| `python pipeline.py run 5` | 生成关系候选 |
| `python pipeline.py run 6 --batch-size 50` | 生成关系初判或收集 Agent 复核 |
| `python pipeline.py run 7` | 装配机器图并渲染 SVG |
| `python pipeline.py init --new-task` | 可恢复地归档旧结果并开始新任务 |

若要使用自定义输出目录，将 `--workspace` 放在子命令之前，并在后续命令中保持一致：

```powershell
python pipeline.py --workspace "任务结果" init
```

完整的逐轮操作、字段契约和审查要求见 [`PIPELINE_AGENT.md`](./PIPELINE_AGENT.md)。

## 最终输出

默认结果位于 `知识图谱构建过程/07_图谱装配/`：

- `完整Claim时间泳道式演化网络.svg`：包含全部 Claim、正式关系和待复核关系的主视图。
- `正式关系骨架.svg`：只显示正式关系及其端点。
- `graph.json`：适合下游程序使用的完整节点和边。
- `graph_nodes.jsonl` / `graph_edges.jsonl`：流式、易审计的数据表。
- `layout.json`：泳道和节点坐标。
- `components.json`：连通分量分析。
- `evolution_analysis.json`：严格跨年路径、枢纽和网络摘要。
- `graph_assembly_report.json` / `第七轮检查报告.md`：装配结果与验收记录。

`知识图谱构建过程/STATUS.md` 是面向人的状态入口，`run_state.json` 是机器可读状态。只有状态为 `completed` 且七轮真实工作均已完成，才能把结果视为本次流水线的完整交付。

## 图中箭头如何理解

数据层的关系方向是“较新 Claim → 较早 Claim”，表示新命题对旧命题产生的知识作用。为了让时间演化图从左向右阅读，SVG 会把显示方向反转为“较早 Claim → 较新 Claim”。

例如，数据中的“新 B `EXTENDS` 旧 A”在 SVG 中显示为 A → B；箭头不表示“A 扩展 B”。机器分析应读取 `semantic_source_claim_id` / `semantic_target_claim_id`，历史路径分析应读取 `history_from_claim_id` / `history_to_claim_id`。

## 设计上的审计保障

- 输入、代码和阶段产物均使用哈希指纹，避免把不同任务或不同版本的结果混在一起。
- Claim 保留摘要中的连续逐字证据，不允许用背景知识补写缺失内容。
- 候选关系与正式关系分离；引用、语义相似或共享主题只用于召回，不自动证明关系成立。
- 全部关系候选都有决定记录，包括被拒绝的候选。
- Claim 和 Relation 使用稳定注册表，修订不会静默复用旧身份。
- 结构指标只用于诊断，不为了减少孤立点或增加长路径而强制连边。
- 默认 `data/`、运行产物、归档和本地环境不会被 Git 提交。

## 测试

维护者可运行隔离的合成集成测试：

```powershell
python -B tests/test_pipeline.py
```

测试使用系统临时目录生成合成数据，不会向项目的 `data/` 写入内容。它覆盖输入歧义、可变核心规模、引用复核、Claim 拆分、阶段失效、归档以及单篇/零边等边界情况。

## 项目结构

```text
.
├─ pipeline.py                  # 唯一推荐的正式入口
├─ pipeline/                    # 七轮实现与七个独立校验器
├─ tests/test_pipeline.py       # 隔离的合成集成测试
├─ data/                        # 本地任务输入，不提交到 Git
├─ 启动提示词.md                # 启动连续七轮 Agent 工作流
├─ PIPELINE_AGENT.md            # 完整执行规范与数据契约
├─ Claim知识谱系设计.md         # Claim、Relation 与视图语义
├─ 领域配置模板.json            # 跨领域配置模板
└─ requirements.txt
```

## 当前边界

- 默认科学输入仅限核心论文的标题、摘要和原始参考文献，不读取全文，也不联网补充科学内容。
- 流水线的自动校验主要保证格式、身份、追溯与结构一致性；`completed` 不代表图谱已经成为人工金标准。
- 关系 `confidence` 是规则评分，不是校准后的正确概率。
- 第五轮当前使用字符 2–4 gram TF-IDF 和全量相似度/范围比较，Claim 数量很大时具有近似 O(M²) 的时间和内存成本。
- 不同 Agent 在相同输入上可能产生不同但有依据的 Claim 或关系；冻结 Agent 产物、配置和代码后，可以稳定重绘图谱。
- 当前仓库尚未附带开源许可证。源码可见不等于自动授予复制、修改或分发权；如需复用，请先联系仓库维护者。

## 参与讨论

欢迎通过 GitHub Issues 反馈输入格式兼容性、领域配置、关系语义、可视化可读性和可扩展性问题。提交问题时请勿附带受版权、伦理审批或隐私限制的原始论文数据。
