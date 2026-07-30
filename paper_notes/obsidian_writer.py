from __future__ import annotations

import json
import shutil
from pathlib import Path

import yaml


def write_note(vault_path: str, summary: dict, title_slug: str, excalidraw_filename: str) -> str:
    """요약 결과를 Obsidian vault의 논문별 폴더에 마크다운 노트로 저장하고, 저장된 파일 경로를 반환한다."""
    folder = Path(vault_path) / "AutoNote" / title_slug
    folder.mkdir(parents=True, exist_ok=True)

    filename = f"{title_slug}.md"
    note_path = folder / filename

    tags = " ".join(f"#{t.replace(' ', '_')}" for t in summary.get("tags", []))
    contributions = "\n".join(f"- {c}" for c in summary.get("key_contributions", []))

    concepts = summary.get("concepts", [])
    concept_label_by_id = {c["id"]: c["label"] for c in concepts}
    concept_links = "\n".join(f"- [[{c['label']}]]" for c in concepts)

    relationship_lines = []
    for r in summary.get("relationships", []):
        from_label = concept_label_by_id.get(r["from_id"], r["from_id"])
        to_label = concept_label_by_id.get(r["to_id"], r["to_id"])
        label = r.get("label")
        arrow = f"→ ({label}) →" if label else "→"
        relationship_lines.append(f"- [[{from_label}]] {arrow} [[{to_label}]]")
    relationships = "\n".join(relationship_lines)

    entities = summary.get("entities", [])
    entities_frontmatter = [
        {
            "label": e["label"],
            "concept": concept_label_by_id.get(e.get("concept_id")) if e.get("concept_id") else None,
        }
        for e in entities
    ]

    entities_by_concept: dict[str | None, list[str]] = {}
    for e in entities_frontmatter:
        entities_by_concept.setdefault(e["concept"], []).append(e["label"])

    concept_lines = []
    for c in concepts:
        concept_lines.append(f"- [[{c['label']}]]")
        for entity_label in entities_by_concept.get(c["label"], []):
            concept_lines.append(f"  - [[{entity_label}]]")
    concept_links = "\n".join(concept_lines)

    standalone_entities = entities_by_concept.get(None, [])
    standalone_entity_links = "\n".join(f"- [[{label}]]" for label in standalone_entities)

    frontmatter = {
        "title": summary["title"],
        "authors": summary["authors"],
        "tags": summary.get("tags", []),
        "concepts": [c["label"] for c in concepts],
        "entities": entities_frontmatter,
    }
    frontmatter_yaml = yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False).strip()

    content = f"""---
{frontmatter_yaml}
---

# {summary['title']}

**저자**: {summary['authors']}
{tags}

## 한 줄 요약
{summary['one_line_summary']}

## 개념도
![[{excalidraw_filename}]]

## 핵심 개념
{concept_links}

## 세부 용어
{standalone_entity_links}

## 개념 간 관계
{relationships}

## 문제 정의
{summary['problem']}

## 기존 연구의 한계
{summary['gap']}

## 핵심 아이디어
{summary['key_idea']}

## 방법론
{summary['method']}

## 주요 기여
{contributions}

## 결과
{summary['results']}

## 한계
{summary['limitations']}
"""

    note_path.write_text(content, encoding="utf-8")
    return str(note_path)


def write_summary_json(vault_path: str, title_slug: str, payload: dict) -> str:
    """프론트에 표시되는 처리 결과(API 비용, 생성된 노드 요약 등)를 .md와 같은
    폴더에 JSON으로 저장하고, 저장된 파일 경로를 반환한다."""
    folder = Path(vault_path) / "AutoNote" / title_slug
    folder.mkdir(parents=True, exist_ok=True)

    path = folder / f"{title_slug}.summary.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


def delete_note(vault_path: str, title_slug: str) -> None:
    """vault에서 해당 논문 폴더(.md + .excalidraw + 요약 JSON)를 통째로 삭제한다."""
    folder = Path(vault_path) / "AutoNote" / title_slug
    if folder.is_dir():
        shutil.rmtree(folder)
