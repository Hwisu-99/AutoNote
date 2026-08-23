from __future__ import annotations

import re
from pathlib import Path

import yaml

from paper_notes.node_store import IMAGE_EXTENSIONS, NODE_STORE_ROOT, list_nodes

# Obsidian wikilink syntax: [[target]], [[target|alias]], embeds !\[\[target]]
_WIKILINK_RE = re.compile(r"!?\[\[([^\]|#]+)(?:\|[^\]]+)?\]\]")
# concept/entity 노드 편집 UI가 첨부 이미지를 넣을 때 쓰는 마크다운 이미지 문법: ![alt](경로)
_MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
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
    """AutoNote/ 폴더의 논문 노트와 node_store(_concepts/_entities)를 스캔해
    Obsidian 그래프 뷰와 같은 방식으로 노드/에지를 만든다: 노트 = 주황 노드,
    concept = 파랑 노드, entity = 회색 노드, 공통 tag = 초록 노드를 매개로 한 에지.

    concept/entity는 각 노드 파일의 sources 목록이 유일한 소스다 - 논문 노트
    frontmatter에는 concepts/entities가 더 이상 없다(node_store.resolve_or_create_node가
    논문을 처리하는 시점에 한 번만 라벨을 판정해 sources에 박아두므로, 그래프를
    그릴 때마다 라벨을 다시 판정할 필요가 없다). entity의 sources 항목에
    concept_slug가 있으면 그 논문에서는 해당 concept 밑에 묶인 것이고, 없으면
    논문에 직접 연결된 것이다(같은 entity라도 논문마다 다를 수 있음)."""
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

        # 본문의 [[위키링크]]는 이제 concept 후보로 취급하지 않는다(node_store가
        # 유일한 소스) - 다른 논문 노트로의 직접 링크와, Obsidian이 붙여넣은 첨부
        # 이미지 감지 용도로만 쓴다.
        links: set[str] = set()
        attachments: set[str] = set()
        for wikilink in _WIKILINK_RE.finditer(body):
            target = wikilink.group(1).strip()
            if target.lower().endswith(".excalidraw"):
                continue  # 개념도 임베드는 다른 논문 노트가 아니므로 그래프 에지에서 제외
            if Path(target).suffix.lower() in IMAGE_EXTENSIONS:
                attachments.add(target)
                continue
            links.add(target)
        for md_image in _MD_IMAGE_RE.finditer(body):
            attachments.add(md_image.group(1).strip())

        notes.append(
            {
                "slug": slug,
                "title": title,
                "tags": tags,
                "links": links,
                "attachments": attachments,
            }
        )

    note_slugs = {n["slug"] for n in notes}
    nodes = [{"id": n["slug"], "label": n["title"], "type": "note"} for n in notes]
    edges: list[dict] = []
    seen_tag_nodes: set[str] = set()
    seen_attachment_nodes: set[str] = set()

    def _add_attachment(parent_id: str, owner_type: str, owner_slug: str, src: str) -> None:
        # owner_type/owner_slug/src는 클릭 시 실제 이미지를 열기 위한 정보다: concept/entity
        # 첨부는 src가 이미 /attachments 마운트 기준 상대경로라 그대로 쓸 수 있지만, note에
        # Obsidian이 붙여넣은 첨부(![[파일명]])는 vault 어디에 실제로 있는지 여기선 알 수
        # 없어 프런트가 note_slug/filename으로 /api/vault-attachment에 물어봐야 한다.
        label = Path(src).name or src
        attachment_id = f"attachment:{parent_id}:{label}"
        if attachment_id not in seen_attachment_nodes:
            nodes.append(
                {
                    "id": attachment_id,
                    "label": label,
                    "type": "attachment",
                    "owner_type": owner_type,
                    "owner_slug": owner_slug,
                    "src": src,
                }
            )
            seen_attachment_nodes.add(attachment_id)
        edges.append({"source": parent_id, "target": attachment_id, "type": "link"})

    for n in notes:
        for target in n["links"]:
            if target in note_slugs:
                edges.append({"source": n["slug"], "target": target, "type": "link"})

        for tag in n["tags"]:
            tag_id = f"tag:{tag}"
            if tag_id not in seen_tag_nodes:
                nodes.append({"id": tag_id, "label": f"#{tag}", "type": "tag"})
                seen_tag_nodes.add(tag_id)
            edges.append({"source": n["slug"], "target": tag_id, "type": "tag"})

        for src in n["attachments"]:
            _add_attachment(n["slug"], "note", n["slug"], src)

    # concept/entity는 node_store가 유일한 소스다 - 논문이 몇 편을 참조하든(0편,
    # 즉 orphan 포함) 항상 그래프에 노드로 얹는다. 에지는 각 노드의 sources
    # 항목이 있을 때만 생긴다 - orphan은 자연히 에지 없는 노드가 된다(예전처럼
    # "orphan만 별도로 얹는" 특수 처리가 필요 없다).
    concept_nodes_store = list_nodes(NODE_STORE_ROOT, "concept")
    entity_nodes_store = list_nodes(NODE_STORE_ROOT, "entity")
    concept_id_by_slug = {c["slug"]: f"concept:{c['display_label']}" for c in concept_nodes_store}

    orphan_ids: set[str] = set()

    for c in concept_nodes_store:
        concept_id = concept_id_by_slug[c["slug"]]
        sources = c.get("sources") or []
        nodes.append({"id": concept_id, "label": c["display_label"], "type": "concept",
                       "node_slug": c["slug"], "anchor_id": c.get("anchor_id")})
        if not sources:
            orphan_ids.add(concept_id)
        for source in sources:
            paper_slug = source.get("slug")
            if paper_slug in note_slugs:
                edges.append({"source": paper_slug, "target": concept_id, "type": "link"})

    for e in entity_nodes_store:
        entity_id = f"entity:{e['display_label']}"
        sources = e.get("sources") or []
        nodes.append({"id": entity_id, "label": e["display_label"], "type": "entity",
                       "node_slug": e["slug"], "anchor_id": e.get("anchor_id")})
        if not sources:
            orphan_ids.add(entity_id)
        for source in sources:
            concept_slug = source.get("concept_slug")
            if concept_slug and concept_slug in concept_id_by_slug:
                edges.append({"source": concept_id_by_slug[concept_slug], "target": entity_id, "type": "link"})
            else:
                paper_slug = source.get("slug")
                if paper_slug in note_slugs:
                    edges.append({"source": paper_slug, "target": entity_id, "type": "link"})

    # concept/entity 노드도 (node_store.py의 편집 UI로) 이미지를 첨부할 수 있다.
    for store_nodes, prefix in ((concept_nodes_store, "concept"), (entity_nodes_store, "entity")):
        for store_node in store_nodes:
            node_id = f"{prefix}:{store_node['display_label']}"
            text = store_node["path"].read_text(encoding="utf-8")
            for md_image in _MD_IMAGE_RE.finditer(text):
                _add_attachment(node_id, prefix, store_node["slug"], md_image.group(1).strip())

    if only_focus and focus_slug:
        keep_ids = {focus_slug}
        for e in edges:
            if e["source"] == focus_slug:
                keep_ids.add(e["target"])
            elif e["target"] == focus_slug:
                keep_ids.add(e["source"])

        # orphan은 이 focus와 1~2촌 범위인지와 무관하게 항상 남겨둔다 - 연결할 대상을
        # 찾는 동안(드래그로 연결하기 전까지) 어떤 논문의 포커스 뷰를 보고 있어도
        # 화면에서 사라지면 안 되기 때문.
        keep_ids |= orphan_ids

        # concept에 딸린 entity는 논문과 2촌(논문 -> concept -> entity)이라 위
        # 1촌 필터에서 빠진다. focus의 concept 노드에 연결된 entity만 추가로
        # 포함시킨다(다른 논문으로 확장되지 않도록 concept -> entity 에지로 한정).
        concept_ids_in_focus = {nid for nid in keep_ids if nid.startswith("concept:")}
        for e in edges:
            if e["source"] in concept_ids_in_focus and e["target"].startswith("entity:"):
                keep_ids.add(e["target"])

        # concept/entity에 달린 첨부 이미지는 그 concept/entity가 이미 keep_ids에 있을
        # 때만(위 확장까지 끝난 뒤) 같이 딸려온다 - note에 직접 붙은 첨부는 focus_slug
        # 자체가 source라 첫 루프에서 이미 포함된다.
        for e in edges:
            if e["source"] in keep_ids and e["target"].startswith("attachment:"):
                keep_ids.add(e["target"])

        nodes = [n for n in nodes if n["id"] in keep_ids]
        edges = [e for e in edges if e["source"] in keep_ids and e["target"] in keep_ids]

    return {"nodes": nodes, "edges": edges, "focus": focus_slug}
