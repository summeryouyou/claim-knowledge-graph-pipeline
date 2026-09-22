from __future__ import annotations

import argparse
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from common import load_domain_profile, DEFAULT_WORKSPACE, file_sha256, read_jsonl, write_json, write_jsonl, write_text


SEMANTIC_NEIGHBORS = 10
CITATION_NEIGHBORS = 6
TWO_HOP_NEIGHBORS = 3
SCOPE_NEIGHBORS = 4
UNHELPFUL_FEATURES = {"的", "与", "和", "在", "中", "了", "为", "及", "对", "相", "相关", "活动", "任务", "刺激"}
ENGLISH_STOPWORDS = {"the", "and", "with", "from", "that", "this", "for", "are", "was", "were", "is", "of"}
CHINESE_FEATURE = re.compile(r"[\u3400-\u9fff]{2,4}")
ENGLISH_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*")


def clean(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key != "__input_line__"}


def claim_text(claim: dict[str, Any], title: str) -> str:
    scope = " ".join(str(value) for value in claim["scope"].values() if value)
    return f"{claim['normalized_text']} {scope} {title}"


def pair_key(claim_a: str, claim_b: str) -> tuple[str, str]:
    return tuple(sorted((claim_a, claim_b)))


def top_indices(scores: np.ndarray, eligible: list[int], limit: int) -> list[int]:
    return sorted(eligible, key=lambda index: (-float(scores[index]), index))[:limit]


def percentile(values: list[int], fraction: float) -> int:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction))]


def scope_overlap(claim_a: dict[str, Any], claim_b: dict[str, Any]) -> list[str]:
    return [
        field for field in claim_a["scope"]
        if claim_a["scope"][field] is not None
        and str(claim_a["scope"][field]).casefold() == str(claim_b["scope"].get(field)).casefold()
    ]


def readable_anchors(
    claim_a: dict[str, Any], claim_b: dict[str, Any], vector_a: Any, vector_b: Any, feature_names: np.ndarray
) -> list[str]:
    text_a, text_b = claim_a["normalized_text"], claim_b["normalized_text"]
    product = vector_a.multiply(vector_b).tocoo()
    chinese = [
        (float(score), feature_names[index].strip())
        for score, index in zip(product.data, product.col)
        if CHINESE_FEATURE.fullmatch(feature_names[index].strip())
        and feature_names[index].strip() not in UNHELPFUL_FEATURES
        and feature_names[index].strip() in text_a
        and feature_names[index].strip() in text_b
    ]
    anchors: list[str] = []
    for _, feature in sorted(chinese, key=lambda item: (-len(item[1]), -item[0], item[1])):
        if not any(feature in existing for existing in anchors):
            anchors.append(feature)
        if len(anchors) == 6:
            break
    tokens_a = {token.casefold() for token in ENGLISH_TOKEN.findall(text_a)}
    tokens_b = {token.casefold() for token in ENGLISH_TOKEN.findall(text_b)}
    english = sorted(
        (token for token in tokens_a & tokens_b if len(token) >= 3 and token not in ENGLISH_STOPWORDS),
        key=lambda token: (-len(token), token),
    )
    return anchors + english[:2]


def generate(workspace: Path) -> None:
    global ENGLISH_STOPWORDS
    profile = load_domain_profile(workspace)
    ENGLISH_STOPWORDS = set(profile["english_stopwords"])
    claims = [clean(row) for row in read_jsonl(workspace / "04_Claim复核" / "claims.jsonl")]
    papers = [clean(row) for row in read_jsonl(workspace / "01_规范化输入" / "papers.jsonl")]
    cites = [clean(row) for row in read_jsonl(workspace / "02_引用骨架" / "cites.jsonl")]
    paper_by_id = {row["paper_id"]: row for row in papers}
    claims.sort(key=lambda row: int(row["claim_id"][1:]))
    index_by_claim = {row["claim_id"]: index for index, row in enumerate(claims)}
    claims_by_paper: dict[str, list[int]] = defaultdict(list)
    for index, claim in enumerate(claims):
        claims_by_paper[claim["paper_id"]].append(index)

    corpus = [claim_text(claim, paper_by_id[claim["paper_id"]]["title"]) for claim in claims]
    vectorizer = TfidfVectorizer(analyzer="char", ngram_range=(2, 4), min_df=2, sublinear_tf=True)
    tfidf_warning = None
    try:
        vectors = vectorizer.fit_transform(corpus)
        feature_names = vectorizer.get_feature_names_out()
        similarities = cosine_similarity(vectors)
    except ValueError as exc:
        if not any(message in str(exc) for message in ("empty vocabulary", "After pruning", "max_df corresponds to < documents than min_df")):
            raise
        from scipy.sparse import csr_matrix
        vectors = csr_matrix((len(claims), 0))
        feature_names = np.array([], dtype=str)
        similarities = np.zeros((len(claims), len(claims)))
        tfidf_warning = "TF-IDF vocabulary empty: zero semantic scores; other recall channels remain available"
    np.fill_diagonal(similarities, 0.0)

    citation_direction = {(row["source_paper_id"], row["target_paper_id"]) for row in cites}
    cited_by_source: dict[str, set[str]] = defaultdict(set)
    for source, target in citation_direction:
        cited_by_source[source].add(target)
    two_hop_by_source: dict[str, set[str]] = defaultdict(set)
    for source, direct_targets in cited_by_source.items():
        for middle in direct_targets:
            two_hop_by_source[source].update(cited_by_source.get(middle, set()))
        two_hop_by_source[source].difference_update(direct_targets)
        two_hop_by_source[source].discard(source)

    candidates: dict[tuple[str, str], dict[str, Any]] = {}

    def add(index_a: int, index_b: int, signal: str) -> None:
        if index_a == index_b:
            return
        id_a, id_b = claims[index_a]["claim_id"], claims[index_b]["claim_id"]
        key = pair_key(id_a, id_b)
        if key not in candidates:
            candidates[key] = {"indices": tuple(sorted((index_a, index_b))), "signals": set()}
        candidates[key]["signals"].add(signal)

    # Same-paper Claim pairs preserve result→interpretation and method→finding argument structure.
    for indices in claims_by_paper.values():
        for position, index_a in enumerate(indices):
            for index_b in indices[position + 1:]:
                add(index_a, index_b, "within_paper")

    # Semantic neighbors are searched independently of citations and therefore recover uncited continuities.
    for index, claim in enumerate(claims):
        year = int(paper_by_id[claim["paper_id"]]["year"])
        eligible = [
            other for other, target in enumerate(claims)
            if target["paper_id"] != claim["paper_id"]
            and int(paper_by_id[target["paper_id"]]["year"]) <= year
        ]
        for other in top_indices(similarities[index], eligible, SEMANTIC_NEIGHBORS):
            add(index, other, "semantic_topk")

    # Direct citation is a recall signal, not a relation assertion; retain only the most comparable Claims per source Claim.
    for index, claim in enumerate(claims):
        linked = [
            other for paper_id in cited_by_source.get(claim["paper_id"], set())
            for other in claims_by_paper.get(paper_id, [])
        ]
        for other in top_indices(similarities[index], linked, CITATION_NEIGHBORS):
            add(index, other, "citation_1hop")

    # Two-hop citation reaches conceptual grandparents without displaying Paper citation edges in the final graph.
    for index, claim in enumerate(claims):
        linked = [
            other for paper_id in two_hop_by_source.get(claim["paper_id"], set())
            for other in claims_by_paper.get(paper_id, [])
        ]
        for other in top_indices(similarities[index], linked, TWO_HOP_NEIGHBORS):
            add(index, other, "citation_2hop")

    # Structured scope overlap adds cross-measurement or cross-paradigm candidates that wording similarity may miss.
    for index, claim in enumerate(claims):
        year = int(paper_by_id[claim["paper_id"]]["year"])
        scored: list[tuple[float, int]] = []
        for other, target in enumerate(claims):
            if target["paper_id"] == claim["paper_id"] or int(paper_by_id[target["paper_id"]]["year"]) > year:
                continue
            overlap = scope_overlap(claim, target)
            if overlap:
                scored.append((float(similarities[index, other]) + 0.08 * len(overlap), other))
        for _, other in sorted(scored, key=lambda item: (-item[0], item[1]))[:SCOPE_NEIGHBORS]:
            add(index, other, "scope_neighbor")

    # Defensive coverage fallback: every Claim must reach at least one cross-paper comparison candidate.
    cross_degree: Counter[str] = Counter()
    for item in candidates.values():
        index_a, index_b = item["indices"]
        if claims[index_a]["paper_id"] != claims[index_b]["paper_id"]:
            cross_degree[claims[index_a]["claim_id"]] += 1
            cross_degree[claims[index_b]["claim_id"]] += 1
    for index, claim in enumerate(claims):
        if cross_degree[claim["claim_id"]] > 0:
            continue
        eligible = [other for other, target in enumerate(claims) if target["paper_id"] != claim["paper_id"]]
        best = top_indices(similarities[index], eligible, 1)
        if best:
            add(index, best[0], "coverage_fallback")

    output_rows: list[dict[str, Any]] = []
    signal_counts: Counter[str] = Counter()
    degree: Counter[str] = Counter()
    cross_degree = Counter()
    for (claim_id_1, claim_id_2), item in sorted(candidates.items()):
        index_1, index_2 = index_by_claim[claim_id_1], index_by_claim[claim_id_2]
        claim_1, claim_2 = claims[index_1], claims[index_2]
        paper_1, paper_2 = paper_by_id[claim_1["paper_id"]], paper_by_id[claim_2["paper_id"]]
        signals = sorted(item["signals"])
        for signal in signals:
            signal_counts[signal] += 1
        degree[claim_id_1] += 1
        degree[claim_id_2] += 1
        if claim_1["paper_id"] != claim_2["paper_id"]:
            cross_degree[claim_id_1] += 1
            cross_degree[claim_id_2] += 1

        anchors = readable_anchors(claim_1, claim_2, vectors[index_1], vectors[index_2], feature_names)
        overlap = scope_overlap(claim_1, claim_2)
        citation_directions = []
        if (claim_1["paper_id"], claim_2["paper_id"]) in citation_direction:
            citation_directions.append(f"{claim_1['paper_id']}->{claim_2['paper_id']}")
        if (claim_2["paper_id"], claim_1["paper_id"]) in citation_direction:
            citation_directions.append(f"{claim_2['paper_id']}->{claim_1['paper_id']}")

        year_1, year_2 = int(paper_1["year"]), int(paper_2["year"])
        orientation_hint: dict[str, Any]
        if year_1 != year_2:
            newer = claim_1 if year_1 > year_2 else claim_2
            older = claim_2 if year_1 > year_2 else claim_1
            orientation_hint = {"source_claim_id": newer["claim_id"], "target_claim_id": older["claim_id"], "basis": "publication_year"}
        elif citation_directions:
            citing_paper = citation_directions[0].split("->", 1)[0]
            source = claim_1 if claim_1["paper_id"] == citing_paper else claim_2
            target = claim_2 if source is claim_1 else claim_1
            orientation_hint = {"source_claim_id": source["claim_id"], "target_claim_id": target["claim_id"], "basis": "same_year_citation"}
        else:
            orientation_hint = {"source_claim_id": None, "target_claim_id": None, "basis": "same_year_or_same_paper_unresolved"}

        output_rows.append({
            "schema_version": "relation_candidate.schema1",
            "candidate_id": f"K_{claim_id_1}_{claim_id_2}",
            "claim_id_1": claim_id_1,
            "claim_id_2": claim_id_2,
            "paper_id_1": claim_1["paper_id"],
            "paper_id_2": claim_2["paper_id"],
            "year_1": year_1,
            "year_2": year_2,
            "candidate_signals": signals,
            "semantic_score": round(float(similarities[index_1, index_2]), 6),
            "shared_lexical_anchors": anchors,
            "scope_overlap_fields": overlap,
            "citation_directions": citation_directions,
            "orientation_hint": orientation_hint,
            "candidate_only": True,
        })

    output = workspace / "05_关系候选"
    output.mkdir(parents=True, exist_ok=True)
    write_jsonl(output / "relation_candidates.jsonl", output_rows)

    degrees = [degree[claim["claim_id"]] for claim in claims]
    cross_degrees = [cross_degree[claim["claim_id"]] for claim in claims]
    noncitation = sum(1 for row in output_rows if "citation_1hop" not in row["candidate_signals"])
    report = {
        "report_version": "relation_candidate_report.schema1",
        "domain_profile_sha256": file_sha256(workspace / "领域配置.json"),
        "status": "passed",
        "claims": len(claims),
        "papers": len(papers),
        "candidate_pairs": len(output_rows),
        "signal_pair_counts": dict(sorted(signal_counts.items())),
        "candidate_pairs_without_direct_citation_signal": noncitation,
        "candidate_pairs_without_direct_citation_fraction": round(noncitation / max(1, len(output_rows)), 4),
        "same_paper_pairs": sum(row["paper_id_1"] == row["paper_id_2"] for row in output_rows),
        "same_year_pairs": sum(row["year_1"] == row["year_2"] for row in output_rows),
        "claim_degree": {"min": min(degrees), "median": statistics.median(degrees), "p90": percentile(degrees, 0.9), "max": max(degrees), "isolated": sum(value == 0 for value in degrees)},
        "cross_paper_degree": {"min": min(cross_degrees), "median": statistics.median(cross_degrees), "p90": percentile(cross_degrees, 0.9), "max": max(cross_degrees), "isolated": sum(value == 0 for value in cross_degrees)},
        "parameters": {
            "semantic_neighbors": SEMANTIC_NEIGHBORS,
            "citation_neighbors": CITATION_NEIGHBORS,
            "two_hop_neighbors": TWO_HOP_NEIGHBORS,
            "scope_neighbors": SCOPE_NEIGHBORS,
            "tfidf": "character 2-4 grams, min_df=2",
        },
        "output": {"file": "relation_candidates.jsonl", "rows": len(output_rows), "sha256": file_sha256(output / "relation_candidates.jsonl")},
        "warning": "Candidate pairs are high-recall comparisons, not accepted Claim relations.",
        "tfidf_warning": tfidf_warning,
    }
    write_json(output / "candidate_generation_report.json", report)

    samples: list[dict[str, Any]] = []
    sample_seen: set[str] = set()
    sample_groups = [
        ("同篇论证", lambda row: "within_paper" in row["candidate_signals"]),
        ("无直接引用的语义近邻", lambda row: "semantic_topk" in row["candidate_signals"] and "citation_1hop" not in row["candidate_signals"] and row["paper_id_1"] != row["paper_id_2"]),
        ("直接引用信号", lambda row: "citation_1hop" in row["candidate_signals"]),
        ("两跳引用信号", lambda row: "citation_2hop" in row["candidate_signals"]),
        ("范围近邻", lambda row: "scope_neighbor" in row["candidate_signals"]),
    ]
    for label, predicate in sample_groups:
        matches = sorted((row for row in output_rows if predicate(row)), key=lambda row: (-row["semantic_score"], row["candidate_id"]))[:5]
        for row in matches:
            if row["candidate_id"] in sample_seen:
                continue
            sample_seen.add(row["candidate_id"])
            samples.append({"sample_group": label, **row})
    write_json(output / "人工抽查样本.json", samples)
    lines = ["# 第五轮关系候选抽查样本", "", "这些记录只表示两条 Claim 值得在下一轮比较，不表示关系已经成立。", ""]
    for row in samples:
        claim_1, claim_2 = claims[index_by_claim[row["claim_id_1"]]], claims[index_by_claim[row["claim_id_2"]]]
        lines.extend([
            f"## {row['candidate_id']} — {row['sample_group']}", "",
            f"- {row['claim_id_1']}（{row['paper_id_1']}, {row['year_1']}）：{claim_1['normalized_text']}",
            f"- {row['claim_id_2']}（{row['paper_id_2']}, {row['year_2']}）：{claim_2['normalized_text']}",
            f"- 召回信号：{', '.join(row['candidate_signals'])}；语义分数：{row['semantic_score']}",
            f"- 共享词片段：{', '.join(row['shared_lexical_anchors']) or '无'}；范围重叠：{', '.join(row['scope_overlap_fields']) or '无'}", "",
        ])
    write_text(output / "人工抽查样本.md", "\n".join(lines))
    write_text(
        output / "第五轮检查报告.md",
        "\n".join([
            "# 第五轮：Claim 关系候选生成检查报告", "",
            f"状态：**passed**。{len(claims)} 条 Claim 生成 {len(output_rows)} 个去重候选对。", "",
            f"召回信号计数：{report['signal_pair_counts']}", "",
            f"无直接引用信号候选：{noncitation}（{report['candidate_pairs_without_direct_citation_fraction']:.1%}）。", "",
            f"全部候选度数：{report['claim_degree']}；跨论文候选度数：{report['cross_paper_degree']}。", "",
            "候选仅代表下一轮需要比较；语义相似、引用或两跳引用均不自动构成知识关系。", "",
            "人工抽查：`人工抽查样本.md`；机器可读结果：`relation_candidates.jsonl`。",
        ]),
    )
    write_text(
        workspace / "STATUS.md",
        "# Claim 演化知识图谱 状态\n\n状态：`round_5_completed`（等待用户检查）\n\n"
        f"已完成：{len(claims)} 条 Claim 的 {len(output_rows)} 个高召回关系候选对；跨论文候选孤立 Claim 为 {report['cross_paper_degree']['isolated']}。\n\n"
        "尚未执行：逐候选 Claim 关系判断与图谱绘制。\n\n"
        "第五轮检查入口：`05_关系候选/第五轮检查报告.md`。\n",
    )
    print(f"Relation candidates complete: {len(output_rows)} pairs for {len(claims)} Claims")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Round 5: generate high-recall Claim relation candidates.")
    parser.add_argument("--workspace", type=Path, default=DEFAULT_WORKSPACE)
    args = parser.parse_args()
    generate(args.workspace.resolve())
