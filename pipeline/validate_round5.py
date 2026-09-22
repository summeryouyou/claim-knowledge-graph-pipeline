from __future__ import annotations

import hashlib
from collections import Counter, defaultdict

from common import validator_workspace, report_warning, DEFAULT_WORKSPACE, read_json, read_jsonl


SIGNALS = {"within_paper", "semantic_topk", "citation_1hop", "citation_2hop", "scope_neighbor", "coverage_fallback"}


def clean(row: dict) -> dict:
    return {key: value for key, value in row.items() if key != "__input_line__"}


def run() -> None:
    round1 = DEFAULT_WORKSPACE / "01_规范化输入"
    round2 = DEFAULT_WORKSPACE / "02_引用骨架"
    round4 = DEFAULT_WORKSPACE / "04_Claim复核"
    round5 = DEFAULT_WORKSPACE / "05_关系候选"
    papers = [clean(row) for row in read_jsonl(round1 / "papers.jsonl")]
    claims = [clean(row) for row in read_jsonl(round4 / "claims.jsonl")]
    cites = [clean(row) for row in read_jsonl(round2 / "cites.jsonl")]
    candidates = [clean(row) for row in read_jsonl(round5 / "relation_candidates.jsonl")]
    report = read_json(round5 / "candidate_generation_report.json")
    if report["domain_profile_sha256"] != hashlib.sha256((DEFAULT_WORKSPACE / "领域配置.json").read_bytes()).hexdigest():
        raise ValueError("Domain configuration changed since candidate generation")
    paper_by_id = {row["paper_id"]: row for row in papers}
    claim_by_id = {row["claim_id"]: row for row in claims}
    citation_edges = {(row["source_paper_id"], row["target_paper_id"]) for row in cites}
    cited_by_source: dict[str, set[str]] = defaultdict(set)
    for source, target in citation_edges:
        cited_by_source[source].add(target)
    two_hop = {
        (source, target)
        for source, middles in cited_by_source.items()
        for middle in middles
        for target in cited_by_source.get(middle, set())
        if target != source and target not in middles
    }

    digest = hashlib.sha256((round5 / report["output"]["file"]).read_bytes()).hexdigest()
    if report["status"] != "passed" or report["candidate_pairs"] != len(candidates) or digest != report["output"]["sha256"]:
        raise ValueError("Candidate report status/count/hash mismatch")

    seen_pairs: set[tuple[str, str]] = set()
    seen_ids: set[str] = set()
    degree: Counter[str] = Counter()
    cross_degree: Counter[str] = Counter()
    noncitation = 0
    for row in candidates:
        claim_id_1, claim_id_2 = row["claim_id_1"], row["claim_id_2"]
        if claim_id_1 not in claim_by_id or claim_id_2 not in claim_by_id or claim_id_1 >= claim_id_2:
            raise ValueError(f"Invalid or noncanonical Claim pair: {row['candidate_id']}")
        pair = (claim_id_1, claim_id_2)
        if pair in seen_pairs or row["candidate_id"] in seen_ids or row["candidate_id"] != f"K_{claim_id_1}_{claim_id_2}":
            raise ValueError(f"Duplicate or unstable candidate identity: {row['candidate_id']}")
        seen_pairs.add(pair)
        seen_ids.add(row["candidate_id"])
        claim_1, claim_2 = claim_by_id[claim_id_1], claim_by_id[claim_id_2]
        paper_1, paper_2 = paper_by_id[claim_1["paper_id"]], paper_by_id[claim_2["paper_id"]]
        if row["paper_id_1"] != claim_1["paper_id"] or row["paper_id_2"] != claim_2["paper_id"]:
            raise ValueError(f"Candidate Paper provenance mismatch: {row['candidate_id']}")
        if row["year_1"] != int(paper_1["year"]) or row["year_2"] != int(paper_2["year"]):
            raise ValueError(f"Candidate year mismatch: {row['candidate_id']}")
        signals = set(row["candidate_signals"])
        if not signals or not signals <= SIGNALS or row["candidate_only"] is not True:
            raise ValueError(f"Invalid signals/status: {row['candidate_id']}")
        if not 0.0 <= row["semantic_score"] <= 1.0:
            raise ValueError(f"Semantic score out of range: {row['candidate_id']}")
        same_paper = claim_1["paper_id"] == claim_2["paper_id"]
        if ("within_paper" in signals) != same_paper:
            raise ValueError(f"within_paper signal mismatch: {row['candidate_id']}")
        direct = (claim_1["paper_id"], claim_2["paper_id"]) in citation_edges or (claim_2["paper_id"], claim_1["paper_id"]) in citation_edges
        if "citation_1hop" in signals and not direct:
            raise ValueError(f"False direct citation signal: {row['candidate_id']}")
        hop2 = (claim_1["paper_id"], claim_2["paper_id"]) in two_hop or (claim_2["paper_id"], claim_1["paper_id"]) in two_hop
        if "citation_2hop" in signals and not hop2:
            raise ValueError(f"False two-hop citation signal: {row['candidate_id']}")
        hint = row["orientation_hint"]
        if row["year_1"] != row["year_2"]:
            newer = claim_id_1 if row["year_1"] > row["year_2"] else claim_id_2
            older = claim_id_2 if newer == claim_id_1 else claim_id_1
            if hint != {"source_claim_id": newer, "target_claim_id": older, "basis": "publication_year"}:
                raise ValueError(f"Temporal orientation mismatch: {row['candidate_id']}")
        degree[claim_id_1] += 1
        degree[claim_id_2] += 1
        if not same_paper:
            cross_degree[claim_id_1] += 1
            cross_degree[claim_id_2] += 1
        if "citation_1hop" not in signals:
            noncitation += 1

    if len(papers) > 1 and (set(degree) != set(claim_by_id) or set(cross_degree) != set(claim_by_id)):
        raise ValueError("At least one Claim has no cross-paper comparison candidate")
    if len(papers) == 1:
        report_warning("Single-paper collection: cross-paper coverage is not applicable")
    if noncitation != report["candidate_pairs_without_direct_citation_signal"]:
        raise ValueError("Noncitation candidate count mismatch")
    if candidates and noncitation / max(1, len(candidates)) < 0.5:
        report_warning("Non-direct-citation signal fraction below 50%; review recall without forcing pairs")

    print({
        "status": "passed",
        "claims": len(claims),
        "candidate_pairs": len(candidates),
        "unique_candidate_ids": len(seen_ids),
        "claims_with_cross_paper_candidates": len(cross_degree),
        "minimum_cross_paper_degree": min(cross_degree.values(), default=0),
        "candidate_pairs_without_direct_citation_signal": noncitation,
        "noncitation_fraction": round(noncitation / max(1, len(candidates)), 4),
        "accepted_relations_generated": 0,
    })


if __name__ == "__main__":
    DEFAULT_WORKSPACE = validator_workspace()
    run()
