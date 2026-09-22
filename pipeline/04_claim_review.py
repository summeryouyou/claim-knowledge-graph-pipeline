from __future__ import annotations

import argparse
import shutil
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from common import sample_paper_ids, fingerprint, prepare_task_generation, assert_task_fingerprint, DEFAULT_WORKSPACE, PROJECT_ROOT, file_sha256, read_json, read_jsonl, write_json, write_jsonl, write_text


DESIGN_DOC = PROJECT_ROOT / "Claim知识谱系设计.md"
CLAIM_TYPES = {"empirical", "interpretation", "theoretical", "methodological", "synthesis"}
CLAIM_STATUSES = {"affirmative", "absence", "no_evidence", "conditional"}
EVIDENCE_BASES = {
    "confirmatory", "exploratory", "replication", "simulation", "reanalysis",
    "literature_synthesis", "theoretical_argument",
}
SCOPE_FIELDS = {"population", "modality", "paradigm", "measurement_level", "conditions"}
CHANGE_ACTIONS = {"edit", "split", "merge", "drop"}
CLAIM_FIELDS = (
    "normalized_text", "claim_type", "claim_status", "scope", "evidence_basis",
    "evidence_spans", "selection_reason",
)


AGENT_TASK = """# 第四轮 Agent 任务：Claim 独立语义复核

## 输入边界

只能读取 `task_batches/*.json` 中的标题、摘要和第三轮 Claim 草案，以及本目录的设计文档快照。不得读取年份、引用关系、旧版 Claim、旧关系或图谱，也不得联网补充信息。

## 复核目标

逐条确认 Claim 是否为作者承担的、完整、原子、可支持或反驳的命题，并核对类型、逻辑状态、范围、证据依据和逐字证据。特别注意：

1. `conditional` 仅表示效应只在关键条件下成立，不表示作者语气较弱；“可能/提示”一般仍可为 `affirmative`。
2. `no_evidence` 表示本研究没检测到证据，不等于作者主张效应不存在。
3. 相关不能增强为因果，研究目的或假设不能作为结果证据。
4. 一个命题混合两个可独立判断的结果时应拆分；同篇重复命题可合并；越界或无实质内容的命题应删除。
5. 可恢复第三轮遗漏、但摘要明确支持且对论文核心贡献必要的命题；必须记录其来源草案键和逐字证据。
6. 不判断跨论文 Claim 关系，不查看论文年份或引用。

## 输出

每个任务批次对应 `agent_outputs/` 下同名 JSON。`reviewed_claim_keys` 必须逐项列出任务中的全部草案键。未出现在 `changes` 中的草案视为复核后接受；这不是自动接受，必须实际阅读后再列入。

```json
{
  "task_id": "batch_001",
  "reviewed_claim_keys": ["P001-D01", "P001-D02"],
  "changes": [
    {
      "source_claim_keys": ["P001-D01"],
      "action": "edit",
      "reason": "状态定义修正",
      "replacement_claims": [
        {
          "review_claim_key": "P001-R01",
          "normalized_text": "完整中文命题",
          "claim_type": "empirical",
          "claim_status": "affirmative",
          "scope": {"population": null, "modality": null, "paradigm": null, "measurement_level": null, "conditions": null},
          "evidence_basis": ["confirmatory"],
          "evidence_spans": [{"text": "verbatim abstract text", "location": "abstract"}],
          "selection_reason": "为何保留"
        }
      ]
    }
  ],
  "batch_notes": "简要说明"
}
```

`edit` 必须有 1 个替代命题；`split` 至少 2 个；`merge` 至少 2 个来源且有 1 个替代命题；`drop` 不得有替代命题。`review_claim_key` 在全项目唯一，后续用于保持稳定 ID。
"""


def clean(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key != "__input_line__"}


def review_view(draft: dict[str, Any]) -> dict[str, Any]:
    return {"claim_key": draft["claim_key"], **{field: draft[field] for field in CLAIM_FIELDS}}


def prepare(workspace: Path) -> None:
    round3 = workspace / "03_Claim生成"
    if not (round3 / "claims_draft.jsonl").is_file():
        raise FileNotFoundError("Round-3 Claim drafts are missing")
    drafts = [clean(row) for row in read_jsonl(round3 / "claims_draft.jsonl")]
    drafts_by_batch: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for draft in drafts:
        drafts_by_batch[draft["source_batch"]].append(draft)

    output = workspace / "04_Claim复核"
    tasks = output / "task_batches"
    agent_outputs = output / "agent_outputs"
    source_manifest = read_json(round3 / "task_manifest.json")
    signature_paths = [round3 / "claims_draft.jsonl", DESIGN_DOC] + [round3 / "task_batches" / f"{name}.json" for name in source_manifest["batches"]]
    prepare_task_generation(output, fingerprint(signature_paths))
    tasks.mkdir(parents=True, exist_ok=True)
    agent_outputs.mkdir(parents=True, exist_ok=True)
    batch_names: list[str] = []
    for source_name in source_manifest["batches"]:
        source_path = round3 / "task_batches" / f"{source_name}.json"
        source_task = read_json(source_path)
        batch_name = source_task["task_id"]
        batch_names.append(batch_name)
        claims_by_paper: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for draft in drafts_by_batch[batch_name]:
            claims_by_paper[draft["paper_id"]].append(review_view(draft))
        write_json(
            tasks / f"{batch_name}.json",
            {
                "task_id": batch_name,
                "content_fields_available": ["paper_id", "title", "abstract", "claim_drafts"],
                "content_fields_withheld": ["year", "authors", "doi", "citations", "prior_relations", "prior_graph"],
                "papers": [
                    {
                        "paper_id": paper["paper_id"],
                        "title": paper["title"],
                        "abstract": paper["abstract"],
                        "claim_drafts": claims_by_paper[paper["paper_id"]],
                    }
                    for paper in source_task["papers"]
                ],
            },
        )

    shutil.copyfile(DESIGN_DOC, output / "设计文档快照.md")
    write_text(output / "AGENT_TASK.md", AGENT_TASK)
    write_text(output / "agent_outputs" / "README.md", "# Agent 输出\n\n逐批完成独立语义复核，格式见 `../AGENT_TASK.md`。")
    write_json(
        output / "task_manifest.json",
        {
            "status": "agent_required",
            "source_claims": len(drafts),
            "batches": batch_names,
            "required_output_files": [f"agent_outputs/{name}.json" for name in batch_names],
            "input_boundary": ["paper_id", "title", "abstract", "claim_drafts"],
            "next_command": "python 04_claim_review.py collect",
        },
    )
    write_text(
        workspace / "STATUS.md",
        "# Claim 演化知识图谱 状态\n\n状态：`agent_required`（第四轮 Claim 独立语义复核）\n\n"
        f"待复核：{len(drafts)} 条草案，{len(batch_names)} 个批次。\n",
    )
    print(f"Claim review tasks prepared: {len(batch_names)} batches, {len(drafts)} drafts")


def validate_claim(claim: dict[str, Any], paper: dict[str, Any], prefix: str, errors: list[str]) -> None:
    if not isinstance(claim.get("review_claim_key"), str) or not claim["review_claim_key"].strip():
        errors.append(f"{prefix}: review_claim_key is required")
    if not isinstance(claim.get("normalized_text"), str) or not claim["normalized_text"].strip():
        errors.append(f"{prefix}: normalized_text is required")
    if claim.get("claim_type") not in CLAIM_TYPES:
        errors.append(f"{prefix}: invalid claim_type")
    if claim.get("claim_status") not in CLAIM_STATUSES:
        errors.append(f"{prefix}: invalid claim_status")
    scope = claim.get("scope")
    if not isinstance(scope, dict) or set(scope) != SCOPE_FIELDS:
        errors.append(f"{prefix}: scope fields are invalid")
    elif any(value is not None and not isinstance(value, str) for value in scope.values()):
        errors.append(f"{prefix}: scope values must be string or null")
    bases = claim.get("evidence_basis")
    if not isinstance(bases, list) or not bases or any(value not in EVIDENCE_BASES for value in bases):
        errors.append(f"{prefix}: evidence_basis is invalid")
    spans = claim.get("evidence_spans")
    if not isinstance(spans, list) or not 1 <= len(spans) <= 3:
        errors.append(f"{prefix}: evidence_spans must contain 1-3 items")
    else:
        for index, span in enumerate(spans, start=1):
            if not isinstance(span, dict) or span.get("location") != "abstract" or not isinstance(span.get("text"), str):
                errors.append(f"{prefix} span {index}: invalid evidence span")
            elif not span["text"].strip() or span["text"] not in paper["abstract"]:
                errors.append(f"{prefix} span {index}: evidence is not a verbatim abstract substring")
    if not isinstance(claim.get("selection_reason"), str) or not claim["selection_reason"].strip():
        errors.append(f"{prefix}: selection_reason is required")


def collect(workspace: Path) -> None:
    output = workspace / "04_Claim复核"
    manifest = read_json(output / "task_manifest.json")
    round3 = workspace / "03_Claim生成"
    source_manifest = read_json(round3 / "task_manifest.json")
    signature_paths = [round3 / "claims_draft.jsonl", DESIGN_DOC] + [round3 / "task_batches" / f"{name}.json" for name in source_manifest["batches"]]
    assert_task_fingerprint(output, fingerprint(signature_paths))
    source_drafts = [clean(row) for row in read_jsonl(workspace / "03_Claim生成" / "claims_draft.jsonl")]
    source_by_key = {row["claim_key"]: row for row in source_drafts}
    papers = [clean(row) for row in read_jsonl(workspace / "01_规范化输入" / "papers.jsonl")]
    paper_by_id = {row["paper_id"]: row for row in papers}
    errors: list[str] = []
    reviewed: set[str] = set()
    changes_by_source: dict[str, dict[str, Any]] = {}
    action_counts: Counter[str] = Counter()

    for batch_name in manifest["batches"]:
        task = read_json(output / "task_batches" / f"{batch_name}.json")
        expected_keys = [claim["claim_key"] for paper in task["papers"] for claim in paper["claim_drafts"]]
        path = output / "agent_outputs" / f"{batch_name}.json"
        if not path.is_file():
            errors.append(f"Missing review output: {path.name}")
            continue
        decision = read_json(path)
        if decision.get("task_id") != batch_name:
            errors.append(f"{path.name}: task_id mismatch")
        actual_keys = decision.get("reviewed_claim_keys")
        if not isinstance(actual_keys, list) or actual_keys != expected_keys:
            errors.append(f"{path.name}: reviewed_claim_keys must exactly match task order")
            continue
        reviewed.update(actual_keys)
        for change_number, change in enumerate(decision.get("changes", []), start=1):
            prefix = f"{path.name} change {change_number}"
            sources = change.get("source_claim_keys")
            action = change.get("action")
            replacements = change.get("replacement_claims")
            if not isinstance(sources, list) or not sources or any(key not in expected_keys for key in sources):
                errors.append(f"{prefix}: invalid source_claim_keys")
                continue
            if len(sources) != len(set(sources)) or (action in {"edit", "split", "drop"} and len(sources) != 1):
                errors.append(f"{prefix}: source count does not match action")
            if any(key in changes_by_source for key in sources):
                errors.append(f"{prefix}: source claim changed more than once")
            if action not in CHANGE_ACTIONS:
                errors.append(f"{prefix}: invalid action")
                continue
            if not isinstance(change.get("reason"), str) or not change["reason"].strip():
                errors.append(f"{prefix}: reason is required")
            if not isinstance(replacements, list):
                errors.append(f"{prefix}: replacement_claims must be a list")
                continue
            if (action == "edit" and len(replacements) != 1) or (action == "split" and len(replacements) < 2):
                errors.append(f"{prefix}: replacement count does not match action")
            if (action == "merge" and (len(sources) < 2 or len(replacements) != 1)) or (action == "drop" and replacements):
                errors.append(f"{prefix}: replacement/source count does not match action")
            paper_ids = {source_by_key[key]["paper_id"] for key in sources if key in source_by_key}
            if len(paper_ids) != 1:
                errors.append(f"{prefix}: a change cannot cross papers")
                continue
            paper = paper_by_id[next(iter(paper_ids))]
            for replacement_number, replacement in enumerate(replacements, start=1):
                validate_claim(replacement, paper, f"{prefix} replacement {replacement_number}", errors)
            for key in sources:
                changes_by_source[key] = change
            action_counts[action] += 1

    if reviewed != set(source_by_key):
        errors.append("Review coverage does not exactly match all source Claim drafts")
    if errors:
        write_json(output / "claim_review_report.json", {"status": "failed", "errors": errors})
        raise ValueError("Claim review collection failed:\n- " + "\n- ".join(errors[:100]))

    final_candidates: list[dict[str, Any]] = []
    emitted_changes: set[int] = set()
    for source in source_drafts:
        key = source["claim_key"]
        change = changes_by_source.get(key)
        if change is None:
            final_candidates.append(
                {
                    "review_claim_key": key,
                    "paper_id": source["paper_id"],
                    **{field: source[field] for field in CLAIM_FIELDS},
                    "source_claim_keys": [key],
                    "review_action": "accept",
                    "review_reason": "独立复核通过：命题与摘要证据一致，无需修改。",
                }
            )
            action_counts["accept"] += 1
            continue
        marker = id(change)
        if marker in emitted_changes:
            continue
        emitted_changes.add(marker)
        paper_id = source_by_key[change["source_claim_keys"][0]]["paper_id"]
        for replacement in change["replacement_claims"]:
            final_candidates.append(
                {
                    "review_claim_key": replacement["review_claim_key"],
                    "paper_id": paper_id,
                    **{field: replacement[field] for field in CLAIM_FIELDS},
                    "source_claim_keys": change["source_claim_keys"],
                    "review_action": change["action"],
                    "review_reason": change["reason"],
                }
            )

    final_keys = [row["review_claim_key"] for row in final_candidates]
    if len(final_keys) != len(set(final_keys)):
        raise ValueError("Duplicate review_claim_key in final candidates")
    text_keys = [(row["paper_id"], row["normalized_text"].strip()) for row in final_candidates]
    if len(text_keys) != len(set(text_keys)):
        raise ValueError("Duplicate final Claim text within a paper; merge or revise review decisions")
    if {row["paper_id"] for row in final_candidates} != set(paper_by_id):
        raise ValueError("Each core paper must retain a supported Claim; review dropped-paper decisions")
    registry_path = output / "claim_id_registry.json"
    registry = read_json(registry_path) if registry_path.is_file() else {"schema_version": "claim_id_registry.schema1", "next_number": 1, "assignments": {}}
    for entry in registry["assignments"].values():
        entry["active"] = False
    for key in sorted(final_keys):
        if key not in registry["assignments"]:
            number = registry["next_number"]
            registry["assignments"][key] = {"claim_id": f"C{number:03d}", "active": True}
            registry["next_number"] = number + 1
        else:
            registry["assignments"][key]["active"] = True
    write_json(registry_path, registry)

    final_claims = []
    for candidate in final_candidates:
        claim_id = registry["assignments"][candidate["review_claim_key"]]["claim_id"]
        final_claims.append({"schema_version": "claim.schema1", "claim_id": claim_id, **candidate})
    final_claims.sort(key=lambda row: int(row["claim_id"][1:]))
    write_jsonl(output / "claims.jsonl", final_claims)

    claims_by_paper: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for claim in final_claims:
        claims_by_paper[claim["paper_id"]].append(claim)
    sample_ids = sample_paper_ids(papers)
    sample = [{"paper_id": paper_id, "title": paper_by_id[paper_id]["title"], "claims": claims_by_paper[paper_id]} for paper_id in sample_ids]
    write_json(output / "人工抽查样本.json", sample)
    lines = ["# 第四轮人工抽查样本", "", "包含接受、修改与拆分案例；可据此检查最终命题和稳定 ID。", ""]
    for item in sample:
        lines.extend([f"## {item['paper_id']} — {item['title']}", ""])
        for claim in item["claims"]:
            lines.extend([
                f"- `{claim['claim_id']}`：{claim['normalized_text']}",
                f"  - 复核：`{claim['review_action']}`；来源：{', '.join(claim['source_claim_keys'])}",
                f"  - 类型/状态：`{claim['claim_type']}` / `{claim['claim_status']}`",
                f"  - 理由：{claim['review_reason']}",
            ])
        lines.append("")
    write_text(output / "人工抽查样本.md", "\n".join(lines))

    report = {
        "report_version": "claim_review_report.schema1",
        "agent_output_hashes": {name: file_sha256(output / "agent_outputs" / f"{name}.json") for name in manifest["batches"]},
        "status": "passed",
        "source_drafts": len(source_drafts),
        "reviewed_drafts": len(reviewed),
        "final_claims": len(final_claims),
        "review_action_distribution": dict(sorted(action_counts.items())),
        "claim_type_distribution": dict(sorted(Counter(row["claim_type"] for row in final_claims).items())),
        "claim_status_distribution": dict(sorted(Counter(row["claim_status"] for row in final_claims).items())),
        "papers_with_claims": len(claims_by_paper),
        "input_boundary": ["paper_id", "title", "abstract", "claim_drafts"],
        "output": {"file": "claims.jsonl", "rows": len(final_claims), "sha256": file_sha256(output / "claims.jsonl")},
        "id_registry": {"file": "claim_id_registry.json", "active": len(final_claims), "next_number": registry["next_number"]},
        "inspection_sample": {"paper_ids": sample_ids, "markdown_file": "人工抽查样本.md"},
        "errors": [],
    }
    write_json(output / "claim_review_report.json", report)
    write_text(
        output / "第四轮检查报告.md",
        "\n".join([
            "# 第四轮：Claim 独立语义复核检查报告", "",
            f"状态：**passed**。已复核 {len(reviewed)} 条草案，形成 {len(final_claims)} 条带稳定 ID 的 Claim。", "",
            f"复核动作分布：{report['review_action_distribution']}", "",
            f"类型分布：{report['claim_type_distribution']}", "",
            f"逻辑状态分布：{report['claim_status_distribution']}", "",
            "所有最终 Claim 均保留摘要逐字证据；本轮没有读取年份、引用或既有关系，也没有判断跨论文关系。", "",
            "人工抽查：`人工抽查样本.md`。稳定 ID 注册表：`claim_id_registry.json`。",
        ]),
    )
    write_text(
        workspace / "STATUS.md",
        "# Claim 演化知识图谱 状态\n\n状态：`round_4_completed`（等待用户检查）\n\n"
        f"已完成：{len(source_drafts)} 条草案的独立语义复核，形成 {len(final_claims)} 条稳定 Claim。\n\n"
        "尚未执行：Claim 关系候选生成与判断、图谱绘制。\n\n"
        "第四轮检查入口：`04_Claim复核/第四轮检查报告.md`。\n",
    )
    manifest["status"] = "completed"
    write_json(output / "task_manifest.json", manifest)
    print(f"Claim review complete: {len(source_drafts)} drafts -> {len(final_claims)} final Claims")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Round 4: prepare or collect independent Claim review.")
    parser.add_argument("action", choices=("prepare", "collect"))
    parser.add_argument("--workspace", type=Path, default=DEFAULT_WORKSPACE)
    args = parser.parse_args()
    if args.action == "prepare":
        prepare(args.workspace.resolve())
    else:
        collect(args.workspace.resolve())
