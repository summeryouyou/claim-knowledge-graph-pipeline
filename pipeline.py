"""Validated, resumable entry point for the seven-stage Agent workflow."""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "pipeline"))
from common import DEFAULT_WORKSPACE, discover_core_file, file_sha256, load_domain_profile, read_json, write_json, write_text

STAGES = {
    1: ("01_normalize_inputs.py", "01_规范化输入"),
    2: ("02_build_citation_backbone.py", "02_引用骨架"),
    3: ("03_claim_generation.py", "03_Claim生成"),
    4: ("04_claim_review.py", "04_Claim复核"),
    5: ("05_generate_relation_candidates.py", "05_关系候选"),
    6: ("06_judge_relations.py", "06_关系判断"),
    7: ("07_assemble_and_render.py", "07_图谱装配"),
}


def input_hashes() -> dict[str, str]:
    paths = [discover_core_file(), ROOT / "data/文献主表.jsonl", ROOT / "data/参考文献关系-原始.jsonl"]
    return {p.name: file_sha256(p) for p in paths}


def code_hash() -> str:
    paths = sorted((ROOT / "pipeline").glob("*.py")) + [ROOT / "pipeline.py", ROOT / "Claim知识谱系设计.md"]
    value = {p.name: file_sha256(p) for p in paths}
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode("utf-8")).hexdigest()


def output_hashes(workspace: Path, stage: int) -> dict[str, str]:
    directory = workspace / STAGES[stage][1]
    # Archive, execution notes and tool logs are not consumed as scientific inputs.
    paths = sorted(p for p in directory.rglob("*") if p.is_file()
                   and "任务归档" not in p.parts
                   and p.suffix in {".json", ".jsonl", ".svg"}
                   and p.name != "validation_result.json")
    snapshots = [p for p in directory.glob("设计文档快照.md")]
    result = {str(p.relative_to(workspace)): file_sha256(p) for p in paths + snapshots}
    if stage >= 5:
        result["领域配置.json"] = file_sha256(workspace / "领域配置.json")
    return result


def state_path(workspace: Path) -> Path:
    return workspace / "run_state.json"


def check_state(workspace: Path) -> dict:
    path = state_path(workspace)
    if not path.is_file():
        raise ValueError("Run python pipeline.py init first")
    state = read_json(path)
    if state["input_hashes"] != input_hashes():
        status_markdown(workspace, state, "stale", "原始输入已改变；旧结果不能作为本任务完成图。")
        raise ValueError("data/ changed: use init --new-task to archive the previous run, or restore the original inputs")
    if state["code_hash"] != code_hash():
        status_markdown(workspace, state, "stale", "程序或设计规范已改变；需要新任务初始化。")
        raise ValueError("Pipeline/specification changed: initialize a new task instead of mixing implementations")
    return state


def check_predecessors(workspace: Path, state: dict, stage: int) -> None:
    for previous in range(1, stage):
        record = state["stages"].get(str(previous))
        if record is None:
            status_markdown(workspace, state, "stale", f"第 {previous} 轮尚未校验，不能推进第 {stage} 轮。")
            raise ValueError(f"Stage {previous} is not validated; finish it before stage {stage}")
        if record["output_hashes"] != output_hashes(workspace, previous):
            status_markdown(workspace, state, "stale", f"第 {previous} 轮文件已改变；下游结果需要重跑。")
            raise ValueError(f"Stage {previous} outputs changed; rerun/validate that stage before proceeding")


def status_markdown(workspace: Path, state: dict, status: str, detail: str) -> None:
    state["status"] = status
    state["detail"] = detail
    write_json(state_path(workspace), state)
    write_text(workspace / "STATUS.md", "# Claim 演化知识图谱状态\n\n"
               f"状态：`{status}`\n\n{detail}\n\n"
               f"已校验轮次：{', '.join(sorted(state['stages'], key=int)) or '无'}。\n")


def initialize(workspace: Path, new_task: bool) -> None:
    inputs = input_hashes()  # Fail without touching output if raw inputs are missing/ambiguous.
    signature = code_hash()
    if state_path(workspace).is_file():
        old = read_json(state_path(workspace))
        if old["input_hashes"] == inputs and old["code_hash"] == signature and not new_task:
            print(json.dumps(old, ensure_ascii=False, indent=2))
            return
    if workspace.exists() and any(workspace.iterdir()):
        if not new_task:
            raise ValueError("Existing results must be archived: run init --new-task, or choose an empty output directory")
        # Only move the named result directory within this project; never the project root/data.
        if workspace.parent != ROOT or workspace == ROOT or workspace.name in {"data", "pipeline", "归档"}:
            raise ValueError("Automatic archive requires a direct, non-reserved child result directory of the project root")
        archive = ROOT / "归档" / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        if not archive.resolve().is_relative_to(ROOT):
            raise ValueError("Archive path resolves outside the project")
        archive.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(workspace), str(archive))
        print(f"Previous results archived recoverably: {archive}")
    state = {"input_hashes": inputs, "code_hash": signature, "stages": {}, "status": "initialized"}
    status_markdown(workspace, state, "initialized", "原始输入已冻结；等待执行第一轮。")


def invoke(workspace: Path, filename: str, arguments: list[str]) -> str:
    command = [sys.executable, "-B", "-X", "utf8", str(ROOT / "pipeline" / filename), *arguments,
               "--workspace", str(workspace)]
    process = subprocess.run(command, cwd=ROOT, text=True, encoding="utf-8", errors="replace",
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    print(process.stdout, end="")
    if process.returncode:
        raise RuntimeError(f"Command failed ({process.returncode}): {filename}")
    return process.stdout


def validate(workspace: Path, stage: int, state: dict) -> None:
    check_predecessors(workspace, state, stage)
    text = invoke(workspace, f"validate_round{stage}.py", [])
    directory = workspace / STAGES[stage][1]
    write_json(directory / "validation_result.json", {"status": "passed", "output": text,
                                                       "semantic_gold_standard": False})
    for key in list(state["stages"]):
        if int(key) >= stage:
            del state["stages"][key]
    state["stages"][str(stage)] = {"output_hashes": output_hashes(workspace, stage)}
    status = "completed" if stage == 7 else f"round_{stage}_completed"
    detail = "七轮执行与数据装配校验完成；candidate 仍是待复核关系，不是科学真值。" if stage == 7 else f"第 {stage} 轮已通过校验；等待检查后进入下一轮。"
    status_markdown(workspace, state, status, detail)


def run_stage(workspace: Path, stage: int, action: str | None, batch_size: int | None) -> None:
    state = check_state(workspace)
    check_predecessors(workspace, state, stage)
    permitted = {2: {"prepare", "finalize"}, 3: {"prepare", "collect"}, 4: {"prepare", "collect"}}
    if stage in permitted and action not in permitted[stage]:
        raise ValueError(f"Stage {stage} requires one of {sorted(permitted[stage])}")
    if stage not in permitted and action is not None:
        raise ValueError(f"Stage {stage} has no action argument")
    if batch_size is not None and (batch_size < 1 or stage not in {3, 6}):
        raise ValueError("Positive --batch-size is available only for stages 3 and 6")
    for key in list(state["stages"]):
        if int(key) >= stage:
            del state["stages"][key]
    status_markdown(workspace, state, "running", f"正在执行第 {stage} 轮；下游旧完成状态已失效。")
    arguments = [action] if action else []
    if batch_size is not None:
        arguments += ["--batch-size", str(batch_size)]
    try:
        invoke(workspace, STAGES[stage][0], arguments)
        if action == "prepare":
            status_markdown(workspace, state, "agent_required", f"第 {stage} 轮任务已准备；按本轮 AGENT_TASK.md 完成全部输出，再 finalize/collect。")
        elif stage == 6 and not (workspace / "06_关系判断/agent_review_attestation.json").is_file():
            status_markdown(workspace, state, "agent_required", "第六轮规则初判已生成；完成 review_plan.json 的定点复核、覆盖与 Agent 复核确认，再重跑第六轮。")
        else:
            validate(workspace, stage, state)
    except Exception:
        status_markdown(workspace, state, "failed", f"第 {stage} 轮执行或校验失败；修正本轮源输入/决定后重跑，禁止继续使用旧下游结果。")
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=DEFAULT_WORKSPACE)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("--new-task", action="store_true")
    run = commands.add_parser("run")
    run.add_argument("stage", type=int, choices=range(1, 8))
    run.add_argument("action", nargs="?", choices=("prepare", "collect", "finalize"))
    run.add_argument("--batch-size", type=int)
    check = commands.add_parser("validate")
    check.add_argument("stage", type=int, choices=range(1, 8))
    commands.add_parser("status")
    commands.add_parser("prepare-domain")
    commands.add_parser("domain-inputs")
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    if workspace == ROOT or any(workspace.is_relative_to(path) for path in (ROOT / "data", ROOT / "pipeline", ROOT / "归档")) or not workspace.is_relative_to(ROOT):
        raise ValueError("Output must be a non-reserved directory inside this project")
    if args.command == "init":
        initialize(workspace, args.new_task)
    elif args.command == "run":
        run_stage(workspace, args.stage, args.action, args.batch_size)
    elif args.command == "validate":
        state = check_state(workspace)
        try:
            validate(workspace, args.stage, state)
        except Exception:
            for key in list(state["stages"]):
                if int(key) >= args.stage:
                    del state["stages"][key]
            status_markdown(workspace, state, "failed", f"第 {args.stage} 轮校验失败。")
            raise
    elif args.command in {"prepare-domain", "domain-inputs"}:
        state = check_state(workspace)
        check_predecessors(workspace, state, 5)
        if args.command == "domain-inputs":
            print(json.dumps({
                "source_papers_sha256": file_sha256(workspace / "01_规范化输入/papers.jsonl"),
                "source_claims_sha256": file_sha256(workspace / "04_Claim复核/claims.jsonl"),
            }, ensure_ascii=False, indent=2))
            return
        target = workspace / "领域配置.json"
        if target.is_file():
            print(f"Existing profile preserved; review it rather than overwriting: {target}")
        else:
            profile = read_json(ROOT / "领域配置模板.json")
            profile["source_papers_sha256"] = file_sha256(workspace / "01_规范化输入/papers.jsonl")
            profile["source_claims_sha256"] = file_sha256(workspace / "04_Claim复核/claims.jsonl")
            write_json(target, profile)
            print(f"Complete this profile using the current collection: {target}")
    else:
        state = check_state(workspace)
        for stage, record in state["stages"].items():
            if record["output_hashes"] != output_hashes(workspace, int(stage)):
                state["status"] = "stale"
                state["detail"] = f"第 {stage} 轮文件已变化，需要重跑/校验。"
                break
        if state["status"] == "stale":
            status_markdown(workspace, state, "stale", state["detail"])
        print(json.dumps(state, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
