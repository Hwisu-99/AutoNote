from __future__ import annotations

import re
from pathlib import Path

import yaml

from paper_notes.dedup import dedupe_labels, normalize_label

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
    노드/에지를 만든다: 노트 = 주황 노드, concept = 회색 노드(frontmatter의
    concepts), entity = 회색 노드(frontmatter의 entities, concept이 있으면
    concept에 연결되고 없으면 note에 직접 연결), 공통 tag = 초록 노드를
    매개로 한 에지. concepts/entities frontmatter가 없는 이전 노트는 본문의
    [[위키링크]]를 concept으로 취급하는 방식으로 하위 호환한다. 노드를 만들기
    전에 vault 전체의 concept/entity 라벨을 dedup.dedupe_labels()로 한 번 병합해,
    표기만 다른 같은 개념(대소문자/공백/유사 문구)이 별개 노드로 쪼개지지 않게 한다."""
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
        fm_concepts = frontmatter.get("concepts") or []
        fm_entities = frontmatter.get("entities") or []

        links: set[str] = set()
        for wikilink in _WIKILINK_RE.finditer(body):
            target = wikilink.group(1).strip()
            if target.lower().endswith(".excalidraw"):
                continue  # 개념도 임베드는 다른 논문 노트가 아니므로 그래프 에지에서 제외
            links.add(target)

        notes.append(
            {
                "slug": slug,
                "title": title,
                "tags": tags,
                "links": links,
                "concepts": fm_concepts,
                "entities": fm_entities,
            }
        )

    note_slugs = {n["slug"] for n in notes}
    nodes = [{"id": n["slug"], "label": n["title"], "type": "note"} for n in notes]
    edges: list[dict] = []
    seen_tag_nodes: set[str] = set()
    seen_concept_nodes: set[str] = set()
    seen_entity_nodes: set[str] = set()

    # 그래프가 커질수록 같은 개념이 논문마다 다른 표기("Self-Attention" vs
    # "self attention mechanism")로 등장해 별개 노드로 쪼개지기 쉽다. 노드/에지를
    # 만들기 전에 전체 vault를 훑어 concept/entity 라벨을 한 번에 중복 제거한다.
    all_concept_labels: set[str] = set()
    all_entity_labels: set[str] = set()
    for n in notes:
        claimed = set(n["concepts"]) | {e["label"] for e in n["entities"]}
        all_concept_labels.update(n["concepts"])
        for entity in n["entities"]:
            all_entity_labels.add(entity["label"])
            concept_label = entity.get("concept")
            if concept_label:
                all_concept_labels.add(concept_label)
        for target in n["links"]:
            if target in note_slugs or target in claimed:
                continue
            all_concept_labels.add(target)

    concept_canon = dedupe_labels(all_concept_labels)
    entity_canon = dedupe_labels(all_entity_labels)

    for n in notes:
        claimed = set(n["concepts"]) | {e["label"] for e in n["entities"]}

        for concept_label in n["concepts"]:
            canonical = concept_canon.get(concept_label, concept_label)
            concept_id = f"concept:{normalize_label(canonical)}"
            if concept_id not in seen_concept_nodes:
                nodes.append({"id": concept_id, "label": canonical, "type": "concept"})
                seen_concept_nodes.add(concept_id)
            edges.append({"source": n["slug"], "target": concept_id, "type": "link"})

        for entity in n["entities"]:
            entity_canonical = entity_canon.get(entity["label"], entity["label"])
            entity_id = f"entity:{normalize_label(entity_canonical)}"
            if entity_id not in seen_entity_nodes:
                nodes.append({"id": entity_id, "label": entity_canonical, "type": "entity"})
                seen_entity_nodes.add(entity_id)

            concept_label = entity.get("concept")
            if concept_label:
                canonical = concept_canon.get(concept_label, concept_label)
                concept_id = f"concept:{normalize_label(canonical)}"
                if concept_id not in seen_concept_nodes:
                    nodes.append({"id": concept_id, "label": canonical, "type": "concept"})
                    seen_concept_nodes.add(concept_id)
                edges.append({"source": concept_id, "target": entity_id, "type": "link"})
            else:
                edges.append({"source": n["slug"], "target": entity_id, "type": "link"})

        for target in n["links"]:
            if target in note_slugs:
                edges.append({"source": n["slug"], "target": target, "type": "link"})
                continue
            if target in claimed:
                continue  # frontmatter의 concepts/entities로 이미 처리됨

            # 하위 호환: concepts/entities frontmatter가 없던 이전 노트를 위한 fallback
            canonical = concept_canon.get(target, target)
            concept_id = f"concept:{normalize_label(canonical)}"
            if concept_id not in seen_concept_nodes:
                nodes.append({"id": concept_id, "label": canonical, "type": "concept"})
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
