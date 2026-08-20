from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import yaml

from paper_notes.dedup import normalize_label

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def _read_frontmatter_and_body(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    try:
        frontmatter = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        frontmatter = {}
    return frontmatter, text[match.end() :]


def _rewrite_frontmatter(path: Path, frontmatter: dict, body: str) -> None:
    """frontmatter만 다시 쓰고 본문(body)은 손대지 않는다 - write_note()와 달리
    이미 LLM이 써둔 본문 서사를 매번 다시 만들 필요가 없을 때(사용자가 나중에
    concept/entity를 직접 추가하는 경우 등) 쓴다."""
    frontmatter_yaml = yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False).strip()
    path.write_text(f"---\n{frontmatter_yaml}\n---\n{body}", encoding="utf-8")


def add_concept_to_note(vault_path: str, title_slug: str, label: str) -> None:
    """논문 노트 frontmatter의 concepts 목록에 사용자가 직접 추가한 concept 하나를
    끼워넣는다(본문은 그대로 둠 - 레퍼런스 표 등은 갱신하지 않기로 결정함)."""
    path = Path(vault_path) / "AutoNote" / title_slug / f"{title_slug}.md"
    if not path.is_file():
        raise FileNotFoundError(f"논문 노트를 찾을 수 없습니다: {title_slug}")
    frontmatter, body = _read_frontmatter_and_body(path)
    concepts = frontmatter.get("concepts") or []
    concepts.append({"label": label, "aliases": []})
    frontmatter["concepts"] = concepts
    _rewrite_frontmatter(path, frontmatter, body)


def add_entity_to_note(vault_path: str, title_slug: str, label: str, concept_label: str | None) -> None:
    """논문 노트 frontmatter의 entities 목록에 사용자가 직접 추가한 entity 하나를
    끼워넣는다. concept_label을 주면 그 concept 밑에 걸리고(그래프에서 concept ->
    entity 에지), 없으면 이 논문에 직접 걸린다(note -> entity 에지) - 기존
    LLM 추출 entity와 완전히 같은 방식."""
    path = Path(vault_path) / "AutoNote" / title_slug / f"{title_slug}.md"
    if not path.is_file():
        raise FileNotFoundError(f"논문 노트를 찾을 수 없습니다: {title_slug}")
    frontmatter, body = _read_frontmatter_and_body(path)
    entities = frontmatter.get("entities") or []
    entities.append({"label": label, "concept": concept_label, "aliases": []})
    frontmatter["entities"] = entities
    _rewrite_frontmatter(path, frontmatter, body)


def remove_node_from_note(vault_path: str, title_slug: str, identity_keys: set[str], is_concept: bool) -> None:
    """concept/entity 노드를 삭제할 때, 그 노드를 참조하던 논문 frontmatter에서도
    참조를 걷어낸다(안 그러면 실제 노드 파일 없는 "죽은" 라벨이 그래프에 클릭 안 되는
    상태로 계속 남음). identity_keys는 삭제된 노드의 display_label+aliases를 정규화한
    집합 - 이 논문 frontmatter에 그 노드가 정확히 어떤 표기로 적혀있었는지 몰라도
    매칭할 수 있다.

    concept을 지우는 경우 그 concept을 참조하던 entity는 같이 지우지 않고
    concept 연결만 풀어준다(entity 자체를 지울지는 별도 결정 - entity는 논문에
    직접 연결된 상태로 남는 게 더 안전한 기본값)."""
    path = Path(vault_path) / "AutoNote" / title_slug / f"{title_slug}.md"
    if not path.is_file():
        return
    frontmatter, body = _read_frontmatter_and_body(path)

    def is_match(label: str) -> bool:
        return bool(label) and normalize_label(label) in identity_keys

    if is_concept:
        frontmatter["concepts"] = [
            c for c in (frontmatter.get("concepts") or [])
            if not is_match(c if isinstance(c, str) else c.get("label", ""))
        ]
        for e in frontmatter.get("entities") or []:
            if is_match(e.get("concept") or ""):
                e["concept"] = None
    else:
        frontmatter["entities"] = [
            e for e in (frontmatter.get("entities") or []) if not is_match(e.get("label", ""))
        ]

    _rewrite_frontmatter(path, frontmatter, body)


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
        breakdown_rows = "\n".join(
            f'| "{_table_cell(item["clause"])}" | {_table_cell(item["explanation"])} |'
            for item in deep_dive["insight_breakdown"]
        )
        breakdown_table = f"| 구절 | 풀이 |\n|---|---|\n{breakdown_rows}" if breakdown_rows else ""
        deep_dive_sections.append(
            f"### [[{c['label']}]]\n\n"
            f"{deep_dive['setup']}\n\n"
            f"> **핵심 통찰**: {deep_dive['core_insight']}\n\n"
            f"{breakdown_table}\n\n"
            f"**왜 중요한가**: {deep_dive['why_it_matters']}"
        )
    deep_dive_body = "\n\n".join(deep_dive_sections) if deep_dive_sections else "_선정된 핵심 개념 없음_"

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

## ⚠️ 한계 & 향후 과제
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
