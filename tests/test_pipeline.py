"""Isolated synthetic integration checks; never populate the project's data/."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

TEMPLATE = Path(__file__).resolve().parents[1]


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def rows(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class PipelineChecks(unittest.TestCase):
    def copy_tool(self, parent: Path) -> Path:
        root = parent / "task"
        root.mkdir()
        shutil.copytree(TEMPLATE / "pipeline", root / "pipeline", ignore=shutil.ignore_patterns("__pycache__"))
        for name in ("pipeline.py", "Claim知识谱系设计.md", "领域配置模板.json"):
            shutil.copy2(TEMPLATE / name, root / name)
        (root / "data").mkdir()
        return root

    def command(self, root: Path, *arguments: str, success=True):
        command = [sys.executable, "-B", "-X", "utf8", str(root / "pipeline.py"), *arguments]
        process = subprocess.run(command, cwd=root, text=True, encoding="utf-8", stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if success:
            self.assertEqual(process.returncode, 0, process.stdout)
        else:
            self.assertNotEqual(process.returncode, 0, process.stdout)
        return process

    def fixture(self, root: Path, count: int, term: str):
        core = []
        master = []
        references = []
        for i in range(1, count + 1):
            metadata = {"record_id": f"SYN:{i}", "title": f"Synthetic {term} study {i}", "year": 2000 + min(i, 20),
                        "doi": f"10.9999/synthetic{i}", "authors": [f"Author{i}, A"], "source_title": "Synthetic Journal",
                        "abstract": f"Synthetic evidence {term} {i} has an explicitly limited conclusion."}
            core.append({**metadata, "rank": i})
            master.append(metadata)
            if i > 1:
                references.append({"source_record_id": f"SYN:{i}", "target_reference_raw": f"Author{i-1} A, {2000+min(i-1,20)}, SYNTHETIC JOURNAL, DOI 10.9999/synthetic{i-1}"})
        # Peripheral records are deliberately incomplete; they must not affect core membership.
        master.extend([{"record_id": "OUTSIDE:1"}, {"record_id": "OUTSIDE:2", "title": "Not selected"}])
        references.append({"source_record_id": "OUTSIDE:1"})
        write_jsonl(root / f"data/核心文献{count}篇.jsonl", core)
        write_jsonl(root / "data/文献主表.jsonl", master)
        write_jsonl(root / "data/参考文献关系-原始.jsonl", references)

    def run_fixture(self, count: int, term: str, split_first=False):
        with tempfile.TemporaryDirectory(prefix="claim-pipeline-") as temporary:
            root = self.copy_tool(Path(temporary))
            self.fixture(root, count, term)
            work = root / "知识图谱构建过程"
            self.command(root, "init")
            self.command(root, "run", "1")
            self.assertEqual(len(rows(work / "01_规范化输入/papers.jsonl")), count)
            self.assertEqual(len(rows(work / "01_规范化输入/citations_raw.jsonl")), count - 1)
            self.command(root, "run", "2", "prepare")
            self.assertEqual(rows(work / "02_引用骨架/citation_review_queue.jsonl"), [])
            self.command(root, "run", "2", "finalize")
            self.command(root, "run", "3", "prepare", "--batch-size", "5")
            manifest = read(work / "03_Claim生成/task_manifest.json")
            for name in manifest["batches"]:
                batch = read(work / f"03_Claim生成/task_batches/{name}.json")
                output = []
                for paper in batch["papers"]:
                    self.assertEqual(set(paper), {"paper_id", "title", "abstract"})
                    output.append({"paper_id": paper["paper_id"], "claims": [{
                        "normalized_text": f"在合成验证情境中，{term}研究报告一个有范围限制的结果。",
                        "claim_type": "empirical", "claim_status": "affirmative",
                        "scope": {"population": None, "modality": None, "paradigm": "合成验证", "measurement_level": "模拟", "conditions": None},
                        "evidence_basis": ["simulation"], "evidence_spans": [{"text": paper["abstract"], "location": "abstract"}],
                        "selection_reason": "合成接口验证，不是科学生成结果"}]})
                write_jsonl(work / f"03_Claim生成/agent_outputs/{name}.jsonl", output)
            self.command(root, "run", "3", "collect")
            self.command(root, "run", "4", "prepare")
            manifest = read(work / "04_Claim复核/task_manifest.json")
            for name in manifest["batches"]:
                batch = read(work / f"04_Claim复核/task_batches/{name}.json")
                keys = [draft["claim_key"] for paper in batch["papers"] for draft in paper["claim_drafts"]]
                changes = []
                if split_first and name == manifest["batches"][0]:
                    draft = batch["papers"][0]["claim_drafts"][0]
                    replacements = []
                    fields = ("normalized_text", "claim_type", "claim_status", "scope", "evidence_basis", "evidence_spans", "selection_reason")
                    for number in (1, 2):
                        replacement = {field: draft[field] for field in fields}
                        replacement.update({"review_claim_key": f"P001-R{number:02d}", "normalized_text": f"{term}合成验证的第{number}个独立接口命题。"})
                        replacements.append(replacement)
                    changes = [{"source_claim_keys": [draft["claim_key"]], "action": "split", "reason": "Synthetic split interface check", "replacement_claims": replacements}]
                write_json(work / f"04_Claim复核/agent_outputs/{name}.json", {
                    "task_id": name, "reviewed_claim_keys": keys, "changes": changes, "batch_notes": "Synthetic interface check only"})
            self.command(root, "run", "4", "collect")
            original_ids = [r["claim_id"] for r in rows(work / "04_Claim复核/claims.jsonl")]
            self.command(root, "run", "4", "collect")
            self.assertEqual(original_ids, [r["claim_id"] for r in rows(work / "04_Claim复核/claims.jsonl")])
            self.command(root, "prepare-domain")
            profile = read(work / "领域配置.json")
            profile.update({"prepared_by_agent": True, "topic_terms": [term], "generic_topics": [], "frequency_topics": [],
                            "lanes": [{"lane_id": "GENERAL", "lane_name": "合成验证主题"}], "lane_rules": [], "default_lane_id": "GENERAL"})
            write_json(work / "领域配置.json", profile)
            self.command(root, "run", "5")
            self.command(root, "run", "6")
            self.assertEqual(read(work / "run_state.json")["status"], "agent_required")
            self.command(root, "run", "7", success=False)
            plan = read(work / "06_关系判断/review_plan.json")
            write_json(work / "06_关系判断/agent_review_attestation.json", {
                "completed_by_agent": True, "reviewed_candidate_ids": plan["required_candidate_ids"],
                "input_hashes": plan["input_hashes"], "notes": "Synthetic interface attestation, not actual semantic validation"})
            self.command(root, "run", "6")
            # A modified override file invalidates both the plan and Agent confirmation.
            override_file = work / "06_关系判断/relation_overrides.jsonl"
            retained = rows(work / "06_关系判断/relations.jsonl")
            if retained:
                write_jsonl(override_file, [{"candidate_id": retained[0]["candidate_id"], "judgment": "unrelated", "annotation_status": "rejected", "reason": "Synthetic override rejection check only", "judgment_method": "agent_manual_override"}])
            else:
                override_file.write_text("\n", encoding="utf-8")
            self.command(root, "validate", "6", success=False)
            self.command(root, "run", "6", success=False)
            plan = read(work / "06_关系判断/review_plan.json")
            reviewed = set(plan["required_candidate_ids"]) | {row["candidate_id"] for row in rows(override_file)}
            write_json(work / "06_关系判断/agent_review_attestation.json", {
                "completed_by_agent": True, "reviewed_candidate_ids": sorted(reviewed), "input_hashes": plan["input_hashes"],
                "notes": "Synthetic refreshed review confirmation"})
            self.command(root, "run", "6")
            self.command(root, "run", "7")
            graph = read(work / "07_图谱装配/graph.json")
            self.assertEqual(len(graph["nodes"]), count + int(split_first))
            self.assertEqual(read(work / "run_state.json")["status"], "completed")
            self.assertTrue((work / "07_图谱装配/正式关系骨架.svg").is_file())
            before = (work / "07_图谱装配/完整Claim时间泳道式演化网络.svg").read_bytes()
            self.command(root, "run", "7")
            self.assertEqual(before, (work / "07_图谱装配/完整Claim时间泳道式演化网络.svg").read_bytes())
            # Stale upstream scientific content must block downstream rendering.
            claims_file = work / "04_Claim复核/claims.jsonl"
            claims_file.write_text(claims_file.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            self.command(root, "run", "7", success=False)
            # Replacing raw data requires a fresh, recoverably archived run.
            with (root / "data/文献主表.jsonl").open("a", encoding="utf-8") as handle:
                handle.write("\n")
            self.command(root, "status", success=False)
            self.command(root, "init", "--new-task")
            self.assertEqual(read(work / "run_state.json")["stages"], {})
            self.assertTrue(any((root / "归档").iterdir()))

    def test_single_paper_zero_citations_and_zero_edges(self):
        self.run_fixture(1, "材料")

    def test_small_biology_collection_with_peripheral_records(self):
        self.run_fixture(3, "细胞", split_first=True)

    def test_multiple_batches_computing_collection(self):
        self.run_fixture(12, "计算")

    def test_core_quantity_is_not_fixed_or_inferred_from_filename(self):
        with tempfile.TemporaryDirectory(prefix="claim-quantity-") as temporary:
            root = self.copy_tool(Path(temporary))
            self.fixture(root, 137, "测试")
            (root / "data/核心文献137篇.jsonl").rename(root / "data/核心文献n篇.jsonl")
            self.command(root, "init")
            self.command(root, "run", "1")
            self.assertEqual(len(rows(root / "知识图谱构建过程/01_规范化输入/papers.jsonl")), 137)

    def test_self_and_future_citations_require_queue_decisions(self):
        with tempfile.TemporaryDirectory(prefix="claim-citation-") as temporary:
            root = self.copy_tool(Path(temporary))
            self.fixture(root, 2, "测试")
            path = root / "data/参考文献关系-原始.jsonl"
            references = rows(path) + [
                {"source_record_id": "SYN:1", "target_reference_raw": "Self, 2001, JOURNAL, DOI 10.9999/synthetic1"},
                {"source_record_id": "SYN:1", "target_reference_raw": "Future, 2002, JOURNAL, DOI 10.9999/synthetic2"}]
            write_jsonl(path, references)
            self.command(root, "init")
            self.command(root, "run", "1")
            self.command(root, "run", "2", "prepare")
            stage = root / "知识图谱构建过程/02_引用骨架"
            queue = rows(stage / "citation_review_queue.jsonl")
            self.assertEqual(len(queue), 2)
            write_jsonl(stage / "citation_review_decisions.jsonl", [{"reference_id": row["reference_id"], "decision": "reject", "target_paper_id": None, "review_note": "Synthetic self/future rejection"} for row in queue])
            self.command(root, "run", "2", "finalize")
            self.assertEqual(len(rows(stage / "cites.jsonl")), 1)

    def test_ambiguous_input_and_missing_selected_metadata(self):
        with tempfile.TemporaryDirectory(prefix="claim-input-") as temporary:
            root = self.copy_tool(Path(temporary))
            self.fixture(root, 2, "测试")
            shutil.copy2(root / "data/核心文献2篇.jsonl", root / "data/核心文献n篇.jsonl")
            self.command(root, "init", success=False)
            (root / "data/核心文献n篇.jsonl").unlink()
            write_jsonl(root / "data/文献主表.jsonl", [{"record_id": "SYN:1", "title": "Missing abstract", "year": 2001}])
            self.command(root, "init")
            self.command(root, "run", "1", success=False)
            self.assertNotIn("1", read(root / "知识图谱构建过程/run_state.json")["stages"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
