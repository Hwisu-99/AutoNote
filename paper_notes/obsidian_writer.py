from __future__ import annotations

from pathlib import Path


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

    content = f"""---
title: "{summary['title']}"
authors: "{summary['authors']}"
tags: {summary.get('tags', [])}
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
