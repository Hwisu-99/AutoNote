from __future__ import annotations

import re
from pathlib import Path

import yaml

# Obsidian wikilink syntax: [[target]], [[target|alias]], embeds !\[\[target]]
_WIKILINK_RE = re.compile(r"!?\[\[([^\]|#]+)(?:\|[^\]]+)?\]\]")
_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def _parse_note(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    try:
        frontmatter = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        frontmatter = {}
    return frontmatter, text[match.end() :]


def build_graph(vault_path: str, focus_slug: str | None = None, only_focus: bool = False) -> dict:
    """AutoNote/ 폴더의 논문 노트들을 스캔해 Obsidian 그래프 뷰와 같은 방식으로
    노드/에지를 만든다: 노트 = 노드, [[위키링크]] = 에지, 공통 tag = 태그 노드를
    매개로 한 에지 (Obsidian 그래프 뷰의 '태그를 노드로 표시' 옵션과 동일)."""
    autonote_dir = Path(vault_path) / "AutoNote"
    if not autonote_dir.is_dir():
        return {"nodes": [], "edges": [], "focus": focus_slug}

    notes: list[dict] = []
    for folder in sorted(autonote_dir.iterdir()):
        if not folder.is_dir():
            continue
        slug = folder.name
        md_path = folder / f"{slug}.md"
        if not md_path.is_file():
            continue

        frontmatter, body = _parse_note(md_path)
        title = frontmatter.get("title") or slug
        tags = frontmatter.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]

        links: set[str] = set()
        for wikilink in _WIKILINK_RE.finditer(body):
            target = wikilink.group(1).strip()
            if target.lower().endswith(".excalidraw"):
                continue  # 개념도 임베드는 다른 논문 노트가 아니므로 그래프 에지에서 제외
            links.add(target)

        notes.append({"slug": slug, "title": title, "tags": tags, "links": links})

    note_slugs = {n["slug"] for n in notes}
    nodes = [{"id": n["slug"], "label": n["title"], "type": "note"} for n in notes]
    edges: list[dict] = []
    seen_tag_nodes: set[str] = set()
    seen_concept_nodes: set[str] = set()

    for n in notes:
        for target in n["links"]:
            if target in note_slugs:
                edges.append({"source": n["slug"], "target": target, "type": "link"})
                continue

            concept_id = f"concept:{target}"
            if concept_id not in seen_concept_nodes:
                nodes.append({"id": concept_id, "label": target, "type": "concept"})
                seen_concept_nodes.add(concept_id)
            edges.append({"source": n["slug"], "target": concept_id, "type": "link"})

        for tag in n["tags"]:
            tag_id = f"tag:{tag}"
            if tag_id not in seen_tag_nodes:
                nodes.append({"id": tag_id, "label": f"#{tag}", "type": "tag"})
                seen_tag_nodes.add(tag_id)
            edges.append({"source": n["slug"], "target": tag_id, "type": "tag"})

    if only_focus and focus_slug:
        keep_ids = {focus_slug}
        for e in edges:
            if e["source"] == focus_slug:
                keep_ids.add(e["target"])
            elif e["target"] == focus_slug:
                keep_ids.add(e["source"])
        nodes = [n for n in nodes if n["id"] in keep_ids]
        edges = [e for e in edges if e["source"] in keep_ids and e["target"] in keep_ids]

    return {"nodes": nodes, "edges": edges, "focus": focus_slug}
