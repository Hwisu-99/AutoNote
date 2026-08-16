from __future__ import annotations

import json
import shutil
from pathlib import Path

import yaml


def _table_cell(text: object) -> str:
    """마크다운 표 셀에 안전하게 넣을 수 있도록 파이프/줄바꿈을 이스케이프한다.
    Claude가 만든 자유 텍스트(claim/evidence/description/note)를 표 셀에 그대로
    넣으면 원문에 `|`나 개행이 섞였을 때 표가 깨지므로 한 번 거쳐야 한다."""
    return str(text).replace("|", "\\|").replace("\n", " ").strip()


def write_note(vault_path: str, summary: dict, title_slug: str, excalidraw_filename: str) -> str:
    """요약 결과를 Obsidian vault의 논문별 폴더에 마크다운 노트로 저장하고, 저장된 파일 경로를 반환한다."""
    folder = Path(vault_path) / "AutoNote" / title_slug
    folder.mkdir(parents=True, exist_ok=True)

    filename = f"{title_slug}.md"
    note_path = folder / filename

    tags = " ".join(f"#{t.replace(' ', '_')}" for t in summary.get("tags", []))

    concepts = summary.get("concepts", [])
    entities = summary.get("entities", [])
    concept_label_by_id = {c["id"]: c["label"] for c in concepts}

    entities_frontmatter = [
        {
            "label": e["label"],
            "concept": concept_label_by_id.get(e.get("concept_id")) if e.get("concept_id") else None,
            "aliases": e.get("aliases", []),
        }
        for e in entities
    ]

    frontmatter = {
        "title": summary["title"],
        "authors": summary["authors"],
        "tags": summary.get("tags", []),
        "concepts": [{"label": c["label"], "aliases": c.get("aliases", [])} for c in concepts],
        "entities": entities_frontmatter,
    }
    frontmatter_yaml = yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False).strip()

    source_meta = summary.get("source_meta", "").strip()
    meta_line = f"*{source_meta}*\n" if source_meta else ""

    problem_motivation = "\n".join(f"- {b}" for b in summary.get("problem_motivation", []))

    claims_rows = "\n".join(
        f"| {i} | {_table_cell(c['claim'])} | {_table_cell(c['evidence'])} |"
        for i, c in enumerate(summary.get("claims", []), start=1)
    )
    claims_table = f"| # | 주장 | 근거 |\n|---|---|---|\n{claims_rows}" if claims_rows else "_없음_"

    reference_rows = [
        f"| [[{_table_cell(c['label'])}]] | {_table_cell(c.get('description', ''))} | {_table_cell(c.get('note', ''))} |"
        for c in concepts
    ]
    for e in entities:
        parent_label = concept_label_by_id.get(e.get("concept_id")) if e.get("concept_id") else None
        note = parent_label if parent_label else e.get("note", "")
        reference_rows.append(
            f"| [[{_table_cell(e['label'])}]] | {_table_cell(e.get('description', ''))} | {_table_cell(note)} |"
        )
    reference_table = (
        "| 개념 | 설명 | 비고 |\n|---|---|---|\n" + "\n".join(reference_rows) if reference_rows else "_없음_"
    )

    deep_dive_sections = []
    for c in concepts:
        deep_dive = c.get("deep_dive")
        if not deep_dive:
            continue
        marker = ""
        if deep_dive["analogy_is_original"]:
            marker = " (요약자 비유)"
        elif deep_dive.get("quote_verified") is False:
            marker = " ⚠️ (원문 인용 확인 안됨)"
        deep_dive_sections.append(
            f"### [[{c['label']}]]\n\n"
            f"- **왜 필요했나**: {deep_dive['why_needed']}\n"
            f"- **비유 또는 직관**: {deep_dive['analogy']}{marker}\n"
            f"- **기존 방식과 차이**: {deep_dive['difference']}\n"
            f"- **최소 예시**: {deep_dive['minimal_example']}\n"
            f"- **왜 중요한가**: {deep_dive['why_important']}"
        )
    deep_dive_body = "\n\n".join(deep_dive_sections) if deep_dive_sections else "_선정된 핵심 개념 없음_"

    results_body = "\n\n".join(
        f"### {r['section_title']}\n{r['content_markdown']}" for r in summary.get("results", [])
    )

    flow_diagram = summary.get("flow_diagram_mermaid", "").strip()

    content = f"""---
{frontmatter_yaml}
---

# {summary['title']}

**저자**: {summary['authors']}
{meta_line}{tags}

> **TL;DR**: {summary['tldr']}

## 🎯 문제 정의 & 동기
{problem_motivation}

## 💡 핵심 주장
{claims_table}

## 🔧 핵심 개념 / 사용 기술
{reference_table}

## 개념도
![[{excalidraw_filename}]]

## 🔬 핵심 개념 풀어보기
{deep_dive_body}

## 📊 평가 결과
{results_body}

## ⚠️ 한계
{summary['limitations']}

## 🧭 전체 흐름 지도
```mermaid
{flow_diagram}
```
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
