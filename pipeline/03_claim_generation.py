from __future__ import annotations

import argparse
import shutil
from collections import Counter
from pathlib import Path
from typing import Any

from common import sample_paper_ids, fingerprint, prepare_task_generation, assert_task_fingerprint, DEFAULT_WORKSPACE, PROJECT_ROOT, file_sha256, read_json, read_jsonl, write_json, write_jsonl, write_text


DESIGN_DOC = PROJECT_ROOT / "Claim知识谱系设计.md"
CLAIM_TYPES = {"empirical", "interpretation", "theoretical", "methodological", "synthesis"}
CLAIM_STATUSES = {"affirmative", "absence", "no_evidence", "conditional"}
EVIDENCE_BASES = {
    "confirmatory",
    "exploratory",
    "replication",
    "simulation",
    "reanalysis",
    "literature_synthesis",
    "theoretical_argument",
}
SCOPE_FIELDS = {"population", "modality", "paradigm", "measurement_level", "conditions"}


AGENT_TASK = """# 第三轮 Agent 任务：仅根据标题和摘要生成 Claim 草稿

## 输入边界

只能读取 `task_batches/*.json` 中的 `paper_id`、`title`、`abstract` 和本目录的设计文档快照。不得读取年份、作者、引用、选择类别、旧 Claim、旧关系或旧图谱，也不得联网补充论文信息。

## 生成要求

1. 每篇保留 1–3 条最核心、最有谱系价值的 Claim；宁缺毋滥。
2. Claim 必须是本文作者承担的、完整、原子、原则上可支持或反驳的中文命题。
3. 只从结果、作者解释、理论论证或方法比较中抽取；研究目的、方法步骤和纯背景陈述不是 Claim。
4. 保留原文强度、否定和必要条件。“未发现证据”不能改写成“不存在”，相关不能增强为因果。
5. 不生成开放问题或宽泛 research gap 节点；它们容易形成无知识内容的孤点。
6. `evidence_spans` 保存 1–3 个摘要中的连续逐字片段，并共同充分支持整条 Claim。
7. `scope` 只写影响真值或跨论文比较的范围；摘要未说明则为 `null`，不得猜测。
8. `evidence_basis` 只能依据摘要明示的信息判断；不确定是否预注册时使用 `confirmatory`，不把它解释成“已预注册”。

## 输出格式

每个任务批次对应 `agent_outputs/` 中的同名 JSONL。每行一篇 Paper，保持任务顺序：

```json
{"paper_id":"P001","claims":[{"normalized_text":"完整中文命题","claim_type":"empirical","claim_status":"affirmative","scope":{"population":null,"modality":null,"paradigm":null,"measurement_level":null,"conditions":null},"evidence_basis":["confirmatory"],"evidence_spans":[{"text":"verbatim abstract text","location":"abstract"}],"selection_reason":"为何是本文核心贡献"}],"paper_notes":"可选"}
```

不要生成 `claim_id`。不要添加 Markdown 围栏或文件外解释。
示例不预设任何研究领域或范围；所有字段按本任务摘要填写，不能机械复制示例内容。
"""


def clean(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key != "__input_line__"}


def prepare(workspace: Path, batch_size: int) -> None:
    if batch_size < 1:
        raise ValueError("batch-size must be positive")
    source = workspace / "01_规范化输入" / "papers.jsonl"
    if not source.is_file() or not DESIGN_DOC.is_file():
        raise FileNotFoundError("Normalized papers or design document is missing")
    papers = [clean(row) for row in read_jsonl(source)]
    output = workspace / "03_Claim生成"
    tasks = output / "task_batches"
    agent_outputs = output / "agent_outputs"
    prepare_task_generation(output, fingerprint([source, DESIGN_DOC], batch_size))
    tasks.mkdir(parents=True, exist_ok=True)
    agent_outputs.mkdir(parents=True, exist_ok=True)

    batch_names: list[str] = []
    for start in range(0, len(papers), batch_size):
        batch_number = start // batch_size + 1
        batch_name = f"batch_{batch_number:03d}"
        batch_names.append(batch_name)
        write_json(
            tasks / f"{batch_name}.json",
            {
                "task_id": batch_name,
                "design_version": "Claim知识谱系设计",
                "content_fields_available": ["paper_id", "title", "abstract"],
                "content_fields_withheld": ["year", "authors", "doi", "selection_category", "citations", "prior_claims", "relations"],
                "papers": [
                    {"paper_id": paper["paper_id"], "title": paper["title"], "abstract": paper["abstract"]}
                    for paper in papers[start : start + batch_size]
                ],
            },
        )

    shutil.copyfile(DESIGN_DOC, output / "设计文档快照.md")
    write_text(output / "AGENT_TASK.md", AGENT_TASK)
    write_text(agent_outputs / "README.md", "# Agent 输出\n\n每个批次严格按 `../AGENT_TASK.md` 生成同名 JSONL。")
    write_json(
        output / "task_manifest.json",
        {
            "status": "agent_required",
            "paper_count": len(papers),
            "batch_size": batch_size,
            "batches": batch_names,
            "required_output_files": [f"agent_outputs/{name}.jsonl" for name in batch_names],
            "input_boundary": ["paper_id", "title", "abstract"],
            "next_command": "python 03_claim_generation.py collect",
        },
    )
    write_text(
        workspace / "STATUS.md",
        "\n".join(
            [
                "# Claim 演化知识图谱 状态",
                "",
                "状态：`agent_required`（第三轮 Claim 草稿生成）",
                "",
                f"任务批次：{len(batch_names)}；论文：{len(papers)}。",
                "",
                "严格执行：`03_Claim生成/AGENT_TASK.md`。",
            ]
        ),
    )
    print(f"Claim tasks prepared: {len(batch_names)} batches for {len(papers)} papers")


def collect(workspace: Path) -> None:
    output = workspace / "03_Claim生成"
    manifest = read_json(output / "task_manifest.json")
    assert_task_fingerprint(output, fingerprint([workspace / "01_规范化输入" / "papers.jsonl", DESIGN_DOC], manifest["batch_size"]))
    papers = [clean(row) for row in read_jsonl(workspace / "01_规范化输入" / "papers.jsonl")]
    paper_by_id = {paper["paper_id"]: paper for paper in papers}
    rows_by_paper: dict[str, dict[str, Any]] = {}
    errors: list[str] = []

    for batch_name in manifest["batches"]:
        path = output / "agent_outputs" / f"{batch_name}.jsonl"
        if not path.is_file():
            errors.append(f"Missing agent output: {path.name}")
            continue
        for loaded in read_jsonl(path):
            row = clean(loaded)
            paper_id = row.get("paper_id")
            if paper_id not in paper_by_id:
                errors.append(f"{path.name}: unknown paper_id {paper_id!r}")
            elif paper_id in rows_by_paper:
                errors.append(f"Duplicate paper output: {paper_id}")
            else:
                rows_by_paper[paper_id] = {**row, "source_batch": batch_name}

    missing = sorted(set(paper_by_id) - set(rows_by_paper))
    if missing:
        errors.append("Missing paper outputs: " + ", ".join(missing))

    drafts: list[dict[str, Any]] = []
    claims_per_paper: dict[str, int] = {}
    for paper_id in sorted(rows_by_paper):
        container = rows_by_paper[paper_id]
        paper = paper_by_id[paper_id]
        claims = container.get("claims")
        if not isinstance(claims, list) or not 1 <= len(claims) <= 3:
            errors.append(f"{paper_id}: claims must contain 1-3 items")
            continue
        claims_per_paper[paper_id] = len(claims)
        local_texts: set[str] = set()
        for number, claim in enumerate(claims, start=1):
            prefix = f"{paper_id} claim {number}"
            if not isinstance(claim, dict):
                errors.append(f"{prefix}: claim must be an object")
                continue
            normalized_text = claim.get("normalized_text")
            claim_type = claim.get("claim_type")
            claim_status = claim.get("claim_status")
            scope = claim.get("scope")
            bases = claim.get("evidence_basis")
            spans = claim.get("evidence_spans")
            reason = claim.get("selection_reason")
            if not isinstance(normalized_text, str) or not normalized_text.strip():
                errors.append(f"{prefix}: normalized_text is empty")
            elif normalized_text.strip() in local_texts:
                errors.append(f"{prefix}: duplicate normalized_text within paper")
            else:
                local_texts.add(normalized_text.strip())
            if claim_type not in CLAIM_TYPES:
                errors.append(f"{prefix}: invalid claim_type {claim_type!r}")
            if claim_status not in CLAIM_STATUSES:
                errors.append(f"{prefix}: invalid claim_status {claim_status!r}")
            if not isinstance(scope, dict) or set(scope) != SCOPE_FIELDS:
                errors.append(f"{prefix}: scope must contain exactly {sorted(SCOPE_FIELDS)}")
            elif any(value is not None and not isinstance(value, str) for value in scope.values()):
                errors.append(f"{prefix}: scope values must be string or null")
            if not isinstance(bases, list) or not bases or any(value not in EVIDENCE_BASES for value in bases):
                errors.append(f"{prefix}: invalid evidence_basis")
            if not isinstance(spans, list) or not 1 <= len(spans) <= 3:
                errors.append(f"{prefix}: evidence_spans must contain 1-3 items")
            else:
                for span_number, span in enumerate(spans, start=1):
                    if not isinstance(span, dict) or span.get("location") != "abstract" or not isinstance(span.get("text"), str):
                        errors.append(f"{prefix} span {span_number}: invalid evidence span")
                    elif not span["text"].strip() or span["text"] not in paper["abstract"]:
                        errors.append(f"{prefix} span {span_number}: text is not a verbatim abstract substring")
            if not isinstance(reason, str) or not reason.strip():
                errors.append(f"{prefix}: selection_reason is required")
            drafts.append(
                {
                    "schema_version": "claim_draft.schema1",
                    "claim_key": f"{paper_id}-D{number:02d}",
                    "paper_id": paper_id,
                    "normalized_text": normalized_text.strip() if isinstance(normalized_text, str) else normalized_text,
                    "claim_type": claim_type,
                    "claim_status": claim_status,
                    "scope": scope,
                    "evidence_basis": bases,
                    "evidence_spans": spans,
                    "selection_reason": reason.strip() if isinstance(reason, str) else reason,
                    "generation_notes": claim.get("generation_notes"),
                    "source_batch": container["source_batch"],
                }
            )

    report = {
        "report_version": "claim_generation_report.schema1",
        "agent_output_hashes": {name: file_sha256(output / "agent_outputs" / f"{name}.jsonl") for name in manifest["batches"] if (output / "agent_outputs" / f"{name}.jsonl").is_file()},
        "status": "failed" if errors else "passed",
        "expected_papers": len(papers),
        "received_papers": len(rows_by_paper),
        "draft_claims": len(drafts),
        "claims_per_paper_distribution": dict(sorted(Counter(claims_per_paper.values()).items())),
        "claim_type_distribution": dict(sorted(Counter(row["claim_type"] for row in drafts).items())),
        "claim_status_distribution": dict(sorted(Counter(row["claim_status"] for row in drafts).items())),
        "errors": errors,
        "input_boundary_verified": ["paper_id", "title", "abstract"],
    }
    if errors:
        write_json(output / "claim_generation_report.json", report)
        raise ValueError("Claim collection failed:\n- " + "\n- ".join(errors[:100]))

    write_jsonl(output / "claims_draft.jsonl", drafts)
    sample_ids = sample_paper_ids(papers)
    claims_by_paper = {
        paper_id: [row for row in drafts if row["paper_id"] == paper_id]
        for paper_id in sample_ids
    }
    inspection_sample = [
        {
            "paper_id": paper_id,
            "title": paper_by_id[paper_id]["title"],
            "abstract": paper_by_id[paper_id]["abstract"],
            "claims": claims_by_paper[paper_id],
        }
        for paper_id in sample_ids
    ]
    write_json(output / "人工抽查样本.json", inspection_sample)
    sample_lines = ["# 第三轮人工抽查样本", "", "按核心集合规模确定性抽取最多 10 篇，展示本轮可见输入、Claim 草稿和逐字证据。", ""]
    for item in inspection_sample:
        sample_lines.extend([f"## {item['paper_id']} — {item['title']}", "", f"摘要：{item['abstract']}", ""])
        for claim in item["claims"]:
            sample_lines.extend(
                [
                    f"- `{claim['claim_key']}`：{claim['normalized_text']}",
                    f"  - 类型/状态：`{claim['claim_type']}` / `{claim['claim_status']}`",
                    "  - 证据：" + " / ".join(f"“{span['text']}”" for span in claim["evidence_spans"]),
                    f"  - 选择理由：{claim['selection_reason']}",
                ]
            )
        sample_lines.append("")
    write_text(output / "人工抽查样本.md", "\n".join(sample_lines))
    report["output"] = {
        "file": "claims_draft.jsonl",
        "rows": len(drafts),
        "sha256": file_sha256(output / "claims_draft.jsonl"),
    }
    report["inspection_sample"] = {
        "paper_ids": sample_ids,
        "json_file": "人工抽查样本.json",
        "markdown_file": "人工抽查样本.md",
    }
    write_json(output / "claim_generation_report.json", report)
    write_text(
        output / "第三轮检查报告.md",
        "\n".join(
            [
                "# 第三轮：Claim 草稿生成检查报告",
                "",
                f"状态：**passed**。{len(papers)} 篇论文共生成 {len(drafts)} 条 Claim 草稿。",
                "",
                f"每篇 Claim 数分布：{dict(sorted(Counter(claims_per_paper.values()).items()))}",
                "",
                f"Claim 类型分布：{report['claim_type_distribution']}",
                "",
                f"Claim 状态分布：{report['claim_status_distribution']}",
                "",
                "人工抽查：`人工抽查样本.md`（按核心集合规模确定性抽样）。",
                "",
                "所有证据片段均已通过摘要逐字包含检查。本轮结果仍是草稿；下一轮必须进行语义复核、去复合化和稳定 ID 分配。",
            ]
        ),
    )
    write_text(
        workspace / "STATUS.md",
        "\n".join(
            [
                "# Claim 演化知识图谱 状态",
                "",
                "状态：`round_3_completed`（等待用户检查）",
                "",
                f"已完成：{len(papers)} 篇论文的 {len(drafts)} 条 Claim 草稿生成与结构校验。",
                "",
                "尚未执行：Claim 语义复核与稳定 ID、Claim 关系判断、图谱绘制。",
                "",
                "第三轮检查入口：`03_Claim生成/第三轮检查报告.md`。",
            ]
        ),
    )
    manifest["status"] = "completed"
    write_json(output / "task_manifest.json", manifest)
    print(f"Claim collection complete: {len(drafts)} drafts for {len(papers)} papers")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Round 3: prepare or collect title/abstract-only Claim generation.")
    parser.add_argument("action", choices=("prepare", "collect"))
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--workspace", type=Path, default=DEFAULT_WORKSPACE)
    args = parser.parse_args()
    if args.action == "prepare":
        prepare(args.workspace.resolve(), args.batch_size)
    else:
        collect(args.workspace.resolve())
