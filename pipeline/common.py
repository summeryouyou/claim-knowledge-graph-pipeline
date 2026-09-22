from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


PIPELINE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PIPELINE_DIR.parent
RAW_DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_WORKSPACE = PROJECT_ROOT / "知识图谱构建过程"

DOI_PATTERN = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.IGNORECASE)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON at {path}:{line_number}: {exc}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"Expected one JSON object at {path}:{line_number}")
            value["__input_line__"] = line_number
            rows.append(value)
    return rows


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value.rstrip() + "\n", encoding="utf-8", newline="\n")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def clean_text(value: object) -> str:
    return re.sub(r"\s+", " ", "" if value is None else str(value)).strip()


def normalize_doi(value: object) -> str | None:
    text = clean_text(value).lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "http://dx.doi.org/", "doi:"):
        if text.startswith(prefix):
            text = text[len(prefix) :].strip()
    text = text.rstrip(".,;:)\\]}")
    return text or None


def extract_dois(text: str) -> list[str]:
    return sorted({doi for match in DOI_PATTERN.findall(text) if (doi := normalize_doi(match))})


def discover_core_file() -> Path:
    matches = sorted(RAW_DATA_DIR.glob("核心文献*篇.jsonl"))
    if len(matches) != 1:
        raise ValueError(f"data/ must contain exactly one 核心文献*篇.jsonl; found {[p.name for p in matches]}")
    return matches[0]


def sample_paper_ids(papers: list[dict[str, Any]], limit: int = 10) -> list[str]:
    ordered = sorted(papers, key=lambda p: (p["selection_rank"], p["paper_id"]))
    count = min(limit, len(ordered))
    if not count:
        return []
    indices = [0] if count == 1 else [round(i * (len(ordered) - 1) / (count - 1)) for i in range(count)]
    return [ordered[i]["paper_id"] for i in indices]


def fingerprint(paths: list[Path], extra: object = None) -> str:
    values = [(str(path.resolve()), file_sha256(path)) for path in paths]
    return hashlib.sha256(json.dumps([values, extra], ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def prepare_task_generation(output: Path, signature: str) -> None:
    from datetime import datetime
    import shutil
    marker = output / "task_input_fingerprint.json"
    previous = read_json(marker).get("signature") if marker.is_file() else None
    if previous != signature:
        for name in ("task_batches", "agent_outputs"):
            target = output / name
            if target.is_dir() and any(target.iterdir()):
                archive = output / "任务归档" / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                if not target.resolve().is_relative_to(output.resolve()) or not archive.resolve().is_relative_to(output.resolve()):
                    raise ValueError("Task archive paths resolve outside the stage directory")
                archive.mkdir(parents=True, exist_ok=True)
                shutil.move(str(target), str(archive / name))
    write_json(marker, {"signature": signature})


def assert_task_fingerprint(output: Path, signature: str) -> None:
    marker = output / "task_input_fingerprint.json"
    if not marker.is_file() or read_json(marker).get("signature") != signature:
        raise ValueError("Task input changed; rerun prepare and review affected Agent outputs before collect")


def prune_generated_batches(directory: Path, names: set[str]) -> None:
    from datetime import datetime
    import shutil
    stale = [p for p in directory.glob("batch_*.json") if p.name not in names]
    if stale:
        archive = directory.parent / "任务归档" / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        if not archive.resolve().is_relative_to(directory.parent.resolve()):
            raise ValueError("Batch archive path resolves outside the stage directory")
        archive.mkdir(parents=True, exist_ok=True)
        for path in stale:
            shutil.move(str(path), str(archive / path.name))


def load_domain_profile(workspace: Path) -> dict[str, Any]:
    path = workspace / "领域配置.json"
    if not path.is_file():
        raise ValueError("Missing 领域配置.json: complete Agent domain preparation after Claim review")
    profile = read_json(path)
    if profile.get("prepared_by_agent") is not True:
        raise ValueError("Agent must complete and attest domain configuration")
    required_lists = ("topic_terms", "generic_topics", "frequency_topics", "mechanism_cues", "challenge_cues", "english_stopwords")
    for key in required_lists:
        values = profile.get(key)
        if not isinstance(values, list) or any(not isinstance(v, str) or not v.strip() for v in values):
            raise ValueError(f"Invalid domain field: {key}")
    if not profile["topic_terms"] or not profile["mechanism_cues"] or not profile["challenge_cues"]:
        raise ValueError("Topic and language cues cannot be empty")
    if any(term != term.casefold() for term in profile["topic_terms"]):
        raise ValueError("topic_terms must be casefolded for deterministic matching")
    if not set(profile["generic_topics"]) <= set(profile["topic_terms"]) or not set(profile["frequency_topics"]) <= set(profile["topic_terms"]):
        raise ValueError("Generic and frequency/category terms must be subsets of topic_terms")
    lanes = profile.get("lanes")
    if not isinstance(lanes, list) or not lanes:
        raise ValueError("At least one presentation lane is required")
    ids = [lane.get("lane_id") for lane in lanes]
    if any(not isinstance(lane.get("lane_id"), str) or not lane["lane_id"] or not isinstance(lane.get("lane_name"), str) or not lane["lane_name"] for lane in lanes) or len(set(ids)) != len(ids):
        raise ValueError("Lane identifiers/names must be nonempty and identifiers unique")
    if profile.get("default_lane_id") not in ids:
        raise ValueError("Unknown default lane")
    rules = profile.get("lane_rules")
    if not isinstance(rules, list):
        raise ValueError("lane_rules must be an ordered array")
    for rule in rules:
        if rule.get("lane_id") not in ids:
            raise ValueError("Unknown rule lane")
        for key in ("claim_types", "claim_statuses", "keywords"):
            values = rule.get(key, [])
            if not isinstance(values, list) or any(not isinstance(v, str) or not v for v in values):
                raise ValueError(f"Invalid lane rule {key}")
        if not any(rule.get(k) for k in ("claim_types", "claim_statuses", "keywords")):
            raise ValueError("An unconditional rule would hide all later lane rules; use default_lane_id")
    sources = {
        "source_papers_sha256": workspace / "01_规范化输入" / "papers.jsonl",
        "source_claims_sha256": workspace / "04_Claim复核" / "claims.jsonl",
    }
    for key, source in sources.items():
        if profile.get(key) != file_sha256(source):
            raise ValueError(f"Domain configuration is stale: {key}; regenerate from current inputs")
    return profile


def report_warning(message: str) -> None:
    print(f"WARNING: {message}")


def validator_workspace() -> Path:
    import argparse
    parser = argparse.ArgumentParser(description="Validate a completed pipeline stage")
    parser.add_argument("--workspace", type=Path, default=DEFAULT_WORKSPACE)
    return parser.parse_args().workspace.resolve()
