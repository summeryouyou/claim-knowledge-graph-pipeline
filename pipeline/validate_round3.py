from __future__ import annotations

import hashlib
import re

from common import sample_paper_ids, validator_workspace, report_warning, DEFAULT_WORKSPACE, read_json, read_jsonl


CJK_PATTERN = re.compile(r"[\u3400-\u9fff]")
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


def clean(row: dict) -> dict:
    return {key: value for key, value in row.items() if key != "__input_line__"}


def run() -> None:
    round1 = DEFAULT_WORKSPACE / "01_规范化输入"
    round3 = DEFAULT_WORKSPACE / "03_Claim生成"
    papers = [clean(row) for row in read_jsonl(round1 / "papers.jsonl")]
    paper_by_id = {row["paper_id"]: row for row in papers}
    manifest = read_json(round3 / "task_manifest.json")
    report = read_json(round3 / "claim_generation_report.json")
    drafts = [clean(row) for row in read_jsonl(round3 / "claims_draft.jsonl")]

    task_paper_ids: list[str] = []
    for batch_name in manifest["batches"]:
        task = read_json(round3 / "task_batches" / f"{batch_name}.json")
        output_rows = [clean(row) for row in read_jsonl(round3 / "agent_outputs" / f"{batch_name}.jsonl")]
        actual_agent_hash = hashlib.sha256((round3 / "agent_outputs" / f"{batch_name}.jsonl").read_bytes()).hexdigest()
        if report["agent_output_hashes"].get(batch_name) != actual_agent_hash:
            raise ValueError(f"Agent output changed since collect: {batch_name}")
        batch_ids = [row["paper_id"] for row in task["papers"]]
        if [row["paper_id"] for row in output_rows] != batch_ids:
            raise ValueError(f"Agent output order/membership mismatch: {batch_name}")
        for paper in task["papers"]:
            if set(paper) != {"paper_id", "title", "abstract"}:
                raise ValueError(f"Round-3 content boundary violation: {batch_name}/{paper.get('paper_id')}")
            canonical = paper_by_id[paper["paper_id"]]
            if paper["title"] != canonical["title"] or paper["abstract"] != canonical["abstract"]:
                raise ValueError(f"Task content differs from normalized input: {paper['paper_id']}")
            task_paper_ids.append(paper["paper_id"])

    if len(task_paper_ids) != len(papers) or set(task_paper_ids) != set(paper_by_id) or len(set(task_paper_ids)) != len(papers):
        raise ValueError("Task coverage is not exactly the normalized core set")
    if len(drafts) != report["draft_claims"] or report["status"] != "passed":
        raise ValueError("Draft/report count or status mismatch")
    digest = hashlib.sha256((round3 / report["output"]["file"]).read_bytes()).hexdigest()
    if digest != report["output"]["sha256"]:
        raise ValueError("Claim draft hash mismatch")

    seen_keys: set[str] = set()
    seen_texts: set[tuple[str, str]] = set()
    counts = {paper_id: 0 for paper_id in paper_by_id}
    evidence_spans = 0
    for row in drafts:
        paper_id = row["paper_id"]
        if paper_id not in paper_by_id:
            raise ValueError(f"Unknown paper_id: {paper_id}")
        if row["claim_key"] in seen_keys:
            raise ValueError(f"Duplicate claim_key: {row['claim_key']}")
        seen_keys.add(row["claim_key"])
        text_key = (paper_id, row["normalized_text"])
        if text_key in seen_texts:
            raise ValueError(f"Duplicate Claim text within paper: {paper_id}")
        seen_texts.add(text_key)
        if not CJK_PATTERN.search(row["normalized_text"]):
            raise ValueError(f"Claim is not a Chinese proposition: {row['claim_key']}")
        if row["claim_type"] not in CLAIM_TYPES or row["claim_status"] not in CLAIM_STATUSES:
            raise ValueError(f"Invalid type/status: {row['claim_key']}")
        if set(row["scope"]) != SCOPE_FIELDS:
            raise ValueError(f"Invalid scope fields: {row['claim_key']}")
        if not row["evidence_basis"] or any(value not in EVIDENCE_BASES for value in row["evidence_basis"]):
            raise ValueError(f"Invalid evidence basis: {row['claim_key']}")
        for span in row["evidence_spans"]:
            if span["location"] != "abstract" or span["text"] not in paper_by_id[paper_id]["abstract"]:
                raise ValueError(f"Non-verbatim evidence: {row['claim_key']}")
            evidence_spans += 1
        counts[paper_id] += 1

    if any(not 1 <= count <= 3 for count in counts.values()):
        raise ValueError("At least one paper does not have 1-3 Claim drafts")
    expected_sample = sample_paper_ids(papers)
    sample = read_json(round3 / "人工抽查样本.json")
    if [row["paper_id"] for row in sample] != expected_sample:
        raise ValueError("Inspection sample is missing or unstable")

    print(
        {
            "status": "passed",
            "task_input_fields": ["paper_id", "title", "abstract"],
            "papers": len(papers),
            "draft_claims": len(drafts),
            "evidence_spans_verified": evidence_spans,
            "papers_with_1_to_3_claims": sum(1 for count in counts.values() if 1 <= count <= 3),
            "inspection_sample_papers": len(sample),
        }
    )


if __name__ == "__main__":
    DEFAULT_WORKSPACE = validator_workspace()
    run()
