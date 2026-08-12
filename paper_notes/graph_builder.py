from __future__ import annotations

import re
from pathlib import Path

import yaml

from paper_notes.dedup import dedupe_labels
from paper_notes.node_store import NODE_STORE_ROOT, find_node_fuzzy, list_nodes

# Obsidian wikilink syntax: [[target]], [[target|alias]], embeds !\[\[target]]
_WIKILINK_RE = re.compile(r"!?\[\[([^\]|#]+)(?:\|[^\]]+)?\]\]")
_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def _resolve_labels(
    raw_labels: set[str], aliases: dict[str, list[str]], store_nodes: list[dict]
) -> dict[str, tuple[str, str | None]]:
    """원본 라벨 -> (그래프에 표시할 라벨, node_store slug 또는 None) 매핑을 만든다.

    node_store에 이미 해당 개념의 물리적 노드 파일이 있으면, 그래프가 별도로
    "대표 라벨"을 계산하지 않고 그 파일의 display_label을 그대로 가져다 쓴다 -
    그래야 그래프에 보이는 라벨과 실제 클릭해서 들어가는 md 파일이 항상 정확히
    일치하고, 매번 그래프를 그릴 때마다 "혹시 둘이 같은 개념인가" 재확인할 필요가
    없다(node_store가 이미 한 번 판단해서 파일로 고정해둔 결과를 그대로 신뢰).
    node_store에 아직 없는 라벨들만 예전처럼 dedupe_labels()로 서로 묶어 대표
    라벨을 새로 고른다."""
    resolved: dict[str, tuple[str, str | None]] = {}
    unmatched: set[str] = set()
    for label in raw_labels:
        node = find_node_fuzzy(store_nodes, label, aliases.get(label))
        if node:
            resolved[label] = (node["display_label"], node["slug"])
        else:
            unmatched.add(label)

    fallback_canon = dedupe_labels(unmatched, aliases={k: v for k, v in aliases.items() if k in unmatched})
    for label in unmatched:
        resolved[label] = (fallback_canon[label], None)

    return resolved


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
    [[위키링크]]를 concept으로 취급하는 방식으로 하위 호환한다.

    concept/entity 노드의 라벨을 정할 때, node_store.py에 이미 물리적 노드
    파일이 있는 라벨은 그 파일의 display_label을 그대로 쓴다(_resolve_labels) -
    그래프 라벨과 클릭해서 들어가는 md 파일이 항상 정확히 일치하게 하기 위함.
    아직 노드 파일이 없는 라벨들만 dedup.dedupe_labels()로 서로 병합해 새
    대표 라벨을 고른다."""
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
        # concepts frontmatter는 예전 노트에서 문자열 리스트("Self-Attention")였다가
        # aliases 필드 도입 이후 {label, aliases} 객체 리스트로 바뀌었다. 두 형식을
        # 모두 {label, aliases} 형태로 정규화해 이후 로직을 단일 형식으로 다룬다.
        fm_concepts = [
            {"label": c, "aliases": []}
            if isinstance(c, str)
            else {"label": c["label"], "aliases": c.get("aliases") or []}
            for c in (frontmatter.get("concepts") or [])
        ]
        fm_entities = [
            {"label": e["label"], "concept": e.get("concept"), "aliases": e.get("aliases") or []}
            for e in (frontmatter.get("entities") or [])
        ]

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
    # aliases는 문자열 유사도로 못 잡는 경우(포함 관계, 동의어)를 모델의 문맥
    # 판단으로 보완하기 위해 dedupe_labels에 함께 전달한다.
    all_concept_labels: set[str] = set()
    all_entity_labels: set[str] = set()
    concept_aliases: dict[str, list[str]] = {}
    entity_aliases: dict[str, list[str]] = {}
    for n in notes:
        claimed = {c["label"] for c in n["concepts"]} | {e["label"] for e in n["entities"]}
        for c in n["concepts"]:
            all_concept_labels.add(c["label"])
            if c["aliases"]:
                concept_aliases.setdefault(c["label"], []).extend(c["aliases"])
        for entity in n["entities"]:
            all_entity_labels.add(entity["label"])
            if entity["aliases"]:
                entity_aliases.setdefault(entity["label"], []).extend(entity["aliases"])
            concept_label = entity.get("concept")
            if concept_label:
                all_concept_labels.add(concept_label)
        for target in n["links"]:
            if target in note_slugs or target in claimed:
                continue
            all_concept_labels.add(target)

    # concept/entity 노드에 물리적 노드 파일(node_store.py)이 있으면 그래프가
    # 별도로 대표 라벨을 계산하지 않고 그 파일의 display_label/slug를 그대로
    # 쓴다(_resolve_labels 참고) - 그래프 라벨과 실제 md 파일이 항상 일치한다.
    # 목록은 노드 수와 무관하게 폴더당 한 번만 스캔한다.
    concept_nodes_store = list_nodes(NODE_STORE_ROOT, "concept")
    entity_nodes_store = list_nodes(NODE_STORE_ROOT, "entity")
    concept_resolved = _resolve_labels(all_concept_labels, concept_aliases, concept_nodes_store)
    entity_resolved = _resolve_labels(all_entity_labels, entity_aliases, entity_nodes_store)

    for n in notes:
        claimed = {c["label"] for c in n["concepts"]} | {e["label"] for e in n["entities"]}

        for c in n["concepts"]:
            canonical, node_slug = concept_resolved[c["label"]]
            concept_id = f"concept:{canonical}"
            if concept_id not in seen_concept_nodes:
                nodes.append({"id": concept_id, "label": canonical, "type": "concept", "node_slug": node_slug})
                seen_concept_nodes.add(concept_id)
            edges.append({"source": n["slug"], "target": concept_id, "type": "link"})

        for entity in n["entities"]:
            entity_canonical, entity_node_slug = entity_resolved[entity["label"]]
            entity_id = f"entity:{entity_canonical}"
            if entity_id not in seen_entity_nodes:
                nodes.append(
                    {"id": entity_id, "label": entity_canonical, "type": "entity", "node_slug": entity_node_slug}
                )
                seen_entity_nodes.add(entity_id)

            concept_label = entity.get("concept")
            if concept_label:
                canonical, node_slug = concept_resolved[concept_label]
                concept_id = f"concept:{canonical}"
                if concept_id not in seen_concept_nodes:
                    nodes.append({"id": concept_id, "label": canonical, "type": "concept", "node_slug": node_slug})
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
            canonical, node_slug = concept_resolved[target]
            concept_id = f"concept:{canonical}"
            if concept_id not in seen_concept_nodes:
                nodes.append({"id": concept_id, "label": canonical, "type": "concept", "node_slug": node_slug})
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

        # concept에 딸린 entity는 논문과 2촌(논문 -> concept -> entity)이라 위
        # 1촌 필터에서 빠진다. focus의 concept 노드에 연결된 entity만 추가로
        # 포함시킨다(다른 논문으로 확장되지 않도록 concept -> entity 에지로 한정).
        concept_ids_in_focus = {nid for nid in keep_ids if nid.startswith("concept:")}
        for e in edges:
            if e["source"] in concept_ids_in_focus and e["target"].startswith("entity:"):
                keep_ids.add(e["target"])

        nodes = [n for n in nodes if n["id"] in keep_ids]
        edges = [e for e in edges if e["source"] in keep_ids and e["target"] in keep_ids]

    return {"nodes": nodes, "edges": edges, "focus": focus_slug}
