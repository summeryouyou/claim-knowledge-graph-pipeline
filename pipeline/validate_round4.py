from __future__ import annotations

import hashlib
import re
from collections import Counter

from common import validator_workspace, report_warning, DEFAULT_WORKSPACE, read_json, read_jsonl


CLAIM_TYPES = {"empirical", "interpretation", "theoretical", "methodological", "synthesis"}
CLAIM_STATUSES = {"affirmative", "absence", "no_evidence", "conditional"}
SCOPE_FIELDS = {"population", "modality", "paradigm", "measurement_level", "conditions"}
CJK_PATTERN = re.compile(r"[\u3400-\u9fff]")


def clean(row: dict) -> dict:
    return {key: value for key, value in row.items() if key != "__input_line__"}


def run() -> None:
    round1 = DEFAULT_WORKSPACE / "01_规范化输入"
    round3 = DEFAULT_WORKSPACE / "03_Claim生成"
    round4 = DEFAULT_WORKSPACE / "04_Claim复核"
    papers = [clean(row) for row in read_jsonl(round1 / "papers.jsonl")]
    paper_by_id = {row["paper_id"]: row for row in papers}
    drafts = [clean(row) for row in read_jsonl(round3 / "claims_draft.jsonl")]
    draft_by_key = {row["claim_key"]: row for row in drafts}
    manifest = read_json(round4 / "task_manifest.json")
    report = read_json(round4 / "claim_review_report.json")
    claims = [clean(row) for row in read_jsonl(round4 / "claims.jsonl")]
    registry = read_json(round4 / "claim_id_registry.json")

    reviewed_keys: list[str] = []
    task_claim_fields = {
        "claim_key", "normalized_text", "claim_type", "claim_status", "scope",
        "evidence_basis", "evidence_spans", "selection_reason",
    }
    for batch_name in manifest["batches"]:
        task = read_json(round4 / "task_batches" / f"{batch_name}.json")
        decision = read_json(round4 / "agent_outputs" / f"{batch_name}.json")
        actual_agent_hash = hashlib.sha256((round4 / "agent_outputs" / f"{batch_name}.json").read_bytes()).hexdigest()
        if report["agent_output_hashes"].get(batch_name) != actual_agent_hash:
            raise ValueError(f"Agent review output changed since collect: {batch_name}")
        expected_keys: list[str] = []
        for paper in task["papers"]:
            if set(paper) != {"paper_id", "title", "abstract", "claim_drafts"}:
                raise ValueError(f"Task content boundary violation: {batch_name}/{paper.get('paper_id')}")
            canonical = paper_by_id[paper["paper_id"]]
            if paper["title"] != canonical["title"] or paper["abstract"] != canonical["abstract"]:
                raise ValueError(f"Task source mismatch: {paper['paper_id']}")
            for draft in paper["claim_drafts"]:
                if set(draft) != task_claim_fields:
                    raise ValueError(f"Unexpected draft field in review task: {draft.get('claim_key')}")
                if draft["claim_key"] not in draft_by_key:
                    raise ValueError(f"Unknown draft in review task: {draft['claim_key']}")
                expected_keys.append(draft["claim_key"])
        if decision["reviewed_claim_keys"] != expected_keys:
            raise ValueError(f"Review attestation mismatch: {batch_name}")
        reviewed_keys.extend(expected_keys)

    if len(reviewed_keys) != len(drafts) or set(reviewed_keys) != set(draft_by_key):
        raise ValueError("Not every source draft was reviewed exactly once")
    digest = hashlib.sha256((round4 / report["output"]["file"]).read_bytes()).hexdigest()
    if report["status"] != "passed" or digest != report["output"]["sha256"] or len(claims) != report["final_claims"]:
        raise ValueError("Review report status/count/hash mismatch")

    claim_ids: set[str] = set()
    review_keys: set[str] = set()
    paper_counts: Counter[str] = Counter()
    evidence_count = 0
    for claim in claims:
        claim_id = claim["claim_id"]
        review_key = claim["review_claim_key"]
        paper_id = claim["paper_id"]
        if claim_id in claim_ids or not re.fullmatch(r"C\d{3,}", claim_id):
            raise ValueError(f"Duplicate or malformed Claim ID: {claim_id}")
        if review_key in review_keys:
            raise ValueError(f"Duplicate review_claim_key: {review_key}")
        claim_ids.add(claim_id)
        review_keys.add(review_key)
        if paper_id not in paper_by_id or any(key not in draft_by_key for key in claim["source_claim_keys"]):
            raise ValueError(f"Invalid provenance: {claim_id}")
        if any(draft_by_key[key]["paper_id"] != paper_id for key in claim["source_claim_keys"]):
            raise ValueError(f"Cross-paper provenance: {claim_id}")
        if claim["claim_type"] not in CLAIM_TYPES or claim["claim_status"] not in CLAIM_STATUSES:
            raise ValueError(f"Invalid type/status: {claim_id}")
        if set(claim["scope"]) != SCOPE_FIELDS or not CJK_PATTERN.search(claim["normalized_text"]):
            raise ValueError(f"Invalid scope or normalized text: {claim_id}")
        if not 1 <= len(claim["evidence_spans"]) <= 3:
            raise ValueError(f"Invalid evidence span count: {claim_id}")
        for span in claim["evidence_spans"]:
            if span["location"] != "abstract" or span["text"] not in paper_by_id[paper_id]["abstract"]:
                raise ValueError(f"Non-verbatim evidence: {claim_id}")
            evidence_count += 1
        paper_counts[paper_id] += 1

    if set(paper_counts) != set(paper_by_id):
        raise ValueError("At least one paper has no final Claim")
    assignments = registry["assignments"]
    registry_ids = [entry["claim_id"] for entry in assignments.values()]
    if len(registry_ids) != len(set(registry_ids)):
        raise ValueError("ID registry reuses a Claim ID")
    active = {key: entry["claim_id"] for key, entry in assignments.items() if entry["active"]}
    if set(active) != review_keys or set(active.values()) != claim_ids:
        raise ValueError("Active ID registry entries do not match final Claims")
    if registry["next_number"] <= max(int(value[1:]) for value in registry_ids):
        raise ValueError("ID registry next_number could reuse an assigned ID")

    print({
        "status": "passed",
        "reviewed_drafts": len(reviewed_keys),
        "final_claims": len(claims),
        "papers_with_claims": len(paper_counts),
        "evidence_spans_verified": evidence_count,
        "unique_stable_ids": len(claim_ids),
        "next_unused_id_number": registry["next_number"],
        "cross_paper_relations_generated": 0,
    })


if __name__ == "__main__":
    DEFAULT_WORKSPACE = validator_workspace()
    run()
