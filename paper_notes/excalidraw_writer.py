from __future__ import annotations

import json
import math
import random
import time
from collections import deque
from pathlib import Path

BOX_WIDTH = 220
BASE_BOX_HEIGHT = 70
CHARS_PER_LINE = 18
LINE_HEIGHT_PX = 22
H_GAP = 120
V_GAP = 40
ARROW_GAP = 4

CATEGORY_COLORS = {
    "input": "#a5d8ff",       # 파랑 - 데이터/입력
    "process": "#b2f2bb",     # 초록 - 방법론/처리
    "result": "#ffd8a8",      # 주황 - 결과/성과
    "limitation": "#ffc9c9",  # 빨강 - 한계/향후과제
    "other": "#e9ecef",       # 회색 - 기타
}


def _seed() -> int:
    return random.randint(1, 2**31 - 1)


def _estimate_box_height(label: str) -> int:
    lines = max(1, math.ceil(len(label) / CHARS_PER_LINE))
    return max(BASE_BOX_HEIGHT, 30 + lines * LINE_HEIGHT_PX)


def _compute_levels(concepts: list[dict], relationships: list[dict]) -> dict[str, int]:
    """relationships를 DAG로 보고 각 노드의 단계(level)를 위상정렬로 계산한다.
    순환이 있으면 삽입 순서 기반으로 대체한다."""
    ids = [c["id"] for c in concepts]
    id_set = set(ids)

    adjacency: dict[str, list[str]] = {i: [] for i in ids}
    indegree: dict[str, int] = {i: 0 for i in ids}
    for rel in relationships:
        src, dst = rel.get("from_id"), rel.get("to_id")
        if src in id_set and dst in id_set and src != dst:
            adjacency[src].append(dst)
            indegree[dst] += 1

    level = {i: 0 for i in ids}
    queue = deque(i for i in ids if indegree[i] == 0)
    remaining_indegree = dict(indegree)
    visited = 0

    while queue:
        node = queue.popleft()
        visited += 1
        for nxt in adjacency[node]:
            level[nxt] = max(level[nxt], level[node] + 1)
            remaining_indegree[nxt] -= 1
            if remaining_indegree[nxt] == 0:
                queue.append(nxt)

    if visited < len(ids):
        # 순환 발생 - 삽입 순서를 3개씩 끊어서 단계로 대체
        for idx, node_id in enumerate(ids):
            level[node_id] = idx // 3

    return level


def _rect_edge_point(rx: float, ry: float, hw: float, hh: float, tx: float, ty: float) -> tuple[float, float]:
    """중심이 (rx,ry)이고 반폭/반높이가 hw/hh인 사각형의 경계에서,
    (tx,ty) 방향으로 향하는 직선이 만나는 점을 계산한다."""
    dx, dy = tx - rx, ty - ry
    if dx == 0 and dy == 0:
        return rx, ry
    scale_x = hw / abs(dx) if dx != 0 else math.inf
    scale_y = hh / abs(dy) if dy != 0 else math.inf
    scale = min(scale_x, scale_y)
    return rx + dx * scale, ry + dy * scale


def _rectangle(el_id: str, x: float, y: float, width: float, height: float, bg_color: str) -> dict:
    return {
        "id": el_id,
        "type": "rectangle",
        "x": x,
        "y": y,
        "width": width,
        "height": height,
        "angle": 0,
        "strokeColor": "#1e1e1e",
        "backgroundColor": bg_color,
        "fillStyle": "solid",
        "strokeWidth": 2,
        "roughness": 1,
        "opacity": 100,
        "groupIds": [],
        "roundness": {"type": 3},
        "seed": _seed(),
        "version": 1,
        "versionNonce": _seed(),
        "isDeleted": False,
        "boundElements": [{"id": f"{el_id}-text", "type": "text"}],
        "updated": int(time.time() * 1000),
        "link": None,
        "locked": False,
    }


def _text(el_id: str, container_id: str, label: str, x: float, y: float, width: float, height: float) -> dict:
    return {
        "id": f"{el_id}-text",
        "type": "text",
        "x": x + 10,
        "y": y + height / 2 - 12,
        "width": width - 20,
        "height": height - 20,
        "angle": 0,
        "strokeColor": "#1e1e1e",
        "backgroundColor": "transparent",
        "fillStyle": "solid",
        "strokeWidth": 2,
        "roughness": 1,
        "opacity": 100,
        "groupIds": [],
        "seed": _seed(),
        "version": 1,
        "versionNonce": _seed(),
        "isDeleted": False,
        "boundElements": None,
        "updated": int(time.time() * 1000),
        "link": None,
        "locked": False,
        "text": label,
        "fontSize": 16,
        "fontFamily": 1,
        "textAlign": "center",
        "verticalAlign": "middle",
        "containerId": container_id,
        "originalText": label,
        "lineHeight": 1.25,
    }


def _arrow(el_id: str, start: tuple[float, float], end: tuple[float, float],
           label: str, start_id: str, end_id: str) -> list[dict]:
    x0, y0 = start
    x1, y1 = end
    arrow = {
        "id": el_id,
        "type": "arrow",
        "x": x0,
        "y": y0,
        "width": x1 - x0,
        "height": y1 - y0,
        "angle": 0,
        "strokeColor": "#495057",
        "backgroundColor": "transparent",
        "fillStyle": "solid",
        "strokeWidth": 2,
        "roughness": 1,
        "opacity": 100,
        "groupIds": [],
        "seed": _seed(),
        "version": 1,
        "versionNonce": _seed(),
        "isDeleted": False,
        "boundElements": [{"id": f"{el_id}-text", "type": "text"}] if label else None,
        "updated": int(time.time() * 1000),
        "link": None,
        "locked": False,
        "points": [[0, 0], [x1 - x0, y1 - y0]],
        "lastCommittedPoint": None,
        "startBinding": {"elementId": start_id, "focus": 0, "gap": ARROW_GAP},
        "endBinding": {"elementId": end_id, "focus": 0, "gap": ARROW_GAP},
        "startArrowhead": None,
        "endArrowhead": "arrow",
    }
    elements = [arrow]
    if label:
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        elements.append(
            {
                "id": f"{el_id}-text",
                "type": "text",
                "x": mx - 30,
                "y": my - 10,
                "width": 60,
                "height": 20,
                "angle": 0,
                "strokeColor": "#495057",
                "backgroundColor": "#ffffff",
                "fillStyle": "solid",
                "strokeWidth": 2,
                "roughness": 1,
                "opacity": 100,
                "groupIds": [],
                "seed": _seed(),
                "version": 1,
                "versionNonce": _seed(),
                "isDeleted": False,
                "boundElements": None,
                "updated": int(time.time() * 1000),
                "link": None,
                "locked": False,
                "text": label,
                "fontSize": 12,
                "fontFamily": 1,
                "textAlign": "center",
                "verticalAlign": "middle",
                "containerId": el_id,
                "originalText": label,
                "lineHeight": 1.25,
            }
        )
    return elements


def build_diagram(concepts: list[dict], relationships: list[dict]) -> dict:
    if not concepts:
        return {
            "type": "excalidraw",
            "version": 2,
            "source": "https://excalidraw.com",
            "elements": [],
            "appState": {"gridSize": None, "viewBackgroundColor": "#ffffff"},
            "files": {},
        }

    levels = _compute_levels(concepts, relationships)

    # 레벨(단계)별로 노드를 묶고, 각 레벨 안에서는 세로로 쌓는다 (왼쪽→오른쪽 파이프라인 흐름)
    columns: dict[int, list[dict]] = {}
    for concept in concepts:
        columns.setdefault(levels[concept["id"]], []).append(concept)

    elements: list[dict] = []
    rect_elements: dict[str, dict] = {}
    boxes: dict[str, tuple[float, float, float, float]] = {}  # id -> (x, y, w, h)

    for col_index in sorted(columns.keys()):
        x = col_index * (BOX_WIDTH + H_GAP)
        y_cursor = 0.0
        for concept in columns[col_index]:
            height = _estimate_box_height(concept["label"])
            bg_color = CATEGORY_COLORS.get(concept.get("category", "other"), CATEGORY_COLORS["other"])

            rect = _rectangle(concept["id"], x, y_cursor, BOX_WIDTH, height, bg_color)
            rect_elements[concept["id"]] = rect
            elements.append(rect)
            elements.append(_text(concept["id"], concept["id"], concept["label"], x, y_cursor, BOX_WIDTH, height))

            boxes[concept["id"]] = (x, y_cursor, BOX_WIDTH, height)
            y_cursor += height + V_GAP

    for i, rel in enumerate(relationships):
        start_box = boxes.get(rel.get("from_id"))
        end_box = boxes.get(rel.get("to_id"))
        if not start_box or not end_box:
            continue

        sx, sy, sw, sh = start_box
        ex, ey, ew, eh = end_box
        start_center = (sx + sw / 2, sy + sh / 2)
        end_center = (ex + ew / 2, ey + eh / 2)

        start_point = _rect_edge_point(*start_center, sw / 2, sh / 2, *end_center)
        end_point = _rect_edge_point(*end_center, ew / 2, eh / 2, *start_center)

        arrow_id = f"arrow-{i}"
        arrow_elements = _arrow(
            arrow_id, start_point, end_point, rel.get("label", ""),
            rel["from_id"], rel["to_id"],
        )
        elements.extend(arrow_elements)

        rect_elements[rel["from_id"]]["boundElements"].append({"id": arrow_id, "type": "arrow"})
        rect_elements[rel["to_id"]]["boundElements"].append({"id": arrow_id, "type": "arrow"})

    return {
        "type": "excalidraw",
        "version": 2,
        "source": "https://excalidraw.com",
        "elements": elements,
        "appState": {"gridSize": None, "viewBackgroundColor": "#ffffff"},
        "files": {},
    }


def write_diagram(vault_path: str, title_slug: str, summary: dict) -> str:
    """개념도를 vault의 논문별 폴더에 .excalidraw 파일로 저장하고 파일명을 반환한다."""
    folder = Path(vault_path) / "AutoNote" / title_slug
    folder.mkdir(parents=True, exist_ok=True)

    diagram = build_diagram(summary.get("concepts", []), summary.get("relationships", []))
    filename = f"{title_slug}.excalidraw"
    path = folder / filename
    path.write_text(json.dumps(diagram, ensure_ascii=False, indent=2), encoding="utf-8")
    return filename
