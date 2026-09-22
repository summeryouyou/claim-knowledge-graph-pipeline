from __future__ import annotations

import argparse
import math
from collections import Counter
from pathlib import Path
from typing import Any

from common import load_domain_profile, prune_generated_batches, DEFAULT_WORKSPACE, file_sha256, read_jsonl, write_json, write_jsonl, write_text


RELATION_TYPES = {"SUPPORTS", "EXTENDS", "QUALIFIES", "CHALLENGES", "ALTERNATIVE_TO", "MECHANISM_FOR"}
CONCEPTUAL_TYPES = {"interpretation", "theoretical", "synthesis"}
NEGATIVE_STATUSES = {"absence", "no_evidence"}
TOPIC_TERMS: tuple[str, ...] = ()
GENERIC_TOPICS: set[str] = set()
FREQUENCY_TOPICS: set[str] = set()
MECHANISM_CUES = ("机制", "通过", "实现", "介导", "组织", "协调", "分配", "产生", "驱动", "传播", "同步", "反映", "表明", "提示", "解释")
CHALLENGE_CUES = ("未发现", "无证据", "未显示", "不能", "无法", "假阳性", "反对", "未改变", "不存在")


def clean(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key != "__input_line__"}


def claim_number(claim_id: str) -> int:
    return int(claim_id[1:])


def topics(text: str) -> set[str]:
    folded = text.casefold()
    return {term for term in TOPIC_TERMS if term in folded}


def scope_overlap(claim_a: dict[str, Any], claim_b: dict[str, Any], semantic_score: float) -> str:
    exact = [
        field for field in ("population", "modality", "paradigm", "measurement_level", "conditions")
        if claim_a["scope"].get(field) is not None
        and str(claim_a["scope"][field]).casefold() == str(claim_b["scope"].get(field)).casefold()
    ]
    if semantic_score >= 0.30 or {"modality", "paradigm"} <= set(exact):
        return "high"
    if semantic_score >= 0.18 or "modality" in exact or len(exact) >= 2:
        return "medium"
    return "low"


def orient(
    candidate: dict[str, Any], claim_1: dict[str, Any], claim_2: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any], str] | None:
    year_1, year_2 = candidate["year_1"], candidate["year_2"]
    if year_1 != year_2:
        return (claim_1, claim_2, "publication_year") if year_1 > year_2 else (claim_2, claim_1, "publication_year")
    if claim_1["paper_id"] != claim_2["paper_id"]:
        directions = candidate["citation_directions"]
        if not directions:
            return None
        citing_paper = directions[0].split("->", 1)[0]
        return (claim_1, claim_2, "same_year_citation") if claim_1["paper_id"] == citing_paper else (claim_2, claim_1, "same_year_citation")

    if claim_1["claim_status"] == "conditional" and claim_2["claim_status"] in NEGATIVE_STATUSES:
        return claim_1, claim_2, "same_paper_argument"
    if claim_2["claim_status"] == "conditional" and claim_1["claim_status"] in NEGATIVE_STATUSES:
        return claim_2, claim_1, "same_paper_argument"
    if claim_1["claim_type"] in CONCEPTUAL_TYPES and claim_2["claim_type"] not in CONCEPTUAL_TYPES:
        return claim_1, claim_2, "same_paper_argument"
    if claim_2["claim_type"] in CONCEPTUAL_TYPES and claim_1["claim_type"] not in CONCEPTUAL_TYPES:
        return claim_2, claim_1, "same_paper_argument"
    if claim_1["claim_type"] == "methodological" and claim_2["claim_type"] == "empirical":
        return claim_1, claim_2, "same_paper_argument"
    if claim_2["claim_type"] == "methodological" and claim_1["claim_type"] == "empirical":
        return claim_2, claim_1, "same_paper_argument"
    if claim_1["claim_type"] in CONCEPTUAL_TYPES and claim_2["claim_type"] in CONCEPTUAL_TYPES:
        return (claim_1, claim_2, "same_paper_argument") if claim_number(claim_1["claim_id"]) > claim_number(claim_2["claim_id"]) else (claim_2, claim_1, "same_paper_argument")
    if (claim_1["claim_status"] in NEGATIVE_STATUSES) != (claim_2["claim_status"] in NEGATIVE_STATUSES):
        negative = claim_1 if claim_1["claim_status"] in NEGATIVE_STATUSES else claim_2
        positive = claim_2 if negative is claim_1 else claim_1
        return negative, positive, "same_paper_argument"
    return None


def relation_type(source: dict[str, Any], target: dict[str, Any], overlap: str) -> str:
    source_text = source["normalized_text"].casefold()
    target_text = target["normalized_text"].casefold()
    same_paper = source["paper_id"] == target["paper_id"]
    if source["claim_status"] in NEGATIVE_STATUSES and target["claim_status"] not in NEGATIVE_STATUSES:
        return "SUPPORTS" if same_paper else "CHALLENGES"
    if any(cue in source_text for cue in CHALLENGE_CUES) and target["claim_status"] == "affirmative":
        return "CHALLENGES"
    if source["claim_type"] in CONCEPTUAL_TYPES and any(cue in source_text for cue in MECHANISM_CUES):
        return "MECHANISM_FOR"
    source_modality = source["scope"].get("modality")
    target_modality = target["scope"].get("modality")
    source_measure = source["scope"].get("measurement_level")
    target_measure = target["scope"].get("measurement_level")
    if target["claim_type"] == "methodological" and source["claim_type"] != "methodological":
        return "EXTENDS"
    if source_modality and target_modality and source_modality != target_modality:
        return "EXTENDS"
    shared = topics(source_text) & topics(target_text)
    if source["claim_status"] == "conditional" and target["claim_status"] != "conditional" and (overlap == "high" or len(shared - GENERIC_TOPICS) >= 2):
        return "QUALIFIES"
    if source["claim_type"] == "empirical" and target["claim_type"] in CONCEPTUAL_TYPES:
        return "SUPPORTS" if overlap == "high" else "EXTENDS"
    if overlap != "high" and (source_modality != target_modality or source_measure != target_measure):
        return "EXTENDS"
    return "SUPPORTS"


def confidence(candidate: dict[str, Any], overlap: str, same_paper: bool) -> float:
    signals = set(candidate["candidate_signals"])
    value = 0.42 + 0.65 * float(candidate["semantic_score"])
    value += 0.07 if "citation_1hop" in signals else 0.0
    value += 0.04 if "citation_2hop" in signals else 0.0
    value += 0.04 if "scope_neighbor" in signals else 0.0
    value += 0.04 if len(signals) >= 3 else 0.0
    value += 0.10 if same_paper else 0.0
    value += 0.04 if overlap == "high" else (0.02 if overlap == "medium" else 0.0)
    return round(min(0.94, value), 2)


def is_comparable(candidate: dict[str, Any], claim_1: dict[str, Any], claim_2: dict[str, Any]) -> tuple[bool, set[str]]:
    score = float(candidate["semantic_score"])
    signals = set(candidate["candidate_signals"])
    shared = topics(claim_1["normalized_text"]) & topics(claim_2["normalized_text"])
    meaningful = shared - GENERIC_TOPICS
    same_paper = claim_1["paper_id"] == claim_2["paper_id"]
    if same_paper:
        cross_type = (claim_1["claim_type"] in CONCEPTUAL_TYPES) != (claim_2["claim_type"] in CONCEPTUAL_TYPES)
        negative_contrast = (claim_1["claim_status"] in NEGATIVE_STATUSES) != (claim_2["claim_status"] in NEGATIVE_STATUSES)
        conceptual_pair = claim_1["claim_type"] in CONCEPTUAL_TYPES and claim_2["claim_type"] in CONCEPTUAL_TYPES
        return (cross_type or negative_contrast or (conceptual_pair and score >= 0.18) or score >= 0.35), shared
    if not shared and score < 0.26:
        return False, shared
    if not meaningful and score < 0.23:
        return False, shared
    if score < 0.23:
        shared_frequencies = shared & FREQUENCY_TOPICS
        shared_specific = meaningful - FREQUENCY_TOPICS
        if (not shared_frequencies and len(shared_specific) < 2) or (shared_frequencies and not shared_specific):
            return False, shared
    comparable = (
        score >= 0.20
        or (score >= 0.16 and len(signals) >= 2)
        or (score >= 0.14 and "citation_1hop" in signals and "semantic_topk" in signals)
    )
    return comparable, shared


def directness(source: dict[str, Any], target: dict[str, Any], same_paper: bool) -> str:
    if not same_paper or source["claim_type"] not in CONCEPTUAL_TYPES:
        return "indirect"
    evidence = " ".join(span["text"] for span in source["evidence_spans"]).casefold()
    return "direct" if any(cue in evidence for cue in ("suggest", "indicate", "consistent", "support", "propose", "imply")) else "indirect"


def relation_decision(candidate: dict[str, Any], claim_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    claim_1, claim_2 = claim_by_id[candidate["claim_id_1"]], claim_by_id[candidate["claim_id_2"]]
    oriented = orient(candidate, claim_1, claim_2)
    comparable, shared = is_comparable(candidate, claim_1, claim_2)
    if oriented is None:
        return {
            "candidate_id": candidate["candidate_id"], "judgment": "unrelated", "annotation_status": "rejected",
            "reason": "同年跨论文候选缺少可验证的引用或知识依赖方向，不能强行建立有向演化关系。",
            "judgment_method": "rule_assisted_semantic_review",
        }
    if not comparable:
        return {
            "candidate_id": candidate["candidate_id"], "judgment": "unrelated", "annotation_status": "rejected",
            "reason": "候选仅有主题、引用或弱范围邻近，无法说明一个Claim如何改变另一个Claim。",
            "judgment_method": "rule_assisted_semantic_review",
        }

    source, target, orientation_basis = oriented
    overlap = scope_overlap(source, target, float(candidate["semantic_score"]))
    rel_type = relation_type(source, target, overlap)
    conf = confidence(candidate, overlap, source["paper_id"] == target["paper_id"])
    status = "accepted" if conf >= 0.82 else "candidate"
    shared_text = "、".join(sorted(shared)) or "核心对象"
    return {
        "candidate_id": candidate["candidate_id"],
        "judgment": "relation",
        "source_claim_id": source["claim_id"],
        "target_claim_id": target["claim_id"],
        "relation_type": rel_type,
        "directness": directness(source, target, source["paper_id"] == target["paper_id"]),
        "scope_overlap": overlap,
        "confidence": conf,
        "annotation_status": status,
        "orientation_basis": orientation_basis,
        "reason": f"两端可比较：共享{shared_text}；来源Claim对目标Claim的主要知识作用判为{rel_type}。",
        "judgment_method": "rule_assisted_semantic_review",
    }


def run(workspace: Path, batch_size: int) -> None:
    if batch_size < 1:
        raise ValueError("batch-size must be positive")
    global TOPIC_TERMS, GENERIC_TOPICS, FREQUENCY_TOPICS, MECHANISM_CUES, CHALLENGE_CUES
    profile = load_domain_profile(workspace)
    TOPIC_TERMS = tuple(profile["topic_terms"])
    GENERIC_TOPICS = set(profile["generic_topics"])
    FREQUENCY_TOPICS = set(profile["frequency_topics"])
    MECHANISM_CUES = tuple(profile["mechanism_cues"])
    CHALLENGE_CUES = tuple(profile["challenge_cues"])
    claims = [clean(row) for row in read_jsonl(workspace / "04_Claim复核" / "claims.jsonl")]
    candidates = [clean(row) for row in read_jsonl(workspace / "05_关系候选" / "relation_candidates.jsonl")]
    papers = [clean(row) for row in read_jsonl(workspace / "01_规范化输入" / "papers.jsonl")]
    claim_by_id = {row["claim_id"]: row for row in claims}
    paper_by_id = {row["paper_id"]: row for row in papers}
    candidate_by_id = {row["candidate_id"]: row for row in candidates}

    decisions = [relation_decision(candidate, claim_by_id) for candidate in candidates]
    decision_by_id = {row["candidate_id"]: row for row in decisions}

    output = workspace / "06_关系判断"
    tasks = output / "review_batches"
    tasks.mkdir(parents=True, exist_ok=True)
    write_text(
        output / "AGENT_REVIEW.md",
        "# 第六轮关系复核\n\n`review_batches/` 保存两端Claim、召回信号和当前逐对判断。"
        "任何Agent均可逐批复核，并把修订写入 `relation_overrides.jsonl` 后重新运行本脚本。"
        "不得因主题相似或引用事实本身建立关系；必须说明来源Claim对目标Claim的知识作用。",
    )
    overrides_path = output / "relation_overrides.jsonl"
    if not overrides_path.exists():
        write_text(overrides_path, "")
    overrides = [clean(row) for row in read_jsonl(overrides_path)]
    override_ids = [row.get("candidate_id") for row in overrides]
    if len(set(override_ids)) != len(override_ids):
        raise ValueError("Duplicate Agent override candidate_id")
    for override in overrides:
        candidate_id = override.get("candidate_id")
        if candidate_id not in decision_by_id:
            raise ValueError(f"Unknown override candidate_id: {candidate_id}")
        decision_by_id[candidate_id] = override
    decisions = [decision_by_id[candidate["candidate_id"]] for candidate in candidates]

    prune_generated_batches(tasks, {f"batch_{i + 1:03d}.json" for i in range(math.ceil(len(candidates) / batch_size))})
    for start in range(0, len(candidates), batch_size):
        batch_number = start // batch_size + 1
        rows = []
        for candidate in candidates[start:start + batch_size]:
            claim_1, claim_2 = claim_by_id[candidate["claim_id_1"]], claim_by_id[candidate["claim_id_2"]]
            rows.append({
                "candidate": candidate,
                "claim_1": {"claim_id": claim_1["claim_id"], "paper_id": claim_1["paper_id"], "year": int(paper_by_id[claim_1["paper_id"]]["year"]), "normalized_text": claim_1["normalized_text"], "claim_type": claim_1["claim_type"], "claim_status": claim_1["claim_status"], "scope": claim_1["scope"]},
                "claim_2": {"claim_id": claim_2["claim_id"], "paper_id": claim_2["paper_id"], "year": int(paper_by_id[claim_2["paper_id"]]["year"]), "normalized_text": claim_2["normalized_text"], "claim_type": claim_2["claim_type"], "claim_status": claim_2["claim_status"], "scope": claim_2["scope"]},
                "current_decision": decision_by_id[candidate["candidate_id"]],
            })
        write_json(tasks / f"batch_{batch_number:03d}.json", {"batch_id": f"batch_{batch_number:03d}", "rows": rows})

    write_jsonl(output / "relation_decisions.jsonl", decisions)
    retained = [row for row in decisions if row["judgment"] == "relation"]
    registry_path = output / "relation_id_registry.json"
    if registry_path.exists():
        import json
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
    else:
        registry = {"schema_version": "relation_id_registry.schema1", "next_number": 1, "assignments": {}}
    for entry in registry["assignments"].values():
        entry["active"] = False
    for decision in retained:
        key = decision["candidate_id"]
        if key not in registry["assignments"]:
            number = registry["next_number"]
            registry["assignments"][key] = {"relation_id": f"R{number:04d}", "active": True}
            registry["next_number"] = number + 1
        else:
            registry["assignments"][key]["active"] = True
    write_json(registry_path, registry)

    relations = []
    for decision in retained:
        candidate = candidate_by_id[decision["candidate_id"]]
        relations.append({
            "schema_version": "claim_relation.schema1",
            "relation_id": registry["assignments"][decision["candidate_id"]]["relation_id"],
            **{key: value for key, value in decision.items() if key not in {"judgment", "judgment_method"}},
            "candidate_signals": candidate["candidate_signals"],
            "semantic_score": candidate["semantic_score"],
        })
    relations.sort(key=lambda row: int(row["relation_id"][1:]))
    write_jsonl(output / "relations.jsonl", relations)
    accepted = [row for row in relations if row["annotation_status"] == "accepted"]
    provisional = [row for row in relations if row["annotation_status"] == "candidate"]
    write_jsonl(output / "accepted_relations.jsonl", accepted)
    write_jsonl(output / "provisional_relations.jsonl", provisional)

    all_degree: Counter[str] = Counter()
    accepted_degree: Counter[str] = Counter()
    for relation in relations:
        all_degree[relation["source_claim_id"]] += 1
        all_degree[relation["target_claim_id"]] += 1
        if relation["annotation_status"] == "accepted":
            accepted_degree[relation["source_claim_id"]] += 1
            accepted_degree[relation["target_claim_id"]] += 1
    type_counts = Counter(row["relation_type"] for row in relations)
    status_counts = Counter(row["annotation_status"] for row in relations)
    isolated_retained = sorted(set(claim_by_id) - set(all_degree), key=claim_number)
    isolated_accepted = sorted(set(claim_by_id) - set(accepted_degree), key=claim_number)
    report = {
        "report_version": "relation_judgment_report.schema1",
        "status": "passed",
        "candidate_pairs_reviewed": len(decisions),
        "relations_retained": len(relations),
        "accepted_relations": len(accepted),
        "provisional_relations": len(provisional),
        "unrelated_pairs": sum(row["judgment"] == "unrelated" for row in decisions),
        "relation_type_distribution": dict(sorted(type_counts.items())),
        "annotation_status_distribution": dict(sorted(status_counts.items())),
        "claims_with_any_retained_relation": len(all_degree),
        "isolated_claims_in_retained_layer": len(isolated_retained),
        "isolated_claim_ids_retained_layer": isolated_retained,
        "claims_with_accepted_relation": len(accepted_degree),
        "isolated_claims_in_accepted_layer": len(isolated_accepted),
        "isolated_claim_ids_accepted_layer": isolated_accepted,
        "decision_method": f"rule-assisted semantic review plus {len(overrides)} auditable Agent overrides",
        "relation_taxonomy": sorted(RELATION_TYPES),
        "outputs": {
            "decisions": {"file": "relation_decisions.jsonl", "rows": len(decisions), "sha256": file_sha256(output / "relation_decisions.jsonl")},
            "relations": {"file": "relations.jsonl", "rows": len(relations), "sha256": file_sha256(output / "relations.jsonl")},
            "accepted": {"file": "accepted_relations.jsonl", "rows": len(accepted), "sha256": file_sha256(output / "accepted_relations.jsonl")},
            "provisional": {"file": "provisional_relations.jsonl", "rows": len(provisional), "sha256": file_sha256(output / "provisional_relations.jsonl")},
        },
        "review_batches": math.ceil(len(candidates) / batch_size),
        "override_rows": len(overrides),
        "warning": "Provisional relations remain visible as low-confidence dashed edges and should not be treated as accepted facts.",
    }
    write_json(output / "relation_judgment_report.json", report)

    samples = []
    for relation_type_name in sorted(RELATION_TYPES):
        matches = sorted((row for row in relations if row["relation_type"] == relation_type_name), key=lambda row: (-row["confidence"], row["relation_id"]))[:4]
        samples.extend(matches)
    samples.extend(sorted(provisional, key=lambda row: (row["confidence"], row["relation_id"]))[:6])
    write_json(output / "人工抽查样本.json", samples)
    required_ids = {row["candidate_id"] for row in samples}
    for claim_id in isolated_retained:
        adjacent = [row for row in candidates if claim_id in {row["claim_id_1"], row["claim_id_2"]}]
        adjacent.sort(key=lambda row: (-row["semantic_score"], row["candidate_id"]))
        required_ids.update(row["candidate_id"] for row in adjacent[:3])
    write_json(output / "review_plan.json", {
        "required_candidate_ids": sorted(required_ids),
        "selection_rule": "All relation-type/provisional inspection samples plus top-three candidates of each retained-layer isolate",
        "input_hashes": {
            "claims": file_sha256(workspace / "04_Claim复核/claims.jsonl"),
            "candidates": file_sha256(workspace / "05_关系候选/relation_candidates.jsonl"),
            "overrides": file_sha256(overrides_path),
            "domain": file_sha256(workspace / "领域配置.json"),
        },
        "note": "Agent must actually review these candidates; extra risk-based reviews are allowed. Coverage is not exhaustive semantic validation.",
    })
    lines = ["# 第六轮关系判断抽查样本", "", "`accepted` 为正式关系；`candidate` 为低置信待复核关系，绘图时应使用虚线。", ""]
    for relation in samples:
        source, target = claim_by_id[relation["source_claim_id"]], claim_by_id[relation["target_claim_id"]]
        lines.extend([
            f"## {relation['relation_id']} — {relation['relation_type']} / {relation['annotation_status']}", "",
            f"- 来源 {source['claim_id']}（{source['paper_id']}）：{source['normalized_text']}",
            f"- 目标 {target['claim_id']}（{target['paper_id']}）：{target['normalized_text']}",
            f"- 置信度：{relation['confidence']}；直接性：{relation['directness']}；范围重叠：{relation['scope_overlap']}",
            f"- 判断理由：{relation['reason']}", "",
        ])
    write_text(output / "人工抽查样本.md", "\n".join(lines))
    write_text(
        output / "第六轮检查报告.md",
        "\n".join([
            "# 第六轮：Claim 关系判断检查报告", "",
            f"状态：**passed**。逐对记录 {len(decisions)} 个候选判断，保留 {len(relations)} 条关系。", "",
            f"正式关系：{len(accepted)}；低置信候选关系：{len(provisional)}；无关系：{report['unrelated_pairs']}。", "",
            f"关系类型：{report['relation_type_distribution']}", "",
            f"保留层孤立 Claim：{report['isolated_claims_in_retained_layer']}；仅正式层孤立 Claim：{report['isolated_claims_in_accepted_layer']}。", "",
            f"Agent修订覆盖条数：{len(overrides)}；实际阅读范围见agent_review_attestation.json；保留层孤立ID：{', '.join(isolated_retained) if isolated_retained else '无'}。", "",
            "全部候选均有决定记录。低置信关系不伪装成事实，将在图中以虚线呈现，并可通过 `relation_overrides.jsonl` 修订后重跑。", "",
            "人工抽查：`人工抽查样本.md`；完整决定：`relation_decisions.jsonl`。",
        ]),
    )
    write_text(
        workspace / "STATUS.md",
        "# Claim 演化知识图谱 状态\n\n状态：`round_6_completed`（等待用户检查）\n\n"
        f"已完成：{len(decisions)} 个候选对的关系判断；{len(accepted)} 条正式关系，{len(provisional)} 条低置信候选关系。\n\n"
        f"保留层孤立 Claim：{report['isolated_claims_in_retained_layer']}。\n\n"
        "尚未执行：时间泳道式演化网络装配与SVG绘制。\n\n"
        "第六轮检查入口：`06_关系判断/第六轮检查报告.md`。\n",
    )
    print(f"Relation judgment complete: {len(decisions)} decisions, {len(relations)} retained")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Round 6: judge Claim relation candidates.")
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--workspace", type=Path, default=DEFAULT_WORKSPACE)
    args = parser.parse_args()
    run(args.workspace.resolve(), args.batch_size)
