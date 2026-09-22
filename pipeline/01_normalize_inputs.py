from __future__ import annotations

import argparse
import statistics
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from common import (
    DEFAULT_WORKSPACE,
    discover_core_file,
    sample_paper_ids,
    RAW_DATA_DIR,
    clean_text,
    extract_dois,
    file_sha256,
    normalize_doi,
    read_jsonl,
    write_json,
    write_jsonl,
    write_text,
)



MASTER_FILE = RAW_DATA_DIR / "文献主表.jsonl"
REFERENCES_FILE = RAW_DATA_DIR / "参考文献关系-原始.jsonl"



def value_type(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "string"


def is_nonempty(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return bool(value)
    return True


def profile_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    fields = sorted({key for row in rows for key in row if key != "__input_line__"})
    return {
        "rows": len(rows),
        "fields": {
            field: {
                "present": sum(field in row for row in rows),
                "nonempty": sum(is_nonempty(row.get(field)) for row in rows),
                "types": sorted({value_type(row.get(field)) for row in rows if field in row}),
            }
            for field in fields
        },
    }


def require_fields(name: str, rows: list[dict[str, Any]], required: set[str]) -> list[str]:
    errors: list[str] = []
    for row in rows:
        missing = sorted(field for field in required if not is_nonempty(row.get(field)))
        if missing:
            errors.append(f"{name}:{row['__input_line__']} missing/empty {missing}")
    return errors


def normalize_title(value: object) -> str:
    return clean_text(value).casefold()


def normalize_authors(value: object) -> list[str]:
    if value is None:
        return []
    raw = value if isinstance(value, list) else [value]
    return [name for item in raw if (name := clean_text(item))]


def integer(value: object, *, field: str, source: str) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{source}: invalid {field}={value!r}") from exc


def markdown_table(rows: list[list[object]], headers: list[str]) -> str:
    def escaped(value: object) -> str:
        return clean_text(value).replace("|", "\\|")

    output = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    output.extend("| " + " | ".join(escaped(value) for value in row) + " |" for row in rows)
    return "\n".join(output)


def run(workspace: Path) -> None:
    CORE_FILE = discover_core_file()
    for path in (CORE_FILE, MASTER_FILE, REFERENCES_FILE):
        if not path.is_file():
            raise FileNotFoundError(f"Missing raw input: {path}")

    output = workspace / "01_规范化输入"
    core_rows = read_jsonl(CORE_FILE)
    EXPECTED_PAPERS = len(core_rows)
    if not EXPECTED_PAPERS:
        raise ValueError("Core selection is empty")
    master_rows = read_jsonl(MASTER_FILE)
    reference_rows = read_jsonl(REFERENCES_FILE)
    selected_ids = {clean_text(row.get("record_id")) for row in core_rows}

    schema_errors = []
    schema_errors.extend(require_fields(CORE_FILE.name, core_rows, {"record_id", "rank", "title"}))
    # The master table may contain incomplete papers outside the selected core set.
    # Abstract completeness is enforced below only after joining the selected set.
    schema_errors.extend(require_fields(MASTER_FILE.name, [r for r in master_rows if clean_text(r.get("record_id")) in selected_ids], {"record_id", "title", "year", "abstract"}))
    schema_errors.extend(require_fields(REFERENCES_FILE.name, [r for r in reference_rows if clean_text(r.get("source_record_id")) in selected_ids], {"source_record_id", "target_reference_raw"}))
    if schema_errors:
        raise ValueError("Input schema errors:\n- " + "\n- ".join(schema_errors[:100]))

    core_ids = [clean_text(row.get("record_id")) for row in core_rows]
    master_ids = [clean_text(row.get("record_id")) for row in master_rows if clean_text(row.get("record_id")) in selected_ids]
    duplicate_core_ids = sorted(key for key, count in Counter(core_ids).items() if count > 1)
    duplicate_master_ids = sorted(key for key, count in Counter(master_ids).items() if count > 1)
    if duplicate_core_ids or duplicate_master_ids:
        raise ValueError(
            f"Duplicate record IDs: core={duplicate_core_ids[:20]}, master={duplicate_master_ids[:20]}"
        )

    if len(core_rows) != EXPECTED_PAPERS:
        raise ValueError(f"Expected {EXPECTED_PAPERS} selected papers, found {len(core_rows)}")

    ranked_core = sorted(
        core_rows,
        key=lambda row: (integer(row["rank"], field="rank", source=CORE_FILE.name), clean_text(row.get("record_id"))),
    )
    ranks = [integer(row["rank"], field="rank", source=CORE_FILE.name) for row in ranked_core]
    if ranks != list(range(1, EXPECTED_PAPERS + 1)):
        raise ValueError("Selection ranks must be unique and cover 1..N")

    master_by_record = {clean_text(row.get("record_id")): row for row in master_rows}
    missing_from_master = [record_id for record_id in core_ids if record_id not in master_by_record]
    if missing_from_master:
        raise ValueError(f"Selected records missing from master table: {missing_from_master}")

    papers: list[dict[str, Any]] = []
    provenance: list[dict[str, Any]] = []
    cross_table_mismatches: list[dict[str, Any]] = []
    record_to_paper: dict[str, str] = {}

    for core in ranked_core:
        record_id = clean_text(core["record_id"])
        master = master_by_record[record_id]
        rank = integer(core["rank"], field="rank", source=f"{CORE_FILE.name}:{core['__input_line__']}")
        paper_id = f"P{rank:03d}"
        record_to_paper[record_id] = paper_id

        comparison = {
            "title": normalize_title(core.get("title")) == normalize_title(master.get("title")),
            "year": clean_text(core.get("year")) == clean_text(master.get("year")),
            "doi": normalize_doi(core.get("doi")) == normalize_doi(master.get("doi")),
        }
        if not all(comparison.values()):
            cross_table_mismatches.append(
                {
                    "paper_id": paper_id,
                    "record_id": record_id,
                    "matching_fields": comparison,
                    "selection_values": {key: core.get(key) for key in comparison},
                    "master_values": {key: master.get(key) for key in comparison},
                }
            )

        year = integer(master["year"], field="year", source=f"{MASTER_FILE.name}:{master['__input_line__']}")
        title = clean_text(master["title"])
        abstract = clean_text(master["abstract"])
        if not title or not abstract or year < 1800 or year > datetime.now().year + 1:
            raise ValueError(f"Invalid normalized Paper fields for {paper_id}")

        papers.append(
            {
                "schema_version": "paper.schema1",
                "paper_id": paper_id,
                "record_id": record_id,
                "title": title,
                "year": year,
                "doi": normalize_doi(master.get("doi")),
                "authors": normalize_authors(master.get("authors")),
                "source_title": clean_text(master.get("source_title")),
                "abstract": abstract,
                "selection_rank": rank,
                "selection_category": clean_text(core.get("category")) or None,
            }
        )
        provenance.append(
            {
                "paper_id": paper_id,
                "record_id": record_id,
                "selection_input": {"file": CORE_FILE.name, "line": core["__input_line__"]},
                "metadata_input": {"file": MASTER_FILE.name, "line": master["__input_line__"]},
                "authoritative_fields": {
                    "paper_id": "derived from selection rank",
                    "selection_rank": CORE_FILE.name,
                    "selection_category": CORE_FILE.name,
                    "record_id": "cross-table join key",
                    "title/year/doi/authors/source_title/abstract": MASTER_FILE.name,
                },
            }
        )

    citations_raw: list[dict[str, Any]] = []
    citation_counts = Counter()
    ignored_reference_rows = 0
    empty_reference_rows = 0
    relation_values = Counter()
    raw_reference_keys = Counter()
    for raw in reference_rows:
        source_record_id = clean_text(raw.get("source_record_id"))
        source_paper_id = record_to_paper.get(source_record_id)
        if source_paper_id is None:
            ignored_reference_rows += 1
            continue
        reference_text = clean_text(raw["target_reference_raw"])
        if not reference_text:
            empty_reference_rows += 1
            continue
        reference_id = f"R{len(citations_raw) + 1:06d}"
        relation = clean_text(raw.get("relation")) or "cites_raw_reference"
        citations_raw.append(
            {
                "schema_version": "citation_raw.schema1",
                "reference_id": reference_id,
                "source_paper_id": source_paper_id,
                "source_record_id": source_record_id,
                "relation": relation,
                "target_reference_raw": str(raw["target_reference_raw"]),
                "target_reference_normalized": reference_text,
                "doi_candidates": extract_dois(reference_text),
                "source_input": {"file": REFERENCES_FILE.name, "line": raw["__input_line__"]},
            }
        )
        citation_counts[source_paper_id] += 1
        relation_values[relation] += 1
        raw_reference_keys[(source_paper_id, reference_text.casefold())] += 1

    duplicate_reference_rows = sum(count - 1 for count in raw_reference_keys.values() if count > 1)
    papers_without_references = [paper["paper_id"] for paper in papers if citation_counts[paper["paper_id"]] == 0]
    paper_dois = [paper["doi"] for paper in papers if paper["doi"]]
    duplicate_dois = sorted(doi for doi, count in Counter(paper_dois).items() if count > 1)
    reference_count_values = [citation_counts[paper["paper_id"]] for paper in papers]
    selected_record_ids = set(record_to_paper)
    nonselected_master_missing_abstracts = [
        {
            "record_id": clean_text(row.get("record_id")),
            "input_line": row["__input_line__"],
        }
        for row in master_rows
        if clean_text(row.get("record_id")) not in selected_record_ids and not is_nonempty(row.get("abstract"))
    ]

    input_profile = {
        "profile_version": "input_profile.schema1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_policy": "Only project-root data/ files were read",
        "inputs": {
            CORE_FILE.name: {
                "role": "selected-paper list and selection metadata",
                "format": "UTF-8 JSON Lines; one object per line",
                "sha256": file_sha256(CORE_FILE),
                **profile_rows(core_rows),
            },
            MASTER_FILE.name: {
                "role": "paper metadata master table",
                "format": "UTF-8 JSON Lines; one object per line",
                "sha256": file_sha256(MASTER_FILE),
                **profile_rows(master_rows),
            },
            REFERENCES_FILE.name: {
                "role": "raw outgoing-reference records",
                "format": "UTF-8 JSON Lines; one object per line",
                "sha256": file_sha256(REFERENCES_FILE),
                **profile_rows(reference_rows),
            },
        },
        "inferred_join": {
            "selection_to_master": f"{CORE_FILE.name}.record_id = 文献主表.record_id",
            "reference_to_source_paper": "参考文献关系-原始.source_record_id = 文献主表.record_id",
        },
        "normalization_rules": {
            "paper_id": "P + zero-padded selection rank; P001..PN",
            "text": "trim and collapse Unicode whitespace",
            "year": "convert numeric string to integer",
            "doi": "lowercase; remove DOI URL/prefix and trailing citation punctuation; missing becomes null",
            "authors": "normalize to non-empty string array",
            "citation": "preserve original target_reference_raw and add whitespace-normalized copy plus DOI candidates",
        },
    }

    blocking_issues: list[str] = []
    warnings: list[str] = []
    if cross_table_mismatches:
        warnings.append(f"{len(cross_table_mismatches)} selected/master cross-table mismatch(es)")
    if duplicate_dois:
        warnings.append(f"{len(duplicate_dois)} duplicate normalized DOI value(s)")
    if duplicate_reference_rows:
        warnings.append(f"{duplicate_reference_rows} duplicate raw reference row(s) within source papers")
    if papers_without_references:
        warnings.append(f"{len(papers_without_references)} selected paper(s) have no raw references")
    if empty_reference_rows:
        warnings.append(f"{empty_reference_rows} selected-source reference row(s) had empty reference text and were skipped")

    report = {
        "report_version": "normalization_report.schema1",
        "status": "passed" if not blocking_issues else "failed",
        "blocking_issues": blocking_issues,
        "warnings": warnings,
        "informational_notes": [
            f"The master table has {len(nonselected_master_missing_abstracts)} nonselected row(s) without abstracts; they do not affect the selected core set."
        ],
        "paper_rows": len(papers),
        "paper_id_range": [papers[0]["paper_id"], papers[-1]["paper_id"]],
        "papers_with_title": sum(bool(row["title"]) for row in papers),
        "papers_with_abstract": sum(bool(row["abstract"]) for row in papers),
        "papers_with_doi": sum(bool(row["doi"]) for row in papers),
        "papers_with_authors": sum(bool(row["authors"]) for row in papers),
        "year_range": [min(row["year"] for row in papers), max(row["year"] for row in papers)],
        "cross_table_mismatches": cross_table_mismatches,
        "duplicate_normalized_dois": duplicate_dois,
        "selected_reference_rows": len(citations_raw),
        "ignored_nonselected_source_reference_rows": ignored_reference_rows,
        "empty_selected_reference_rows_skipped": empty_reference_rows,
        "duplicate_selected_reference_rows": duplicate_reference_rows,
        "papers_without_raw_references": papers_without_references,
        "raw_reference_count_per_paper": {
            "min": min(reference_count_values),
            "median": statistics.median(reference_count_values),
            "max": max(reference_count_values),
        },
        "reference_relation_values": dict(sorted(relation_values.items())),
        "nonselected_master_rows_without_abstract": nonselected_master_missing_abstracts,
    }

    sample_ids = sample_paper_ids(papers)
    sample_ranks = [p["selection_rank"] for p in papers if p["paper_id"] in sample_ids]
    by_rank = {paper["selection_rank"]: paper for paper in papers}
    sample_rows = [
        [
            paper["paper_id"],
            paper["year"],
            paper["title"],
            paper["doi"],
            len(paper["abstract"]),
            citation_counts[paper["paper_id"]],
        ]
        for rank in sample_ranks
        for paper in [by_rank[rank]]
    ]
    citations_by_paper: dict[str, list[dict[str, Any]]] = {}
    for citation in citations_raw:
        citations_by_paper.setdefault(citation["source_paper_id"], []).append(citation)
    inspection_sample = {
        "sampling_rule": f"Deterministic evenly spaced selection ranks: {sample_ranks}",
        "purpose": "Compare normalized records with their source rows without opening the full data files.",
        "samples": [
            {
                "normalized_paper": paper,
                "selection_source": {
                    "file": CORE_FILE.name,
                    "line": ranked_core[paper["selection_rank"] - 1]["__input_line__"],
                    "record_id": ranked_core[paper["selection_rank"] - 1]["record_id"],
                    "rank": ranked_core[paper["selection_rank"] - 1]["rank"],
                    "title": ranked_core[paper["selection_rank"] - 1]["title"],
                    "year": ranked_core[paper["selection_rank"] - 1].get("year"),
                    "doi": ranked_core[paper["selection_rank"] - 1].get("doi"),
                },
                "master_source": {
                    "file": MASTER_FILE.name,
                    "line": master_by_record[paper["record_id"]]["__input_line__"],
                    "record_id": master_by_record[paper["record_id"]]["record_id"],
                    "title": master_by_record[paper["record_id"]]["title"],
                    "year": master_by_record[paper["record_id"]]["year"],
                    "doi": master_by_record[paper["record_id"]].get("doi"),
                    "abstract": master_by_record[paper["record_id"]]["abstract"],
                },
                "raw_reference_examples": citations_by_paper.get(paper["paper_id"], [])[:2],
            }
            for rank in sample_ranks
            for paper in [by_rank[rank]]
        ],
    }
    review_markdown = "\n".join(
        [
            "# 第一轮：原始输入规范化检查报告",
            "",
            "## 结论",
            "",
            f"自动校验：**{report['status']}**。规范化得到 {len(papers)} 篇 Paper 和 {len(citations_raw)} 条原始引用记录。",
            "",
            "本轮没有读取旧版 Claim、关系或完整图谱，也没有生成新的 Claim 或 Claim 关系。",
            "",
            "## 输入理解与连接",
            "",
            f"- `{CORE_FILE.name}`：决定纳入范围、排序及选择类别。",
            "- `文献主表.jsonl`：提供规范化 Paper 的标题、年份、DOI、作者、期刊和摘要。",
            "- `参考文献关系-原始.jsonl`：每行是一篇来源论文的一条未解析参考文献。",
            "- 连接键：`record_id` / `source_record_id`；不是标题模糊匹配。",
            "",
            "## 完整性摘要",
            "",
            markdown_table(
                [
                    ["Paper", len(papers), f"{report['year_range'][0]}–{report['year_range'][1]}"],
                    ["有摘要 Paper", report["papers_with_abstract"], f"核心集合 {len(papers)}（DOI 可缺省）"],
                    ["有 DOI Paper", report["papers_with_doi"], f"核心集合 {len(papers)}（DOI 可缺省）"],
                    ["原始引用记录", len(citations_raw), f"每篇 {report['raw_reference_count_per_paper']['min']}–{report['raw_reference_count_per_paper']['max']} 条"],
                ],
                ["检查项", "结果", "说明"],
            ),
            "",
            "## 分层抽样（按核心集合规模最多抽取 10 篇，便于重复检查）",
            "",
            markdown_table(sample_rows, ["paper_id", "年份", "标题", "DOI", "摘要字符", "原始引用数"]),
            "",
            "## 异常与提醒",
            "",
            *(f"- {warning}" for warning in warnings),
            *( ["- 未发现需要提醒的异常。"] if not warnings else [] ),
            f"- 非阻断输入事实：文献主表中有 {len(nonselected_master_missing_abstracts)} 条未入选记录缺摘要；核心集合不受影响。",
            "",
            "## 下一轮前建议检查",
            "",
            "1. 打开 `papers.jsonl` 抽查标题、摘要、年份与 DOI。",
            "2. 打开 `citations_raw.jsonl` 抽查 `source_paper_id` 与原始参考文献文本。",
            "3. 可先打开 `inspection_sample.json`，直接对照按核心集合规模最多抽取 10 篇的原始行、规范化结果与引用示例。",
            "4. 检查 `normalization_report.json` 中所有 mismatch、duplicate 和 missing 列表。",
        ]
    )

    write_jsonl(output / "papers.jsonl", papers)
    write_jsonl(output / "paper_provenance.jsonl", provenance)
    write_jsonl(output / "citations_raw.jsonl", citations_raw)
    write_json(output / "inspection_sample.json", inspection_sample)
    report["output_files"] = {
        "papers.jsonl": {"rows": len(papers), "sha256": file_sha256(output / "papers.jsonl")},
        "paper_provenance.jsonl": {
            "rows": len(provenance),
            "sha256": file_sha256(output / "paper_provenance.jsonl"),
        },
        "citations_raw.jsonl": {
            "rows": len(citations_raw),
            "sha256": file_sha256(output / "citations_raw.jsonl"),
        },
        "inspection_sample.json": {
            "samples": len(inspection_sample["samples"]),
            "sha256": file_sha256(output / "inspection_sample.json"),
        },
    }
    write_json(output / "input_profile.json", input_profile)
    write_json(output / "normalization_report.json", report)
    write_text(output / "第一轮检查报告.md", review_markdown)
    write_text(
        workspace / "STATUS.md",
        "\n".join(
            [
                "# Claim 演化知识图谱 状态",
                "",
                "状态：`round_1_completed`（等待用户检查）",
                "",
                f"已完成：原始输入理解、{len(papers)} 篇 Paper 规范化、原始引用保留与第一轮质量审计。",
                "",
                "尚未执行：引用解析、Claim 生成、Claim 关系判断和图谱绘制。",
                "",
                "第一轮检查入口：`01_规范化输入/第一轮检查报告.md`。",
            ]
        ),
    )
    print(f"Round 1 complete: {len(papers)} papers; {len(citations_raw)} raw references -> {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Round 1: profile and normalize raw paper/reference inputs.")
    parser.add_argument("--workspace", type=Path, default=DEFAULT_WORKSPACE)
    args = parser.parse_args()
    run(args.workspace.resolve())
