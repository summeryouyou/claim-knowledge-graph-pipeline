from __future__ import annotations

import hashlib
from collections import Counter

from common import validator_workspace, report_warning, DEFAULT_WORKSPACE, read_json, read_jsonl


RELATION_TYPES = {"SUPPORTS", "EXTENDS", "QUALIFIES", "CHALLENGES", "ALTERNATIVE_TO", "MECHANISM_FOR"}
DIRECTNESS = {"direct", "indirect"}
SCOPE_OVERLAP = {"high", "medium", "low"}
STATUSES = {"accepted", "candidate"}


def clean(row: dict) -> dict:
    return {key: value for key, value in row.items() if key != "__input_line__"}


def run() -> None:
    round1 = DEFAULT_WORKSPACE / "01_规范化输入"
    round4 = DEFAULT_WORKSPACE / "04_Claim复核"
    round5 = DEFAULT_WORKSPACE / "05_关系候选"
    round6 = DEFAULT_WORKSPACE / "06_关系判断"

    papers = [clean(row) for row in read_jsonl(round1 / "papers.jsonl")]
    claims = [clean(row) for row in read_jsonl(round4 / "claims.jsonl")]
    candidates = [clean(row) for row in read_jsonl(round5 / "relation_candidates.jsonl")]
    decisions = [clean(row) for row in read_jsonl(round6 / "relation_decisions.jsonl")]
    relations = [clean(row) for row in read_jsonl(round6 / "relations.jsonl")]
    accepted = [clean(row) for row in read_jsonl(round6 / "accepted_relations.jsonl")]
    provisional = [clean(row) for row in read_jsonl(round6 / "provisional_relations.jsonl")]
    overrides = [clean(row) for row in read_jsonl(round6 / "relation_overrides.jsonl")]
    registry = read_json(round6 / "relation_id_registry.json")
    report = read_json(round6 / "relation_judgment_report.json")
    plan = read_json(round6 / "review_plan.json")
    current_review_hashes = {
        "claims": hashlib.sha256((round4 / "claims.jsonl").read_bytes()).hexdigest(),
        "candidates": hashlib.sha256((round5 / "relation_candidates.jsonl").read_bytes()).hexdigest(),
        "overrides": hashlib.sha256((round6 / "relation_overrides.jsonl").read_bytes()).hexdigest(),
        "domain": hashlib.sha256((DEFAULT_WORKSPACE / "领域配置.json").read_bytes()).hexdigest(),
    }
    if plan["input_hashes"] != current_review_hashes:
        raise ValueError("Relation review plan is stale; rerun stage six before validating")
    attestation = read_json(round6 / "agent_review_attestation.json")
    reviewed_ids = attestation.get("reviewed_candidate_ids")
    if attestation.get("completed_by_agent") is not True or not isinstance(reviewed_ids, list) or len(set(reviewed_ids)) != len(reviewed_ids):
        raise ValueError("Agent relation-review attestation must list unique actually reviewed candidates")
    if not set(plan["required_candidate_ids"]) <= set(reviewed_ids) or not set(reviewed_ids) <= {row["candidate_id"] for row in candidates}:
        raise ValueError("Agent review must cover the required review plan and only known candidates")
    if attestation.get("input_hashes") != plan["input_hashes"] or not isinstance(attestation.get("notes"), str) or not attestation["notes"].strip():
        raise ValueError("Agent review attestation is stale or has no notes")
    if not {row["candidate_id"] for row in overrides} <= set(reviewed_ids):
        raise ValueError("Every Agent override must be included in actual review coverage")

    paper_by_id = {row["paper_id"]: row for row in papers}
    claim_by_id = {row["claim_id"]: row for row in claims}
    candidate_by_id = {row["candidate_id"]: row for row in candidates}
    decision_by_id = {row["candidate_id"]: row for row in decisions}
    relation_by_candidate = {row["candidate_id"]: row for row in relations}

    if len(decision_by_id) != len(decisions) or set(decision_by_id) != set(candidate_by_id):
        raise ValueError("Every candidate must have exactly one decision")
    if len(relation_by_candidate) != len(relations):
        raise ValueError("At most one retained relation is allowed per candidate")

    override_ids = [row.get("candidate_id") for row in overrides]
    if len(set(override_ids)) != len(override_ids) or not set(override_ids) <= set(candidate_by_id):
        raise ValueError("Override IDs must be unique known candidates")
    if any(decision_by_id[row["candidate_id"]] != row for row in overrides):
        raise ValueError("Saved Agent overrides were not applied to decisions; rerun stage six")

    relation_ids: set[str] = set()
    degree: Counter[str] = Counter()
    for candidate_id, decision in decision_by_id.items():
        judgment = decision.get("judgment")
        if judgment not in {"relation", "unrelated"}:
            raise ValueError(f"Invalid judgment: {candidate_id}")
        if judgment == "unrelated":
            if decision.get("annotation_status") != "rejected" or candidate_id in relation_by_candidate:
                raise ValueError(f"Rejected decision leaked into relation layer: {candidate_id}")
            continue
        if candidate_id not in relation_by_candidate:
            raise ValueError(f"Retained decision missing relation row: {candidate_id}")
        relation = relation_by_candidate[candidate_id]
        if any(relation.get(key) != value for key, value in decision.items() if key not in {"judgment", "judgment_method"}):
            raise ValueError(f"Retained decision and relation differ: {candidate_id}")
        source_id, target_id = relation["source_claim_id"], relation["target_claim_id"]
        if source_id not in claim_by_id or target_id not in claim_by_id or source_id == target_id:
            raise ValueError(f"Invalid endpoints: {candidate_id}")
        candidate_endpoints = {candidate_by_id[candidate_id]["claim_id_1"], candidate_by_id[candidate_id]["claim_id_2"]}
        if {source_id, target_id} != candidate_endpoints:
            raise ValueError(f"Relation endpoints differ from candidate: {candidate_id}")
        if relation["relation_type"] not in RELATION_TYPES:
            raise ValueError(f"Unknown relation type: {candidate_id}")
        if relation["directness"] not in DIRECTNESS or relation["scope_overlap"] not in SCOPE_OVERLAP:
            raise ValueError(f"Invalid relation metadata: {candidate_id}")
        if relation["annotation_status"] not in STATUSES or not isinstance(relation.get("reason"), str) or len(relation["reason"]) < 12:
            raise ValueError(f"Relation needs status and concrete rationale: {candidate_id}")
        confidence = float(relation["confidence"])
        if not 0.0 <= confidence <= 1.0:
            raise ValueError(f"Confidence out of range: {candidate_id}")
        if (relation["annotation_status"] == "accepted") != (confidence >= 0.82):
            raise ValueError(f"Confidence/status threshold mismatch: {candidate_id}")

        source = claim_by_id[source_id]
        target = claim_by_id[target_id]
        source_year = int(paper_by_id[source["paper_id"]]["year"])
        target_year = int(paper_by_id[target["paper_id"]]["year"])
        if source["paper_id"] != target["paper_id"] and source_year < target_year:
            raise ValueError(f"Cross-paper relation runs backward in time: {candidate_id}")
        if source["paper_id"] != target["paper_id"] and source_year == target_year:
            directions = candidate_by_id[candidate_id]["citation_directions"]
            expected = f"{source['paper_id']}->{target['paper_id']}"
            if expected not in directions:
                raise ValueError(f"Same-year cross-paper relation lacks matching citation direction: {candidate_id}")

        relation_id = relation["relation_id"]
        if relation_id in relation_ids:
            raise ValueError(f"Duplicate relation ID: {relation_id}")
        relation_ids.add(relation_id)
        assignment = registry["assignments"].get(candidate_id)
        if assignment != {"relation_id": relation_id, "active": True}:
            raise ValueError(f"Registry mismatch: {candidate_id}")
        degree[source_id] += 1
        degree[target_id] += 1

    active_registry = {
        candidate_id for candidate_id, assignment in registry["assignments"].items() if assignment.get("active")
    }
    if active_registry != set(relation_by_candidate):
        raise ValueError("Active registry assignments differ from relation layer")
    if {row["candidate_id"] for row in accepted} != {
        row["candidate_id"] for row in relations if row["annotation_status"] == "accepted"
    }:
        raise ValueError("accepted_relations.jsonl is not an exact partition")
    if {row["candidate_id"] for row in provisional} != {
        row["candidate_id"] for row in relations if row["annotation_status"] == "candidate"
    }:
        raise ValueError("provisional_relations.jsonl is not an exact partition")

    expected_hashes = {
        "decisions": round6 / "relation_decisions.jsonl",
        "relations": round6 / "relations.jsonl",
        "accepted": round6 / "accepted_relations.jsonl",
        "provisional": round6 / "provisional_relations.jsonl",
    }
    for key, path in expected_hashes.items():
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if report["outputs"][key]["rows"] != sum(1 for _ in read_jsonl(path)) or report["outputs"][key]["sha256"] != digest:
            raise ValueError(f"Report count/hash mismatch: {key}")

    isolated = sorted(set(claim_by_id) - set(degree), key=lambda value: int(value[1:]))
    if report["status"] != "passed" or report["candidate_pairs_reviewed"] != len(candidates):
        raise ValueError("Round report status/count mismatch")
    if report["relations_retained"] != len(relations) or report["unrelated_pairs"] != len(candidates) - len(relations):
        raise ValueError("Round report relation totals mismatch")
    if report["isolated_claim_ids_retained_layer"] != isolated:
        raise ValueError("Round report isolated Claim list mismatch")
    if report["override_rows"] != len(overrides):
        raise ValueError("Round report override count mismatch")
    if len(isolated) > 5:
        report_warning("Retained layer has more than five isolates; review recall and semantics, do not force edges")

    print({
        "status": "passed",
        "candidates_decided": len(decisions),
        "relations_retained": len(relations),
        "accepted": len(accepted),
        "provisional": len(provisional),
        "agent_overrides": len(overrides),
        "relation_types_present": dict(sorted(Counter(row["relation_type"] for row in relations).items())),
        "claims_connected_in_retained_layer": len(degree),
        "isolated_claim_ids": isolated,
    })


if __name__ == "__main__":
    DEFAULT_WORKSPACE = validator_workspace()
    run()
