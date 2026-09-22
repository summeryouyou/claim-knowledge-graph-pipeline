from __future__ import annotations

import hashlib

from common import validator_workspace, report_warning, DEFAULT_WORKSPACE, normalize_doi, read_json, read_jsonl


def clean(row: dict) -> dict:
    return {key: value for key, value in row.items() if key != "__input_line__"}


def run() -> None:
    round1 = DEFAULT_WORKSPACE / "01_规范化输入"
    round2 = DEFAULT_WORKSPACE / "02_引用骨架"
    papers = [clean(row) for row in read_jsonl(round1 / "papers.jsonl")]
    references = [clean(row) for row in read_jsonl(round1 / "citations_raw.jsonl")]
    resolutions = [clean(row) for row in read_jsonl(round2 / "reference_resolution_candidates.jsonl")]
    cites = [clean(row) for row in read_jsonl(round2 / "cites.jsonl")]
    report = read_json(round2 / "citation_report.json")
    paper_by_id = {paper["paper_id"]: paper for paper in papers}
    reference_by_id = {row["reference_id"]: row for row in references}

    for filename, metadata in report["output_files"].items():
        digest = hashlib.sha256((round2 / filename).read_bytes()).hexdigest()
        if digest != metadata["sha256"]:
            raise ValueError(f"Output hash mismatch: {filename}")
    if len(resolutions) != len(references):
        raise ValueError("Not every raw reference has a resolution audit row")

    resolution_by_id = {row["reference_id"]: row for row in resolutions}
    evidence_rows = 0
    seen_edges: set[tuple[str, str]] = set()
    for edge in cites:
        source = edge["source_paper_id"]
        target = edge["target_paper_id"]
        if source not in paper_by_id or target not in paper_by_id or source == target:
            raise ValueError(f"Invalid CITES endpoints: {source} -> {target}")
        if int(paper_by_id[source]["year"]) < int(paper_by_id[target]["year"]):
            raise ValueError(f"Future-dated CITES: {source} -> {target}")
        if (source, target) in seen_edges:
            raise ValueError(f"Duplicate CITES edge: {source} -> {target}")
        seen_edges.add((source, target))
        for reference_id in edge["evidence_reference_ids"]:
            reference = reference_by_id[reference_id]
            if reference["source_paper_id"] != source:
                raise ValueError(f"Evidence source mismatch: {reference_id}")
            if resolution_by_id[reference_id]["resolution_method"] == "doi_exact":
                target_doi = normalize_doi(paper_by_id[target]["doi"])
                if target_doi not in {normalize_doi(value) for value in reference["doi_candidates"]}:
                    raise ValueError(f"DOI evidence mismatch: {reference_id}")
            evidence_rows += 1

    print(
        {
            "status": "passed",
            "papers": len(papers),
            "raw_references_audited": len(resolutions),
            "unique_internal_cites": len(cites),
            "evidence_rows_verified": evidence_rows,
            "future_or_self_edges": 0,
        }
    )


if __name__ == "__main__":
    DEFAULT_WORKSPACE = validator_workspace()
    run()
