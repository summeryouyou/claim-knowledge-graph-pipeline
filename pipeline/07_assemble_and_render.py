from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict, deque
from html import escape
from pathlib import Path
from typing import Any, Iterable

from common import load_domain_profile, DEFAULT_WORKSPACE, file_sha256, read_jsonl, write_json, write_jsonl, write_text


LANES: list[tuple[str, str]] = []
DOMAIN_PROFILE: dict[str, Any] = {}
LANE_NAMES = dict(LANES)
RELATION_COLORS = {
    "SUPPORTS": "#2864dc",
    "EXTENDS": "#16865f",
    "QUALIFIES": "#bd7000",
    "CHALLENGES": "#bd3545",
    "ALTERNATIVE_TO": "#8a4fb5",
    "MECHANISM_FOR": "#6546b8",
}
TYPE_COLORS = {
    "empirical": "#2864dc",
    "interpretation": "#6546b8",
    "theoretical": "#16865f",
    "synthesis": "#16865f",
    "methodological": "#bd7000",
}
LANE_FILLS = ["#f7f9fc", "#ffffff", "#f8faf7", "#ffffff", "#faf8fc", "#fffaf2"]
NODE_WIDTH = 292
NODE_HEIGHT = 88
X_STEP = 330
ROW_STEP = 104
LEFT_MARGIN = 350
RIGHT_MARGIN = 160
TOP_MARGIN = 250
BOTTOM_MARGIN = 150
LANE_HEADER = 76
MIN_LANE_HEIGHT = 300


def clean(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key != "__input_line__"}


def classify_lane(claim: dict[str, Any]) -> tuple[str, str]:
    text = " ".join([claim["normalized_text"], str(claim["scope"].get("paradigm") or ""), str(claim["scope"].get("measurement_level") or "")]).casefold()
    for rule in DOMAIN_PROFILE["lane_rules"]:
        if (claim["claim_type"] in rule.get("claim_types", [])
            or claim["claim_status"] in rule.get("claim_statuses", [])
            or any(cue.casefold() in text for cue in rule.get("keywords", []))):
            return rule["lane_id"], rule.get("reason", "领域配置的有序泳道规则")
    return DOMAIN_PROFILE["default_lane_id"], "领域配置默认泳道"


def weighted_wrap(text: str, limit: int = 22, lines: int = 3) -> list[str]:
    result: list[str] = []
    current = ""
    weight = 0
    for char in text:
        char_weight = 1 if ord(char) < 128 else 2
        if current and weight + char_weight > limit * 2:
            result.append(current)
            current, weight = char, char_weight
            if len(result) == lines:
                break
        else:
            current += char
            weight += char_weight
    if len(result) < lines and current:
        result.append(current)
    consumed = sum(len(value) for value in result)
    if consumed < len(text) and result:
        result[-1] = result[-1].rstrip("，。；：,. ") + "…"
    return result


def weak_components(node_ids: Iterable[str], edges: list[dict[str, Any]]) -> list[list[str]]:
    adjacent: dict[str, set[str]] = {node_id: set() for node_id in node_ids}
    for edge in edges:
        a, b = edge["history_from_claim_id"], edge["history_to_claim_id"]
        adjacent[a].add(b)
        adjacent[b].add(a)
    components: list[list[str]] = []
    remaining = set(adjacent)
    while remaining:
        start = min(remaining, key=lambda value: int(value[1:]))
        stack, component = [start], []
        remaining.remove(start)
        while stack:
            node = stack.pop()
            component.append(node)
            for neighbor in sorted(adjacent[node], reverse=True):
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    stack.append(neighbor)
        components.append(sorted(component, key=lambda value: int(value[1:])))
    return sorted(components, key=lambda values: (-len(values), int(values[0][1:])))


def longest_temporal_path(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> dict[str, Any]:
    if not nodes:
        return {"edge_count": 0, "claim_ids": [], "years": []}
    year = {node["claim_id"]: node["year"] for node in nodes}
    outgoing: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        old, new = edge["history_from_claim_id"], edge["history_to_claim_id"]
        if year[old] < year[new]:
            outgoing[old].append(new)
    ordered = sorted(year, key=lambda node_id: (year[node_id], int(node_id[1:])))
    distance = {node_id: 0 for node_id in ordered}
    predecessor: dict[str, str] = {}
    for node_id in ordered:
        for neighbor in sorted(outgoing[node_id], key=lambda value: (year[value], int(value[1:]))):
            proposal = distance[node_id] + 1
            if proposal > distance[neighbor]:
                distance[neighbor] = proposal
                predecessor[neighbor] = node_id
    end = max(ordered, key=lambda node_id: (distance[node_id], year[node_id], -int(node_id[1:])))
    path = [end]
    while path[-1] in predecessor:
        path.append(predecessor[path[-1]])
    path.reverse()
    return {"edge_count": distance[end], "claim_ids": path, "years": [year[node_id] for node_id in path]}


def temporal_hubs(nodes: list[dict[str, Any]], edges: list[dict[str, Any]], limit: int = 20) -> list[dict[str, Any]]:
    year = {node["claim_id"]: node["year"] for node in nodes}
    outgoing: dict[str, set[str]] = defaultdict(set)
    incoming: Counter[str] = Counter()
    for edge in edges:
        old, new = edge["history_from_claim_id"], edge["history_to_claim_id"]
        if year[old] < year[new]:
            outgoing[old].add(new)
            incoming[new] += 1
    roots = [node_id for node_id in outgoing if incoming[node_id] == 0]
    rows = []
    for root in roots:
        queue = deque([(root, 0)])
        seen = {root}
        max_depth = 0
        while queue:
            node_id, depth = queue.popleft()
            max_depth = max(max_depth, depth)
            for child in outgoing.get(node_id, set()):
                if child not in seen:
                    seen.add(child)
                    queue.append((child, depth + 1))
        rows.append({
            "root_claim_id": root,
            "year": year[root],
            "direct_children": len(outgoing[root]),
            "direct_child_claim_ids": sorted(outgoing[root], key=lambda value: (year[value], int(value[1:]))),
            "descendant_claims": len(seen) - 1,
            "descendant_claim_ids": sorted(seen - {root}, key=lambda value: (year[value], int(value[1:]))),
            "maximum_temporal_depth": max_depth,
        })
    return sorted(rows, key=lambda row: (-row["descendant_claims"], -row["maximum_temporal_depth"], row["year"], row["root_claim_id"]))[:limit]


def strongly_connected_components(node_ids: Iterable[str], edges: list[dict[str, Any]]) -> list[list[str]]:
    adjacent: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        adjacent[edge["history_from_claim_id"]].append(edge["history_to_claim_id"])
    index = 0
    indices: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    result: list[list[str]] = []

    def visit(node: str) -> None:
        nonlocal index
        indices[node] = low[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for neighbor in adjacent.get(node, []):
            if neighbor not in indices:
                visit(neighbor)
                low[node] = min(low[node], low[neighbor])
            elif neighbor in on_stack:
                low[node] = min(low[node], indices[neighbor])
        if low[node] == indices[node]:
            component = []
            while True:
                member = stack.pop()
                on_stack.remove(member)
                component.append(member)
                if member == node:
                    break
            result.append(sorted(component, key=lambda value: int(value[1:])))

    for node_id in sorted(node_ids, key=lambda value: int(value[1:])):
        if node_id not in indices:
            visit(node_id)
    return [component for component in result if len(component) > 1]


def build_layout(nodes: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    min_year = min(node["year"] for node in nodes)
    max_year = max(node["year"] for node in nodes)
    by_lane_year: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for node in nodes:
        by_lane_year[(node["lane_id"], node["year"])].append(node)
    for values in by_lane_year.values():
        values.sort(key=lambda row: (-row["retained_degree"], int(row["claim_id"][1:])))

    lane_heights: dict[str, int] = {}
    for lane_id, _ in LANES:
        maximum = max((len(by_lane_year[(lane_id, year)]) for year in range(min_year, max_year + 1)), default=0)
        lane_heights[lane_id] = max(MIN_LANE_HEIGHT, LANE_HEADER + maximum * ROW_STEP + 34)

    lane_tops: dict[str, int] = {}
    cursor = TOP_MARGIN
    for lane_id, _ in LANES:
        lane_tops[lane_id] = cursor
        cursor += lane_heights[lane_id]

    positioned: list[dict[str, Any]] = []
    for lane_id, _ in LANES:
        maximum = max((len(by_lane_year[(lane_id, year)]) for year in range(min_year, max_year + 1)), default=0)
        for year in range(min_year, max_year + 1):
            values = by_lane_year[(lane_id, year)]
            vertical_offset = max(0, (maximum - len(values)) * ROW_STEP // 2)
            for row_number, node in enumerate(values):
                positioned.append({
                    **node,
                    "x": LEFT_MARGIN + (year - min_year) * X_STEP - NODE_WIDTH // 2,
                    "y": lane_tops[lane_id] + LANE_HEADER + vertical_offset + row_number * ROW_STEP,
                    "width": NODE_WIDTH,
                    "height": NODE_HEIGHT,
                })
    positioned.sort(key=lambda row: int(row["claim_id"][1:]))
    layout = {
        "min_year": min_year,
        "max_year": max_year,
        "width": LEFT_MARGIN + (max_year - min_year) * X_STEP + NODE_WIDTH // 2 + RIGHT_MARGIN,
        "height": cursor + BOTTOM_MARGIN,
        "lane_tops": lane_tops,
        "lane_heights": lane_heights,
        "node_width": NODE_WIDTH,
        "node_height": NODE_HEIGHT,
        "x_step_per_year": X_STEP,
    }
    return positioned, layout


def render_svg(
    path: Path,
    title: str,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    subtitle: str,
) -> dict[str, int]:
    if not nodes:
        write_text(path, f'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="260" viewBox="0 0 1200 260"><title>{escape(title)}</title><rect width="1200" height="260" fill="#ffffff"/><text x="40" y="80" font-family="Microsoft YaHei,Arial" font-size="24" fill="#172033">{escape(title)}</text><text x="40" y="140" font-family="Microsoft YaHei,Arial" font-size="18" fill="#475467">当前没有正式关系及其端点；完整图仍保留全部 Claim。</text></svg>')
        return {"width": 1200, "height": 260}
    positioned, layout = build_layout(nodes)
    position = {node["claim_id"]: node for node in positioned}
    width, height = layout["width"], layout["height"]
    min_year, max_year = layout["min_year"], layout["max_year"]
    relation_counts = Counter(edge["relation_type"] for edge in edges)
    status_counts = Counter(edge["annotation_status"] for edge in edges)
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">',
        f'<title id="title">{escape(title)}</title>',
        f'<desc id="desc">{escape(subtitle)}。横轴为年份，纵轴为主题泳道；跨年箭头从较早 Claim 指向较新 Claim；同年边不表示发表先后。</desc>',
        '<style><![CDATA[',
        'text{font-family:"Microsoft YaHei","Noto Sans CJK SC",Arial,sans-serif;fill:#172033}',
        '.year{font-size:18px;font-weight:600;fill:#344054}.lane-label{font-size:21px;font-weight:700}',
        '.lane-count{font-size:14px;fill:#667085}.node-id{font-size:14px;font-weight:700}.node-text{font-size:13px}',
        '.edge{fill:none;pointer-events:stroke}.edge:hover{opacity:1!important;stroke-width:5!important}',
        '.node:hover rect.body{stroke-width:3}.legend{font-size:14px}.small{font-size:13px;fill:#667085}',
        ']]></style>',
        '<defs>',
    ]
    for relation_type, color in RELATION_COLORS.items():
        lines.append(
            f'<marker id="arrow-{relation_type}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="{color}"/></marker>'
        )
    lines.extend([
        '</defs>',
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="#ffffff"/>',
        f'<text x="40" y="48" font-size="30" font-weight="700">{escape(title)}</text>',
        f'<text x="40" y="78" class="small">{escape(subtitle)}</text>',
        f'<text x="40" y="103" class="small">Claim {len(nodes)} · 关系 {len(edges)} · 正式 {status_counts.get("accepted", 0)} · 待复核 {status_counts.get("candidate", 0)}</text>',
    ])

    legend_x, legend_y = 40, 135
    for index, relation_type in enumerate(RELATION_COLORS):
        x = legend_x + (index % 3) * 245
        y = legend_y + (index // 3) * 27
        color = RELATION_COLORS[relation_type]
        lines.append(f'<line x1="{x}" y1="{y}" x2="{x + 38}" y2="{y}" stroke="{color}" stroke-width="3" marker-end="url(#arrow-{relation_type})"/>')
        lines.append(f'<text x="{x + 49}" y="{y + 5}" class="legend">{relation_type} ({relation_counts.get(relation_type, 0)})</text>')
    lines.append('<line x1="790" y1="135" x2="836" y2="135" stroke="#475467" stroke-width="3"/><text x="847" y="140" class="legend">正式关系</text>')
    lines.append('<line x1="790" y1="162" x2="836" y2="162" stroke="#475467" stroke-width="2" stroke-dasharray="9 7" opacity="0.38"/><text x="847" y="167" class="legend">待复核关系</text>')

    lane_counts = Counter(node["lane_id"] for node in positioned)
    for lane_index, (lane_id, lane_name) in enumerate(LANES):
        top = layout["lane_tops"][lane_id]
        lane_height = layout["lane_heights"][lane_id]
        lines.append(f'<rect x="18" y="{top}" width="{width - 36}" height="{lane_height}" fill="{LANE_FILLS[lane_index % len(LANE_FILLS)]}"/>')
        lines.append(f'<text x="36" y="{top + 39}" class="lane-label">{escape(lane_name)}</text>')
        lines.append(f'<text x="36" y="{top + 62}" class="lane-count">{lane_counts[lane_id]} Claims</text>')

    for year in range(min_year, max_year + 1):
        x = LEFT_MARGIN + (year - min_year) * X_STEP
        lines.append(f'<line x1="{x}" y1="{TOP_MARGIN - 30}" x2="{x}" y2="{height - BOTTOM_MARGIN + 15}" stroke="#d7dce5" stroke-width="1"/>')
        lines.append(f'<text x="{x}" y="{TOP_MARGIN - 48}" class="year" text-anchor="middle">{year}</text>')

    ordered_edges = sorted(edges, key=lambda row: (row["annotation_status"] == "accepted", row["relation_id"]))
    for index, edge in enumerate(ordered_edges):
        old = position[edge["history_from_claim_id"]]
        new = position[edge["history_to_claim_id"]]
        color = RELATION_COLORS[edge["relation_type"]]
        old_cy, new_cy = old["y"] + NODE_HEIGHT / 2, new["y"] + NODE_HEIGHT / 2
        if old["year"] < new["year"]:
            x1, x2 = old["x"] + NODE_WIDTH, new["x"]
            span = x2 - x1
            c1, c2 = x1 + span * 0.36, x2 - span * 0.36
            path_data = f'M {x1:.1f} {old_cy:.1f} C {c1:.1f} {old_cy:.1f}, {c2:.1f} {new_cy:.1f}, {x2:.1f} {new_cy:.1f}'
        else:
            right = max(old["x"], new["x"]) + NODE_WIDTH + 24 + (index % 4) * 9
            x1, x2 = old["x"] + NODE_WIDTH, new["x"] + NODE_WIDTH
            path_data = f'M {x1:.1f} {old_cy:.1f} C {right:.1f} {old_cy:.1f}, {right:.1f} {new_cy:.1f}, {x2:.1f} {new_cy:.1f}'
        candidate = edge["annotation_status"] == "candidate"
        dash = ' stroke-dasharray="10 8"' if candidate else ""
        opacity = "0.30" if candidate else "0.72"
        stroke_width = "1.8" if candidate else "2.7"
        tooltip = (
            f'{edge["history_from_claim_id"]} → {edge["history_to_claim_id"]}（历史显示） | '
            f'{edge["relation_type"]} | {edge["annotation_status"]} | confidence={edge["confidence"]} | {edge["reason"]}'
        )
        lines.append(
            f'<path class="edge" data-relation-id="{edge["relation_id"]}" data-relation-type="{edge["relation_type"]}" '
            f'data-status="{edge["annotation_status"]}" data-history-from="{edge["history_from_claim_id"]}" '
            f'data-history-to="{edge["history_to_claim_id"]}" d="{path_data}" stroke="{color}" stroke-width="{stroke_width}" '
            f'opacity="{opacity}"{dash} marker-end="url(#arrow-{edge["relation_type"]})"><title>{escape(tooltip)}</title></path>'
        )

    for node in positioned:
        x, y = node["x"], node["y"]
        type_color = TYPE_COLORS[node["claim_type"]]
        border = "#bd3545" if node["claim_status"] in {"absence", "no_evidence"} else "#98a2b3"
        tooltip = f'{node["claim_id"]} | {node["paper_id"]} | {node["year"]} | {node["paper_title"]} | {node["normalized_text"]}'
        lines.append(f'<g class="node" data-claim-id="{node["claim_id"]}" data-paper-id="{node["paper_id"]}" data-year="{node["year"]}" data-lane="{node["lane_id"]}"><title>{escape(tooltip)}</title>')
        lines.append(f'<rect class="body" x="{x}" y="{y}" width="{NODE_WIDTH}" height="{NODE_HEIGHT}" rx="9" fill="#ffffff" stroke="{border}" stroke-width="1.4"/>')
        lines.append(f'<rect x="{x}" y="{y}" width="7" height="{NODE_HEIGHT}" rx="3.5" fill="{type_color}"/>')
        lines.append(f'<text x="{x + 16}" y="{y + 20}" class="node-id">{node["claim_id"]} · {node["paper_id"]} · {node["claim_type"]}</text>')
        for line_number, text in enumerate(weighted_wrap(node["normalized_text"], 23, 3)):
            lines.append(f'<text x="{x + 16}" y="{y + 42 + line_number * 17}" class="node-text">{escape(text)}</text>')
        lines.append('</g>')

    footer_y = height - 70
    lines.append(f'<text x="40" y="{footer_y}" class="small">阅读方向：SVG 跨年箭头由旧 Claim 指向新 Claim，同年/同篇边不表示时间先后；JSON 数据层仍保存“新 Claim 对旧 Claim 的知识作用”。泳道仅用于呈现，不是本体分类。</text>')
    lines.append(f'<text x="40" y="{footer_y + 24}" class="small">节点色条：蓝=实证，紫=解释，绿=理论/综合，橙=方法；红色边框表示零结果或无证据。悬停节点或边可查看完整内容。</text>')
    lines.append('</svg>')
    write_text(path, "\n".join(lines))
    return {"width": width, "height": height}


def run(workspace: Path) -> None:
    global LANES, LANE_NAMES, DOMAIN_PROFILE
    DOMAIN_PROFILE = load_domain_profile(workspace)
    LANES = [(lane["lane_id"], lane["lane_name"]) for lane in DOMAIN_PROFILE["lanes"]]
    LANE_NAMES = dict(LANES)
    papers = [clean(row) for row in read_jsonl(workspace / "01_规范化输入" / "papers.jsonl")]
    claims = [clean(row) for row in read_jsonl(workspace / "04_Claim复核" / "claims.jsonl")]
    relations = [clean(row) for row in read_jsonl(workspace / "06_关系判断" / "relations.jsonl")]
    paper_by_id = {row["paper_id"]: row for row in papers}

    output = workspace / "07_图谱装配"
    output.mkdir(parents=True, exist_ok=True)
    override_path = output / "lane_overrides.jsonl"
    if not override_path.exists():
        write_text(override_path, "")
    overrides = [clean(row) for row in read_jsonl(override_path)]
    override_by_claim = {row["claim_id"]: row for row in overrides}
    if len(override_by_claim) != len(overrides) or not set(override_by_claim) <= {row["claim_id"] for row in claims}:
        raise ValueError("Lane overrides must be unique current Claim IDs")

    retained_degree: Counter[str] = Counter()
    accepted_degree: Counter[str] = Counter()
    for relation in relations:
        for claim_id in (relation["source_claim_id"], relation["target_claim_id"]):
            retained_degree[claim_id] += 1
            if relation["annotation_status"] == "accepted":
                accepted_degree[claim_id] += 1

    nodes = []
    for claim in claims:
        lane_id, lane_reason = classify_lane(claim)
        if claim["claim_id"] in override_by_claim:
            lane_id = override_by_claim[claim["claim_id"]]["lane_id"]
            lane_reason = override_by_claim[claim["claim_id"]].get("reason", "人工泳道覆盖")
        if lane_id not in LANE_NAMES:
            raise ValueError(f"Unknown lane for {claim['claim_id']}: {lane_id}")
        paper = paper_by_id[claim["paper_id"]]
        nodes.append({
            "schema_version": "graph_node.schema1",
            "claim_id": claim["claim_id"],
            "paper_id": claim["paper_id"],
            "year": int(paper["year"]),
            "paper_title": paper["title"],
            "normalized_text": claim["normalized_text"],
            "claim_type": claim["claim_type"],
            "claim_status": claim["claim_status"],
            "scope": claim["scope"],
            "lane_id": lane_id,
            "lane_name": LANE_NAMES[lane_id],
            "lane_assignment_reason": lane_reason,
            "retained_degree": retained_degree[claim["claim_id"]],
            "accepted_degree": accepted_degree[claim["claim_id"]],
        })

    edges = []
    for relation in relations:
        edges.append({
            "schema_version": "graph_edge.schema1",
            "relation_id": relation["relation_id"],
            "semantic_source_claim_id": relation["source_claim_id"],
            "semantic_target_claim_id": relation["target_claim_id"],
            "history_from_claim_id": relation["target_claim_id"],
            "history_to_claim_id": relation["source_claim_id"],
            "relation_type": relation["relation_type"],
            "directness": relation["directness"],
            "scope_overlap": relation["scope_overlap"],
            "confidence": relation["confidence"],
            "annotation_status": relation["annotation_status"],
            "reason": relation["reason"],
            "candidate_id": relation["candidate_id"],
        })

    positioned_nodes, layout = build_layout(nodes)
    position_by_id = {node["claim_id"]: node for node in positioned_nodes}
    node_rows = [{**node, "x": position_by_id[node["claim_id"]]["x"], "y": position_by_id[node["claim_id"]]["y"], "width": NODE_WIDTH, "height": NODE_HEIGHT} for node in nodes]
    write_jsonl(output / "graph_nodes.jsonl", node_rows)
    write_jsonl(output / "graph_edges.jsonl", edges)
    write_json(output / "layout.json", {"lanes": [{"lane_id": lane_id, "lane_name": name} for lane_id, name in LANES], **layout})
    write_json(output / "graph.json", {"schema_version": "claim_evolution_graph.schema1", "nodes": node_rows, "edges": edges})

    claim_ids = [node["claim_id"] for node in nodes]
    node_by_year = {node["claim_id"]: node["year"] for node in nodes}
    retained_components = weak_components(claim_ids, edges)
    component_rows = []
    for index, component in enumerate(retained_components, start=1):
        component_edges = [
            edge for edge in edges
            if edge["history_from_claim_id"] in component and edge["history_to_claim_id"] in component
        ]
        component_rows.append({
            "component_id": f"COMP{index:02d}",
            "size": len(component),
            "claim_ids": component,
            "year_min": min(node_by_year[claim_id] for claim_id in component),
            "year_max": max(node_by_year[claim_id] for claim_id in component),
            "relation_count": len(component_edges),
            "accepted_relations": sum(edge["annotation_status"] == "accepted" for edge in component_edges),
            "provisional_relations": sum(edge["annotation_status"] == "candidate" for edge in component_edges),
        })
    write_json(output / "components.json", {"components": component_rows})
    accepted_edges = [edge for edge in edges if edge["annotation_status"] == "accepted"]
    accepted_node_ids = {endpoint for edge in accepted_edges for endpoint in (edge["history_from_claim_id"], edge["history_to_claim_id"])}
    accepted_nodes = [node for node in nodes if node["claim_id"] in accepted_node_ids]
    incoming: Counter[str] = Counter(edge["history_to_claim_id"] for edge in edges)
    outgoing: Counter[str] = Counter(edge["history_from_claim_id"] for edge in edges)
    node_by_id = {node["claim_id"]: node for node in nodes}
    analysis = {
        "analysis_version": "evolution_analysis.schema1",
        "display_direction": "older_claim_to_newer_claim",
        "semantic_storage_direction": "newer_claim_to_older_claim",
        "retained_graph": {
            "nodes": len(nodes),
            "edges": len(edges),
            "weak_components": len(retained_components),
            "component_sizes": [len(component) for component in retained_components],
            "isolated_claim_ids": [component[0] for component in retained_components if len(component) == 1],
            "cross_lane_edges": sum(node_by_id[edge["history_from_claim_id"]]["lane_id"] != node_by_id[edge["history_to_claim_id"]]["lane_id"] for edge in edges),
            "same_year_edges": sum(node_by_id[edge["history_from_claim_id"]]["year"] == node_by_id[edge["history_to_claim_id"]]["year"] for edge in edges),
            "multi_parent_claims": sorted([claim_id for claim_id, count in incoming.items() if count >= 2], key=lambda value: (-incoming[value], int(value[1:]))),
            "branch_claims": sorted([claim_id for claim_id, count in outgoing.items() if count >= 2], key=lambda value: (-outgoing[value], int(value[1:]))),
            "directed_cycles": strongly_connected_components(claim_ids, edges),
            "longest_temporal_path": longest_temporal_path(nodes, edges),
            "largest_origin_histories": temporal_hubs(nodes, edges),
        },
        "accepted_graph": {
            "nodes_with_accepted_edges": len(accepted_nodes),
            "edges": len(accepted_edges),
            "weak_components": len(weak_components(accepted_node_ids, accepted_edges)),
            "longest_temporal_path": longest_temporal_path(accepted_nodes, accepted_edges),
        },
    }
    write_json(output / "evolution_analysis.json", analysis)

    full_size = render_svg(
        output / "完整Claim时间泳道式演化网络.svg",
        "Claim 时间泳道式演化网络（完整关系层）",
        nodes,
        edges,
        "包含全部 Claim；正式关系为实线，低置信待复核关系为虚线",
    )
    core_size = render_svg(
        output / "正式关系骨架.svg",
        "Claim 时间泳道式演化网络（正式关系骨架）",
        accepted_nodes,
        accepted_edges,
        "仅显示已接受关系及其 Claim，用于观察高置信演化骨架",
    )

    lane_counts = Counter(node["lane_id"] for node in nodes)
    type_counts = Counter(edge["relation_type"] for edge in edges)
    report = {
        "report_version": "graph_assembly_report.schema1",
        "domain_profile_sha256": file_sha256(workspace / "领域配置.json"),
        "lane_override_sha256": file_sha256(override_path),
        "status": "passed",
        "claim_nodes": len(nodes),
        "paper_nodes": 0,
        "citation_edges": 0,
        "retained_relations": len(edges),
        "accepted_relations": len(accepted_edges),
        "provisional_relations": len(edges) - len(accepted_edges),
        "lane_distribution": {lane_id: lane_counts[lane_id] for lane_id, _ in LANES},
        "relation_type_distribution": dict(sorted(type_counts.items())),
        "weak_components": len(retained_components),
        "largest_component_claims": len(retained_components[0]),
        "isolated_claim_ids": analysis["retained_graph"]["isolated_claim_ids"],
        "multi_parent_claims": len(analysis["retained_graph"]["multi_parent_claims"]),
        "branch_claims": len(analysis["retained_graph"]["branch_claims"]),
        "longest_temporal_path_edges": analysis["retained_graph"]["longest_temporal_path"]["edge_count"],
        "lane_override_rows": len(overrides),
        "outputs": {
            "layout": {"file": "layout.json", "sha256": file_sha256(output / "layout.json")},
            "nodes": {"file": "graph_nodes.jsonl", "rows": len(nodes), "sha256": file_sha256(output / "graph_nodes.jsonl")},
            "edges": {"file": "graph_edges.jsonl", "rows": len(edges), "sha256": file_sha256(output / "graph_edges.jsonl")},
            "graph": {"file": "graph.json", "sha256": file_sha256(output / "graph.json")},
            "analysis": {"file": "evolution_analysis.json", "sha256": file_sha256(output / "evolution_analysis.json")},
            "components": {"file": "components.json", "rows": len(component_rows), "sha256": file_sha256(output / "components.json")},
            "full_svg": {"file": "完整Claim时间泳道式演化网络.svg", "sha256": file_sha256(output / "完整Claim时间泳道式演化网络.svg"), **full_size},
            "accepted_svg": {"file": "正式关系骨架.svg", "sha256": file_sha256(output / "正式关系骨架.svg"), **core_size},
        },
    }
    write_json(output / "graph_assembly_report.json", report)

    longest = analysis["retained_graph"]["longest_temporal_path"]
    write_text(
        output / "第七轮检查报告.md",
        "\n".join([
            "# 第七轮：时间泳道式演化网络装配报告", "",
            "状态：**passed**。", "",
            f"- Claim 节点：{len(nodes)}；Paper 节点：0；论文引用边：0。",
            f"- 关系：{len(edges)}（正式 {len(accepted_edges)}；待复核 {len(edges) - len(accepted_edges)}）。",
            f"- 泳道分布：{report['lane_distribution']}。",
            f"- 弱连通分量：{len(retained_components)}；最大分量：{len(retained_components[0])} 个 Claim；孤立：{', '.join(report['isolated_claim_ids']) or '无'}。",
            f"- 多父汇聚节点：{report['multi_parent_claims']}；分支节点：{report['branch_claims']}。",
            f"- 最长跨年演化路径：{longest['edge_count']} 跳，{' → '.join(longest['claim_ids'])}（年份 {' → '.join(map(str, longest['years']))}）。", "",
            "SVG 箭头按历史阅读方向从旧 Claim 指向新 Claim；机器数据仍保存新 Claim 对旧 Claim 的语义作用方向。", "",
            "修改泳道：编辑 `lane_overrides.jsonl` 后重跑本轮。修改关系：编辑上一轮 `relation_overrides.jsonl`，重跑第六、七轮。",
        ]),
    )
    write_text(
        workspace / "STATUS.md",
        "# Claim 演化知识图谱 状态\n\n状态：`awaiting_validation`\n\n"
        f"已完成：{len(papers)} 篇论文、{len(nodes)} 个 Claim、{len(edges)} 条保留关系的完整七轮流程。\n\n"
        f"最终图：`07_图谱装配/完整Claim时间泳道式演化网络.svg`。\n\n"
        f"正式骨架：`07_图谱装配/正式关系骨架.svg`。\n\n"
        "最终检查入口：`07_图谱装配/第七轮检查报告.md`。\n",
    )
    print(f"Graph assembly complete: {len(nodes)} nodes, {len(edges)} edges, {len(retained_components)} components")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Round 7: assemble and render the Claim evolution graph.")
    parser.add_argument("--workspace", type=Path, default=DEFAULT_WORKSPACE)
    args = parser.parse_args()
    run(args.workspace.resolve())
