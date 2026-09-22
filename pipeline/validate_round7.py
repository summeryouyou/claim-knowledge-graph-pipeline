from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from collections import Counter

from common import load_domain_profile, validator_workspace, report_warning, DEFAULT_WORKSPACE, read_json, read_jsonl


LANE_IDS = {"L1", "L2", "L3", "L4", "L5", "L6"}
RELATION_TYPES = {"SUPPORTS", "EXTENDS", "QUALIFIES", "CHALLENGES", "ALTERNATIVE_TO", "MECHANISM_FOR"}
SVG_NS = {"svg": "http://www.w3.org/2000/svg"}


def clean(row: dict) -> dict:
    return {key: value for key, value in row.items() if key != "__input_line__"}


def digest(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run() -> None:
    global LANE_IDS
    LANE_IDS = {lane["lane_id"] for lane in load_domain_profile(DEFAULT_WORKSPACE)["lanes"]}
    round1 = DEFAULT_WORKSPACE / "01_规范化输入"
    round4 = DEFAULT_WORKSPACE / "04_Claim复核"
    round6 = DEFAULT_WORKSPACE / "06_关系判断"
    round7 = DEFAULT_WORKSPACE / "07_图谱装配"
    papers = [clean(row) for row in read_jsonl(round1 / "papers.jsonl")]
    claims = [clean(row) for row in read_jsonl(round4 / "claims.jsonl")]
    relations = [clean(row) for row in read_jsonl(round6 / "relations.jsonl")]
    nodes = [clean(row) for row in read_jsonl(round7 / "graph_nodes.jsonl")]
    edges = [clean(row) for row in read_jsonl(round7 / "graph_edges.jsonl")]
    graph = read_json(round7 / "graph.json")
    analysis = read_json(round7 / "evolution_analysis.json")
    components = read_json(round7 / "components.json")["components"]
    layout = read_json(round7 / "layout.json")
    report = read_json(round7 / "graph_assembly_report.json")
    if report["domain_profile_sha256"] != digest(DEFAULT_WORKSPACE / "领域配置.json") or report["lane_override_sha256"] != digest(round7 / "lane_overrides.jsonl"):
        raise ValueError("Domain/lane overrides changed since rendering; rerun stage seven")

    paper_by_id = {row["paper_id"]: row for row in papers}
    claim_by_id = {row["claim_id"]: row for row in claims}
    relation_by_id = {row["relation_id"]: row for row in relations}
    node_by_id = {row["claim_id"]: row for row in nodes}
    edge_by_id = {row["relation_id"]: row for row in edges}
    if len(node_by_id) != len(nodes) or set(node_by_id) != set(claim_by_id):
        raise ValueError("Graph must contain every Claim exactly once")
    if len(edge_by_id) != len(edges) or set(edge_by_id) != set(relation_by_id):
        raise ValueError("Graph edges must exactly match retained Claim relations")
    if graph["nodes"] != nodes or graph["edges"] != edges:
        raise ValueError("graph.json does not reproduce JSONL node/edge layers")

    positions = set()
    for claim_id, node in node_by_id.items():
        claim = claim_by_id[claim_id]
        if node["paper_id"] != claim["paper_id"] or node["normalized_text"] != claim["normalized_text"]:
            raise ValueError(f"Claim provenance mismatch: {claim_id}")
        if node["year"] != int(paper_by_id[claim["paper_id"]]["year"]):
            raise ValueError(f"Claim year mismatch: {claim_id}")
        if node["lane_id"] not in LANE_IDS:
            raise ValueError(f"Invalid lane: {claim_id}")
        position = (node["x"], node["y"])
        if position in positions:
            raise ValueError(f"Overlapping node origin: {claim_id}")
        positions.add(position)
        if not 0 <= node["x"] <= layout["width"] - node["width"] or not 0 <= node["y"] <= layout["height"] - node["height"]:
            raise ValueError(f"Node lies outside SVG layout: {claim_id}")

    status_counts = Counter()
    for relation_id, edge in edge_by_id.items():
        relation = relation_by_id[relation_id]
        if edge["relation_type"] not in RELATION_TYPES or edge["relation_type"] != relation["relation_type"]:
            raise ValueError(f"Relation type mismatch: {relation_id}")
        if edge["semantic_source_claim_id"] != relation["source_claim_id"] or edge["semantic_target_claim_id"] != relation["target_claim_id"]:
            raise ValueError(f"Semantic direction mismatch: {relation_id}")
        if edge["history_from_claim_id"] != relation["target_claim_id"] or edge["history_to_claim_id"] != relation["source_claim_id"]:
            raise ValueError(f"History display direction is not the exact semantic reverse: {relation_id}")
        old = node_by_id[edge["history_from_claim_id"]]
        new = node_by_id[edge["history_to_claim_id"]]
        if old["paper_id"] != new["paper_id"] and old["year"] > new["year"]:
            raise ValueError(f"History arrow runs backward in time: {relation_id}")
        status_counts[edge["annotation_status"]] += 1

    component_claims = [claim_id for component in components for claim_id in component["claim_ids"]]
    if len(component_claims) != len(set(component_claims)) or set(component_claims) != set(claim_by_id):
        raise ValueError("Component partition must cover each Claim exactly once")
    if any(component["size"] != len(component["claim_ids"]) for component in components):
        raise ValueError("Component size mismatch")

    retained = analysis["retained_graph"]
    longest = retained["longest_temporal_path"]
    path_pairs = list(zip(longest["claim_ids"], longest["claim_ids"][1:]))
    history_pairs = {(edge["history_from_claim_id"], edge["history_to_claim_id"]) for edge in edges}
    if longest["edge_count"] != len(path_pairs) or any(pair not in history_pairs for pair in path_pairs):
        raise ValueError("Longest temporal path is not reproducible from graph edges")
    if any(node_by_id[a]["year"] >= node_by_id[b]["year"] for a, b in path_pairs):
        raise ValueError("Temporal path must advance strictly in year")
    if retained["directed_cycles"]:
        report_warning("Directed cycles present; inspect same-year/same-paper dependencies; do not silently remove edges")
    if longest["edge_count"] < 3 or not retained["multi_parent_claims"] or not retained["branch_claims"]:
        report_warning("Multi-level evolution, convergence, or branching limited by corpus; document this limitation")
    if components[0]["size"] / len(nodes) < 0.75 or len(retained["isolated_claim_ids"]) > 5:
        report_warning("Graph fragmented: inspect corpus coverage and semantic support, never force connectivity")

    expected_files = {
        "layout": round7 / "layout.json",
        "nodes": round7 / "graph_nodes.jsonl",
        "edges": round7 / "graph_edges.jsonl",
        "graph": round7 / "graph.json",
        "analysis": round7 / "evolution_analysis.json",
        "components": round7 / "components.json",
        "full_svg": round7 / "完整Claim时间泳道式演化网络.svg",
        "accepted_svg": round7 / "正式关系骨架.svg",
    }
    for key, path in expected_files.items():
        if report["outputs"][key]["sha256"] != digest(path):
            raise ValueError(f"Output hash mismatch: {key}")

    full_root = ET.parse(expected_files["full_svg"]).getroot()
    full_nodes = full_root.findall(".//svg:g[@class='node']", SVG_NS)
    full_edges = full_root.findall(".//svg:path[@class='edge']", SVG_NS)
    if len(full_nodes) != len(nodes) or len(full_edges) != len(edges):
        raise ValueError("Full SVG node/edge count mismatch")
    if {element.attrib["data-claim-id"] for element in full_nodes} != set(claim_by_id):
        raise ValueError("Full SVG Claim IDs mismatch")
    if {element.attrib["data-relation-id"] for element in full_edges} != set(relation_by_id):
        raise ValueError("Full SVG relation IDs mismatch")
    if full_root.findall(".//svg:script", SVG_NS):
        raise ValueError("SVG must remain self-contained and script-free")

    accepted_ids = {row["relation_id"] for row in relations if row["annotation_status"] == "accepted"}
    accepted_claims = {
        endpoint for row in relations if row["annotation_status"] == "accepted"
        for endpoint in (row["source_claim_id"], row["target_claim_id"])
    }
    accepted_root = ET.parse(expected_files["accepted_svg"]).getroot()
    if len(accepted_root.findall(".//svg:g[@class='node']", SVG_NS)) != len(accepted_claims):
        raise ValueError("Accepted SVG Claim count mismatch")
    if {element.attrib["data-relation-id"] for element in accepted_root.findall(".//svg:path[@class='edge']", SVG_NS)} != accepted_ids:
        raise ValueError("Accepted SVG relation set mismatch")

    if report["status"] != "passed" or report["paper_nodes"] != 0 or report["citation_edges"] != 0:
        raise ValueError("Final report must exclude Paper nodes and citation edges")
    if report["claim_nodes"] != len(nodes) or report["retained_relations"] != len(edges):
        raise ValueError("Final report graph totals mismatch")
    if report["accepted_relations"] != status_counts["accepted"] or report["provisional_relations"] != status_counts["candidate"]:
        raise ValueError("Final report status partition mismatch")
    status_text = (DEFAULT_WORKSPACE / "STATUS.md").read_text(encoding="utf-8")
    if "状态：`completed`" not in status_text and "状态：`awaiting_validation`" not in status_text:
        raise ValueError("Graph assembly has not reached validation stage")

    print({
        "status": "passed",
        "claim_nodes": len(nodes),
        "claim_relations": len(edges),
        "paper_nodes": 0,
        "citation_edges": 0,
        "largest_component": components[0]["size"],
        "weak_components": len(components),
        "longest_temporal_path_edges": longest["edge_count"],
        "multi_parent_claims": len(retained["multi_parent_claims"]),
        "branch_claims": len(retained["branch_claims"]),
        "full_svg_size": [report["outputs"]["full_svg"]["width"], report["outputs"]["full_svg"]["height"]],
    })


if __name__ == "__main__":
    DEFAULT_WORKSPACE = validator_workspace()
    run()
