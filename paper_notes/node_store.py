"""concept/entity를 위한 물리적 Obsidian 노트 파일(_concepts/, _entities/)을 관리한다.

지금까지 concept/entity는 논문 md의 frontmatter 안에만 존재하는 문자열이었고, 실제
"같은 개념" 판정(dedup)은 /api/graph를 호출할 때마다 vault 전체를 다시 스캔해서
그 순간 계산됐다(paper_notes/graph_builder.py + dedup.py). 이 모듈은 그 판정을 논문을
처리하는 시점(쓸 때)으로 옮겨, concept/entity마다 실제로 클릭해서 들어갈 수 있는
노트 파일을 만든다 - 사용자가 그 노드에 대해 직접 메모를 남길 수 있게 하고, 나중에
brain consolidation 때 노드 단위로 비교/병합할 수 있게 하기 위함이다.

병합 정책: 새 논문이 제공한 alias가 이미 존재하는 서로 다른 두 노드 파일을 잇는
경우(예: 논문 A의 "MoE", 논문 B의 "Mixture-of-Experts Layer"를 논문 C가 이어줌),
파일을 즉시 합치지 않는다. 잘못된 병합(예: 상위/하위 개념을 같은 것으로 오판)은
사용자가 이미 노드에 메모를 남긴 뒤라면 되돌리기 어렵기 때문에, _merge_candidates.json에
후보(status=pending)로만 기록해두고, 실제 병합(execute_merge)은 review_merge_candidates.py
CLI로 사람이 검토·승인한 뒤에만 실행한다. 병합되면 나중 생성된 쪽은 삭제되지 않고
redirect_to를 가진 스텁으로 축소되며(다른 노트가 그 slug로 건 wikilink가 깨지지
않게), 그 라벨은 생존 노드의 aliases에 합쳐져 Obsidian 자체의 alias 해석으로도
정상 연결된다.

저장 위치: 노드 파일은 Obsidian vault가 아니라 이 프로젝트 폴더(NODE_STORE_ROOT)
아래 _concepts/_entities/config에 저장한다 - vault 안에 두면 Obsidian에서 노드를
클릭해 열람/메모할 수 있다는 장점이 있지만, vault 밖에 두기로 한 것은 사용자의
명시적 선택이다(구현 시 이 트레이드오프를 안내했고, vault 밖으로 두는 쪽을 확정함).

이번 단계는 새로 처리되는 논문에만 적용한다 - 기존에 이미 처리된 논문들은 아직 이
노드 파일 구조로 옮기지 않았고, graph_builder.py가 여전히 frontmatter 기반 실시간
dedup으로 그래프를 그린다(이 모듈은 별도로 병행되는 시스템이다).
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import yaml

from paper_notes.dedup import normalize_label

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
_DIR_BY_TYPE = {"concept": "_concepts", "entity": "_entities"}

# 노드 파일이 실제로 저장되는 기본 위치: 이 프로젝트 폴더(paper_notes/의 부모).
# resolve_or_create_node 등은 store_root를 인자로 받으므로(테스트에서는 임시
# 디렉터리를 대신 넘겨 격리한다), 실제 호출부(app.py 등)에서 이 상수를 쓴다.
NODE_STORE_ROOT = str(Path(__file__).resolve().parent.parent)

_AUTO_MARKER = (
    "<!-- auto-generated: 이 아래는 논문을 처리할 때마다 파이프라인이 자동으로 "
    "다시 씁니다. 직접 수정하지 마세요. -->"
)
_USER_MARKER = (
    "<!-- user-notes: 이 아래는 자동 생성 영역이 아닙니다. 자유롭게 메모를 "
    "남기세요 - 파이프라인은 이 아래 내용을 절대 덮어쓰지 않습니다. -->"
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _node_dir(store_root: str, node_type: str) -> Path:
    folder = Path(store_root) / _DIR_BY_TYPE[node_type]
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _slugify(label: str) -> str:
    return normalize_label(label).replace(" ", "-")


def _identity_keys(label: str, aliases: list[str]) -> set[str]:
    return {normalize_label(name) for name in [label, *aliases] if name and name.strip()}


def _read_frontmatter(path: Path) -> dict:
    match = _FRONTMATTER_RE.match(path.read_text(encoding="utf-8"))
    if not match:
        return {}
    try:
        return yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        return {}


def _extract_user_section(path: Path) -> str:
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8")
    idx = text.find(_USER_MARKER)
    if idx == -1:
        return ""
    return text[idx + len(_USER_MARKER) :].lstrip("\n")


def _existing_nodes(store_root: str, node_type: str) -> list[dict]:
    nodes = []
    for path in sorted(_node_dir(store_root, node_type).glob("*.md")):
        frontmatter = _read_frontmatter(path)
        # redirect_to가 있으면 병합되어 사라진 노드의 스텁이다 - 더 이상 독립된
        # 노드가 아니므로 새 concept/entity와의 매칭 대상에서 제외한다.
        if not frontmatter.get("slug") or frontmatter.get("redirect_to"):
            continue
        nodes.append({**frontmatter, "path": path})
    return nodes


def get_display_label(store_root: str, node_type: str, slug: str) -> str:
    path = _node_dir(store_root, node_type) / f"{slug}.md"
    return _read_frontmatter(path).get("display_label", slug)


def _write_node_file(path: Path, frontmatter: dict, user_section: str) -> None:
    frontmatter_yaml = yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False).strip()

    auto_lines = ["## 등장 논문"]
    for src in frontmatter["sources"]:
        auto_lines.append(f"- [[{src['slug']}|{src['title']}]]")
    auto_section = "\n".join(auto_lines)

    content = (
        f"---\n{frontmatter_yaml}\n---\n\n"
        f"{_AUTO_MARKER}\n\n{auto_section}\n\n"
        f"{_USER_MARKER}\n{user_section}"
    )
    path.write_text(content, encoding="utf-8")


def _candidates_path(store_root: str) -> Path:
    folder = Path(store_root) / "config"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "_merge_candidates.json"


def _load_candidates(store_root: str) -> list[dict]:
    path = _candidates_path(store_root)
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []


def _save_candidates(store_root: str, candidates: list[dict]) -> None:
    _candidates_path(store_root).write_text(
        json.dumps(candidates, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _find_candidate(candidates: list[dict], node_type: str, slug_a: str, slug_b: str) -> dict | None:
    key = tuple(sorted([slug_a, slug_b]))
    for c in candidates:
        if c["type"] == node_type and tuple(sorted([c["slug_a"], c["slug_b"]])) == key:
            return c
    return None


def _record_merge_candidate(
    store_root: str, node_type: str, primary_slug: str, other_slug: str, via_label: str, detected_in: str
) -> None:
    candidates = _load_candidates(store_root)
    # 상태(pending/rejected/merged) 상관없이 이미 기록된 적 있으면 다시 추가하지
    # 않는다 - 그렇지 않으면 사람이 거부한 후보가 새 논문이 들어올 때마다 계속
    # 재등장하게 된다.
    if _find_candidate(candidates, node_type, primary_slug, other_slug):
        return

    candidates.append(
        {
            "type": node_type,
            "slug_a": primary_slug,
            "slug_b": other_slug,
            "via_alias": via_label,
            "detected_in_paper": detected_in,
            "detected_at": _now_iso(),
            "status": "pending",
        }
    )
    _save_candidates(store_root, candidates)


def list_merge_candidates(store_root: str, status: str = "pending") -> list[dict]:
    return [c for c in _load_candidates(store_root) if c.get("status", "pending") == status]


def reject_merge_candidate(store_root: str, node_type: str, slug_a: str, slug_b: str) -> None:
    candidates = _load_candidates(store_root)
    candidate = _find_candidate(candidates, node_type, slug_a, slug_b)
    if candidate is None:
        return
    candidate["status"] = "rejected"
    candidate["rejected_at"] = _now_iso()
    _save_candidates(store_root, candidates)


def _write_redirect_stub(path: Path, slug: str, redirect_to: str) -> None:
    frontmatter = {"slug": slug, "redirect_to": redirect_to, "merged_at": _now_iso()}
    frontmatter_yaml = yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False).strip()
    content = f"---\n{frontmatter_yaml}\n---\n\n이 노드는 [[{redirect_to}]]로 병합되었습니다.\n"
    path.write_text(content, encoding="utf-8")


def execute_merge(store_root: str, node_type: str, slug_a: str, slug_b: str) -> str:
    """slug_a/slug_b 두 노드 파일을 하나로 합친다. 더 먼저 생성된 쪽이 생존자가
    되고, 나중 생성된 쪽은 내용이 흡수된 뒤 redirect 스텁으로 축소된다. 사람이
    검토·승인한 뒤에만 호출돼야 한다(review_merge_candidates.py). 생존자 slug를
    반환한다."""
    node_dir = _node_dir(store_root, node_type)
    path_a, path_b = node_dir / f"{slug_a}.md", node_dir / f"{slug_b}.md"
    fm_a, fm_b = _read_frontmatter(path_a), _read_frontmatter(path_b)

    if fm_a.get("created_at", "") <= fm_b.get("created_at", ""):
        survivor_path, survivor_fm, loser_path, loser_fm = path_a, fm_a, path_b, fm_b
    else:
        survivor_path, survivor_fm, loser_path, loser_fm = path_b, fm_b, path_a, fm_a

    merged_aliases = set(survivor_fm.get("aliases") or [])
    merged_aliases.add(loser_fm["display_label"])
    merged_aliases.update(loser_fm.get("aliases") or [])
    survivor_fm["aliases"] = sorted(merged_aliases)

    survivor_sources = survivor_fm.get("sources") or []
    existing_source_slugs = {s["slug"] for s in survivor_sources}
    for s in loser_fm.get("sources") or []:
        if s["slug"] not in existing_source_slugs:
            survivor_sources.append(s)
    survivor_fm["sources"] = survivor_sources

    merged_from = survivor_fm.get("merged_from") or []
    merged_from.append(loser_fm["slug"])
    merged_from.extend(loser_fm.get("merged_from") or [])
    survivor_fm["merged_from"] = merged_from

    survivor_user_section = _extract_user_section(survivor_path)
    loser_user_section = _extract_user_section(loser_path)
    if loser_user_section.strip():
        divider = (
            f"\n\n---\n*(아래는 {loser_fm['slug']}.md에서 병합된 메모입니다, "
            f"{_now_iso()[:10]})*\n\n"
        )
        survivor_user_section = survivor_user_section + divider + loser_user_section

    _write_node_file(survivor_path, survivor_fm, survivor_user_section)
    _write_redirect_stub(loser_path, loser_fm["slug"], survivor_fm["slug"])

    candidates = _load_candidates(store_root)
    candidate = _find_candidate(candidates, node_type, slug_a, slug_b)
    if candidate is not None:
        candidate["status"] = "merged"
        candidate["merged_at"] = _now_iso()
        _save_candidates(store_root, candidates)

    return survivor_fm["slug"]


def resolve_or_create_node(
    store_root: str,
    node_type: str,
    label: str,
    aliases: list[str],
    source_slug: str,
    source_title: str,
    category: str | None = None,
) -> str:
    """label/aliases에 해당하는 concept/entity 노드 파일을 찾아 갱신하거나 새로
    만들고, 최종 slug를 반환한다.

    기존 노드가 둘 이상 걸리면(새 alias가 서로 다른 두 기존 노드를 잇는 경우)
    파일을 바로 합치지 않는다 - 가장 먼저 생성된 노드를 대표(primary)로 삼아
    갱신하고, 나머지는 _merge_candidates.json에 병합 후보로만 기록한다.
    """
    keys = _identity_keys(label, aliases)
    existing = _existing_nodes(store_root, node_type)
    matches = [n for n in existing if keys & _identity_keys(n["display_label"], n.get("aliases") or [])]

    if not matches:
        return _create_node(store_root, node_type, label, aliases, source_slug, source_title, category)

    matches.sort(key=lambda n: n.get("created_at", ""))
    primary = matches[0]
    _update_node(primary["path"], label, aliases, source_slug, source_title)

    for other in matches[1:]:
        _record_merge_candidate(store_root, node_type, primary["slug"], other["slug"], label, source_slug)

    return primary["slug"]


def _create_node(
    store_root: str,
    node_type: str,
    label: str,
    aliases: list[str],
    source_slug: str,
    source_title: str,
    category: str | None,
) -> str:
    slug = _slugify(label)
    path = _node_dir(store_root, node_type) / f"{slug}.md"
    frontmatter = {
        "slug": slug,
        "type": node_type,
        "display_label": label,
        "aliases": aliases,
        "sources": [{"slug": source_slug, "title": source_title}],
        "created_at": _now_iso(),
    }
    if node_type == "concept":
        frontmatter["category"] = category
    _write_node_file(path, frontmatter, user_section="")
    return slug


def _update_node(path: Path, label: str, aliases: list[str], source_slug: str, source_title: str) -> None:
    frontmatter = _read_frontmatter(path)

    # display_label은 최초 생성 시 표기로 고정한다("나중 표기가 항상 더 낫다"는
    # 보장이 없다 - OCR 품질 등으로 오히려 나중 논문이 더 못생긴 표기를 줄 수도
    # 있다). slug와 마찬가지로 노드 정체성은 최초 생성 시 확정, 이후 변경 없음.
    existing_aliases = set(frontmatter.get("aliases") or [])
    existing_aliases.update(a for a in aliases if a)
    frontmatter["aliases"] = sorted(existing_aliases)

    sources = frontmatter.get("sources") or []
    if not any(s["slug"] == source_slug for s in sources):
        sources.append({"slug": source_slug, "title": source_title})
    frontmatter["sources"] = sources

    user_section = _extract_user_section(path)
    _write_node_file(path, frontmatter, user_section)
