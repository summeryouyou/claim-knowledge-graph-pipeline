from __future__ import annotations

import hashlib
import json

from common import discover_core_file, clean_text, validator_workspace, report_warning, DEFAULT_WORKSPACE, RAW_DATA_DIR, read_json, write_json


def raw_rows(filename: str) -> list[dict]:
    path = RAW_DATA_DIR / filename
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def run() -> None:
    output = DEFAULT_WORKSPACE / "01_规范化输入"
    report = read_json(output / "normalization_report.json")
    for filename, metadata in report["output_files"].items():
        digest = hashlib.sha256((output / filename).read_bytes()).hexdigest()
        if digest != metadata["sha256"]:
            raise ValueError(f"Output hash mismatch: {filename}")

    samples = read_json(output / "inspection_sample.json")["samples"]
    raw = {
        filename: raw_rows(filename)
        for filename in (discover_core_file().name, "文献主表.jsonl", "参考文献关系-原始.jsonl")
    }
    normalized_papers = [json.loads(line) for line in (output / "papers.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    core_rows = raw[discover_core_file().name]
    if len(normalized_papers) != len(core_rows) or {p["record_id"] for p in normalized_papers} != {clean_text(r["record_id"]) for r in core_rows}:
        raise ValueError("Normalized set differs from core selection")
    reference_checks = 0
    for item in samples:
        paper = item["normalized_paper"]
        selected = item["selection_source"]
        master = item["master_source"]
        selected_raw = raw[selected["file"]][selected["line"] - 1]
        master_raw = raw[master["file"]][master["line"] - 1]
        if clean_text(selected_raw["record_id"]) != paper["record_id"] or clean_text(master_raw["record_id"]) != paper["record_id"]:
            raise ValueError(f"Source pointer mismatch: {paper['paper_id']}")
        if clean_text(master_raw["title"]) != paper["title"]:
            raise ValueError(f"Title mismatch: {paper['paper_id']}")
        for reference in item["raw_reference_examples"]:
            source = reference["source_input"]
            reference_raw = raw[source["file"]][source["line"] - 1]
            if (
                clean_text(reference_raw["source_record_id"]) != paper["record_id"]
                or reference_raw["target_reference_raw"] != reference["target_reference_raw"]
            ):
                raise ValueError(f"Reference source pointer mismatch: {reference['reference_id']}")
            reference_checks += 1

    result = {
        "status": "passed",
        "conclusion": "Round 1 outputs are consistent with their recorded hashes and sampled raw source lines.",
        "output_hashes_verified": len(report["output_files"]),
        "sampled_papers_verified_against_raw": len(samples),
        "sampled_references_verified_against_raw": reference_checks,
    }
    write_json(output / "independent_validation.json", result)
    print(result)


if __name__ == "__main__":
    DEFAULT_WORKSPACE = validator_workspace()
    run()
