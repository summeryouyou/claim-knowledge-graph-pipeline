from __future__ import annotations

import argparse
import math
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from common import DEFAULT_WORKSPACE, file_sha256, normalize_doi, read_jsonl, write_json, write_jsonl, write_text


YEAR_RE = re.compile(r",\s*((?:18|19|20)\d{2})\s*,")
WORD_RE = re.compile(r"[a-z0-9]+")
INITIALS_RE = re.compile(r"^[A-ZÀ-ÖØ-Þ][A-ZÀ-ÖØ-Þ.\-]*$")
VENUE_STOPWORDS = {"a", "an", "and", "of", "the", "j", "journal"}


def plain(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(character for character in text if not unicodedata.combining(character))


def author_key_from_paper(paper: dict[str, Any]) -> str | None:
    authors = paper.get("authors") or []
    if not authors:
        return None
    surname = str(authors[0]).split(",", 1)[0]
    key = "".join(WORD_RE.findall(plain(surname).casefold()))
    return key or None


def citation_signature(text: str) -> tuple[str | None, int | None, set[str]]:
    year_match = YEAR_RE.search(text)
    if year_match is None:
        return None, None, set()
    author_part = text[: year_match.start()].strip(" ,[]")
    author_tokens = author_part.split()
    while author_tokens and INITIALS_RE.fullmatch(plain(author_tokens[-1])):
        author_tokens.pop()
    author_key = "".join(WORD_RE.findall(plain(" ".join(author_tokens)).casefold())) or None
    remainder = text[year_match.end() :]
    venue = re.split(r",\s*(?:V\d|P\d|DOI\b|ARTN\b)", remainder, maxsplit=1, flags=re.IGNORECASE)[0]
    venue_tokens = {
        token[:3]
        for token in WORD_RE.findall(plain(venue).casefold())
        if len(token) >= 3 and token not in VENUE_STOPWORDS
    }
    return author_key, int(year_match.group(1)), venue_tokens


def venue_signature(source_title: str) -> set[str]:
    return {
        token[:3]
        for token in WORD_RE.findall(plain(source_title).casefold())
        if len(token) >= 3 and token not in VENUE_STOPWORDS
    }


def clean_loaded(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key != "__input_line__"}


def prepare(workspace: Path) -> None:
    source = workspace / "01_规范化输入"
    output = workspace / "02_引用骨架"
    papers = [clean_loaded(row) for row in read_jsonl(source / "papers.jsonl")]
    references = [clean_loaded(row) for row in read_jsonl(source / "citations_raw.jsonl")]
    if not papers:
        raise ValueError("No normalized core papers")

    paper_by_id = {paper["paper_id"]: paper for paper in papers}
    doi_to_papers: dict[str, list[str]] = defaultdict(list)
    author_year_to_papers: dict[tuple[str, int], list[str]] = defaultdict(list)
    paper_venue: dict[str, set[str]] = {}
    for paper in papers:
        if doi := normalize_doi(paper.get("doi")):
            doi_to_papers[doi].append(paper["paper_id"])
        if author_key := author_key_from_paper(paper):
            author_year_to_papers[(author_key, int(paper["year"]))].append(paper["paper_id"])
        paper_venue[paper["paper_id"]] = venue_signature(paper.get("source_title", ""))

    resolution_rows: list[dict[str, Any]] = []
    review_queue: list[dict[str, Any]] = []
    for reference in references:
        source_id = reference["source_paper_id"]
        raw_text = reference["target_reference_normalized"]
        doi_matches = sorted(
            {
                paper_id
                for candidate in reference.get("doi_candidates", [])
                for paper_id in doi_to_papers.get(normalize_doi(candidate) or "", [])
            }
        )
        author_key, cited_year, cited_venue = citation_signature(raw_text)
        author_year_matches = sorted(author_year_to_papers.get((author_key, cited_year), [])) if author_key and cited_year else []
        venue_matches = sorted(
            paper_id
            for paper_id in author_year_matches
            if cited_venue
            and len(cited_venue & paper_venue.get(paper_id, set())) >= max(1, math.ceil(len(cited_venue) * 0.6))
        )

        status = "external_or_unresolved"
        target_id: str | None = None
        candidates: list[str] = []
        method: str | None = None
        confidence: str | None = None
        requires_review = False
        review_reason: str | None = None

        if len(doi_matches) == 1:
            target_id = doi_matches[0]
            candidates = doi_matches
            method = "doi_exact"
            confidence = "high"
            if target_id == source_id:
                status = "self_reference"
                requires_review = True
                review_reason = "The resolved target equals the source paper and is excluded from CITES by default."
            elif int(paper_by_id[source_id]["year"]) < int(paper_by_id[target_id]["year"]):
                status = "future_dated_candidate"
                requires_review = True
                review_reason = "The citing paper predates the resolved target according to normalized publication years."
            else:
                status = "internal_mapped_doi"
        elif len(doi_matches) > 1:
            status = "ambiguous_doi_candidate"
            candidates = doi_matches
            method = "doi_exact"
            confidence = "low"
            requires_review = True
            review_reason = "A DOI candidate maps to multiple selected papers."
        # A nonmatching DOI is positive evidence that this is a different work.
        # Bibliographic fallback is therefore allowed only when no DOI was supplied.
        elif reference.get("doi_candidates"):
            status = "external_or_unresolved_doi"
        elif len(venue_matches) == 1:
            target_id = venue_matches[0]
            candidates = venue_matches
            method = "author_year_venue"
            confidence = "medium"
            status = "bibliographic_candidate"
            requires_review = True
            review_reason = "No internal DOI match; unique first-author/year/venue match requires confirmation."
        elif len(venue_matches) > 1:
            status = "ambiguous_bibliographic_candidate"
            candidates = venue_matches
            method = "author_year_venue"
            confidence = "low"
            requires_review = True
            review_reason = "Multiple selected papers match first author, year, and venue."
        elif len(author_year_matches) == 1:
            target_id = author_year_matches[0]
            candidates = author_year_matches
            method = "author_year_only"
            confidence = "low"
            status = "weak_bibliographic_candidate"
            requires_review = True
            review_reason = "Only first author and year match; venue could not be confirmed."
        elif len(author_year_matches) > 1:
            status = "ambiguous_author_year_candidate"
            candidates = author_year_matches
            method = "author_year_only"
            confidence = "low"
            requires_review = True
            review_reason = "Multiple selected papers share the parsed first author and year."

        result = {
            **reference,
            "resolution_status": status,
            "candidate_target_paper_id": target_id,
            "internal_match_candidates": candidates,
            "resolution_method": method,
            "mapping_confidence": confidence,
            "parsed_citation": {
                "first_author_key": author_key,
                "year": cited_year,
                "venue_tokens": sorted(cited_venue),
            },
            "requires_review": requires_review,
        }
        resolution_rows.append(result)
        if requires_review:
            review_queue.append(
                {
                    "reference_id": reference["reference_id"],
                    "source_paper": {
                        "paper_id": source_id,
                        "year": paper_by_id[source_id]["year"],
                        "title": paper_by_id[source_id]["title"],
                    },
                    "target_reference_raw": reference["target_reference_raw"],
                    "resolution_status": status,
                    "resolution_method": method,
                    "candidate_target_paper_id": target_id,
                    "candidate_papers": [
                        {
                            "paper_id": paper_id,
                            "year": paper_by_id[paper_id]["year"],
                            "title": paper_by_id[paper_id]["title"],
                            "doi": paper_by_id[paper_id]["doi"],
                            "first_author": (paper_by_id[paper_id].get("authors") or [None])[0],
                            "source_title": paper_by_id[paper_id]["source_title"],
                        }
                        for paper_id in candidates
                    ],
                    "review_reason": review_reason,
                    "required_decision": {
                        "reference_id": reference["reference_id"],
                        "decision": "accept | reject",
                        "target_paper_id": "required when accepting",
                        "review_note": "brief reason",
                    },
                }
            )

    status_counts = Counter(row["resolution_status"] for row in resolution_rows)
    preliminary_edges = {
        (row["source_paper_id"], row["candidate_target_paper_id"])
        for row in resolution_rows
        if row["resolution_status"] == "internal_mapped_doi"
    }
    report = {
        "report_version": "citation_preliminary_report.schema1",
        "status": "agent_review_required" if review_queue else "ready_to_finalize",
        "reference_rows": len(resolution_rows),
        "resolution_status_counts": dict(sorted(status_counts.items())),
        "unique_high_confidence_internal_edges": len(preliminary_edges),
        "review_queue_rows": len(review_queue),
        "policy": {
            "automatic_accept": "unique exact DOI match, excluding self/future-dated cases",
            "manual_review": "bibliographic fallback, ambiguity, self-reference, and future-dated cases",
            "graph_use": "CITES is auxiliary provenance/candidate evidence and will not be displayed as a final graph edge.",
            "noncitation_rule": "Absence of CITES must not be treated as evidence that two Claims are unrelated.",
        },
    }
    write_jsonl(output / "reference_resolution_candidates.jsonl", resolution_rows)
    write_jsonl(output / "citation_review_queue.jsonl", review_queue)
    decisions_path = output / "citation_review_decisions.jsonl"
    if not review_queue:
        write_text(decisions_path, "")
    write_json(output / "citation_preliminary_report.json", report)
    write_text(
        output / "AGENT_TASK.md",
        "\n".join(
            [
                "# 第二轮引用解析复核任务",
                "",
                "逐条检查 `citation_review_queue.jsonl`。只能依据该行原始参考文献和候选 Paper 元数据作出决定。",
                "",
                "将每条决定写入 `citation_review_decisions.jsonl`：",
                "",
                "```json",
                '{"reference_id":"R000001","decision":"accept","target_paper_id":"P001","review_note":"author, year and venue agree"}',
                "```",
                "",
                "若证据不足、候选歧义、自指或时间矛盾无法解释，使用 `reject`，且 `target_paper_id` 为 `null`。",
            ]
        ),
    )
    print(f"Citation prepare: {len(preliminary_edges)} DOI edges; {len(review_queue)} rows require review")


def finalize(workspace: Path) -> None:
    output = workspace / "02_引用骨架"
    papers = [clean_loaded(row) for row in read_jsonl(workspace / "01_规范化输入" / "papers.jsonl")]
    resolutions = [clean_loaded(row) for row in read_jsonl(output / "reference_resolution_candidates.jsonl")]
    review_queue = [clean_loaded(row) for row in read_jsonl(output / "citation_review_queue.jsonl")]
    decisions = [clean_loaded(row) for row in read_jsonl(output / "citation_review_decisions.jsonl")]
    paper_by_id = {paper["paper_id"]: paper for paper in papers}
    resolution_by_id = {row["reference_id"]: row for row in resolutions}
    required_ids = {row["reference_id"] for row in review_queue}
    decision_ids = [row.get("reference_id") for row in decisions]
    if len(decision_ids) != len(set(decision_ids)):
        raise ValueError("Duplicate reference_id in citation_review_decisions.jsonl")
    if set(decision_ids) != required_ids:
        missing = sorted(required_ids - set(decision_ids))
        extra = sorted(set(decision_ids) - required_ids)
        raise ValueError(f"Review decisions do not cover queue exactly: missing={missing[:20]}, extra={extra[:20]}")

    accepted_by_reference: dict[str, tuple[str, str]] = {}
    for row in resolutions:
        if row["resolution_status"] == "internal_mapped_doi":
            accepted_by_reference[row["reference_id"]] = (row["candidate_target_paper_id"], "doi_exact")
    for decision in decisions:
        if decision.get("decision") not in {"accept", "reject"}:
            raise ValueError(f"Invalid decision for {decision.get('reference_id')}")
        if decision["decision"] == "reject":
            continue
        reference = resolution_by_id[decision["reference_id"]]
        target = decision.get("target_paper_id")
        if target not in reference["internal_match_candidates"]:
            raise ValueError(f"Accepted target is not a candidate: {decision['reference_id']} -> {target}")
        if target == reference["source_paper_id"]:
            raise ValueError(f"Self-reference cannot enter CITES: {decision['reference_id']}")
        accepted_by_reference[decision["reference_id"]] = (target, reference["resolution_method"])

    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for reference_id, (target, method) in accepted_by_reference.items():
        source = resolution_by_id[reference_id]["source_paper_id"]
        key = (source, target)
        edge = grouped.setdefault(
            key,
            {
                "schema_version": "citation_edge.schema1",
                "source_paper_id": source,
                "relation": "CITES",
                "target_paper_id": target,
                "evidence_reference_ids": [],
                "resolution_methods": [],
            },
        )
        edge["evidence_reference_ids"].append(reference_id)
        if method not in edge["resolution_methods"]:
            edge["resolution_methods"].append(method)

    cites = [grouped[key] for key in sorted(grouped)]
    for edge in cites:
        edge["evidence_reference_ids"].sort()
        edge["resolution_methods"].sort()
    future_edges = [
        edge
        for edge in cites
        if int(paper_by_id[edge["source_paper_id"]]["year"]) < int(paper_by_id[edge["target_paper_id"]]["year"])
    ]
    if future_edges:
        raise ValueError(f"Accepted CITES contains future-dated edges: {future_edges[:10]}")

    indegree = Counter(edge["target_paper_id"] for edge in cites)
    outdegree = Counter(edge["source_paper_id"] for edge in cites)
    isolated = [paper["paper_id"] for paper in papers if not indegree[paper["paper_id"]] and not outdegree[paper["paper_id"]]]
    adjacency: dict[str, set[str]] = {paper["paper_id"]: set() for paper in papers}
    for edge in cites:
        source_id = edge["source_paper_id"]
        target_id = edge["target_paper_id"]
        adjacency[source_id].add(target_id)
        adjacency[target_id].add(source_id)
    remaining = set(adjacency)
    component_sizes: list[int] = []
    while remaining:
        frontier = [remaining.pop()]
        size = 0
        while frontier:
            current = frontier.pop()
            size += 1
            neighbors = adjacency[current] & remaining
            remaining.difference_update(neighbors)
            frontier.extend(neighbors)
        component_sizes.append(size)
    component_sizes.sort(reverse=True)
    same_year_edges = [
        edge
        for edge in cites
        if int(paper_by_id[edge["source_paper_id"]]["year"]) == int(paper_by_id[edge["target_paper_id"]]["year"])
    ]
    decision_counts = Counter(row["decision"] for row in decisions)
    method_counts = Counter(method for edge in cites for method in edge["resolution_methods"])
    report = {
        "report_version": "citation_report.schema1",
        "status": "passed",
        "reference_rows": len(resolutions),
        "unique_internal_cites": len(cites),
        "accepted_reference_evidence_rows": len(accepted_by_reference),
        "review_decision_counts": dict(sorted(decision_counts.items())),
        "accepted_resolution_method_counts": dict(sorted(method_counts.items())),
        "papers_with_outgoing_internal_cites": sum(bool(outdegree[p["paper_id"]]) for p in papers),
        "papers_with_incoming_internal_cites": sum(bool(indegree[p["paper_id"]]) for p in papers),
        "papers_isolated_in_citation_backbone": isolated,
        "undirected_component_sizes": component_sizes,
        "same_year_edges": len(same_year_edges),
        "future_dated_edges": future_edges,
        "display_policy": "Paper CITES edges are retained as hidden provenance and are not drawn in the final Claim graph.",
        "candidate_policy": "Later Claim-pair generation must also consider semantic/temporal neighborhoods; CITES is not an exclusive gate.",
    }
    examples = cites[:10]
    write_jsonl(output / "cites.jsonl", cites)
    report["output_files"] = {
        "cites.jsonl": {"rows": len(cites), "sha256": file_sha256(output / "cites.jsonl")},
        "reference_resolution_candidates.jsonl": {
            "rows": len(resolutions),
            "sha256": file_sha256(output / "reference_resolution_candidates.jsonl"),
        },
        "citation_review_queue.jsonl": {
            "rows": len(review_queue),
            "sha256": file_sha256(output / "citation_review_queue.jsonl"),
        },
        "citation_review_decisions.jsonl": {
            "rows": len(decisions),
            "sha256": file_sha256(output / "citation_review_decisions.jsonl"),
        },
    }
    write_json(output / "citation_report.json", report)
    write_json(
        output / "inspection_sample.json",
        {
            "sampling_rule": "First 10 edges in stable source/target order",
            "edges": examples,
            "papers": {
                paper_id: {
                    "year": paper_by_id[paper_id]["year"],
                    "title": paper_by_id[paper_id]["title"],
                    "doi": paper_by_id[paper_id]["doi"],
                }
                for edge in examples
                for paper_id in (edge["source_paper_id"], edge["target_paper_id"])
            },
        },
    )
    write_text(
        output / "第二轮检查报告.md",
        "\n".join(
            [
                "# 第二轮：引用骨架解析检查报告",
                "",
                "## 结论",
                "",
                f"解析完成并通过结构校验：{len(resolutions)} 条原始引用中形成 {len(cites)} 条核心集合内部唯一 CITES 边。",
                "",
                f"人工复核队列共 {len(review_queue)} 条：接纳 {decision_counts['accept']} 条，拒绝 {decision_counts['reject']} 条。",
                "",
                "## 图谱使用边界",
                "",
                "- CITES 只作为来源追溯和后续 Claim 关系候选的一个证据信号。",
                "- 最终可视化不显示 Paper 或 CITES。",
                "- 两篇论文之间没有 CITES，不代表其 Claim 无关；后续仍会建立语义与时间候选。",
                "",
                "## 结构统计",
                "",
                f"- 有内部出边的 Paper：{report['papers_with_outgoing_internal_cites']}/{len(papers)}",
                f"- 有内部入边的 Paper：{report['papers_with_incoming_internal_cites']}/{len(papers)}",
                f"- 在引用骨架中完全孤立的 Paper：{len(isolated)}",
                *(
                    f"  - {paper_id}（{paper_by_id[paper_id]['year']}）：{paper_by_id[paper_id]['title']}"
                    for paper_id in isolated
                ),
                "  - 上述论文不会被删除；后续通过 Claim 语义与时间邻域继续建立候选关系。",
                f"- 无向连通分量规模：{component_sizes}",
                f"- 同年引用边：{len(same_year_edges)}（只表示引用，不据此虚构月份先后）",
                f"- 未来引用冲突：{len(future_edges)}",
                "",
                "详细复核轨迹保存在 `reference_resolution_candidates.jsonl`、`citation_review_queue.jsonl` 和 `citation_review_decisions.jsonl`。",
            ]
        ),
    )
    write_text(
        workspace / "STATUS.md",
        "\n".join(
            [
                "# Claim 演化知识图谱 状态",
                "",
                "状态：`round_2_completed`（等待用户检查）",
                "",
                "已完成：第一轮输入规范化；第二轮引用解析、疑难记录复核及引用骨架定稿。",
                "",
                "尚未执行：Claim 生成、Claim 复核、Claim 关系判断和图谱绘制。",
                "",
                "第二轮检查入口：`02_引用骨架/第二轮检查报告.md`。",
            ]
        ),
    )
    print(f"Citation finalize: {len(cites)} internal edges; {len(isolated)} citation-isolated papers")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Round 2: resolve and review the selected-paper citation backbone.")
    parser.add_argument("action", choices=("prepare", "finalize"))
    parser.add_argument("--workspace", type=Path, default=DEFAULT_WORKSPACE)
    args = parser.parse_args()
    if args.action == "prepare":
        prepare(args.workspace.resolve())
    else:
        finalize(args.workspace.resolve())
