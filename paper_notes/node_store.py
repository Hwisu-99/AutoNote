"""concept/entity를 위한 물리적 Obsidian 노트 파일(_concepts/, _entities/)을 관리한다.

지금까지 concept/entity는 논문 md의 frontmatter 안에만 존재하는 문자열이었고, 실제
"같은 개념" 판정(dedup)은 /api/graph를 호출할 때마다 vault 전체를 다시 스캔해서
그 순간 계산됐다(paper_notes/graph_builder.py + dedup.py). 이 모듈은 그 판정을 논문을
처리하는 시점(쓸 때)으로 옮겨, concept/entity마다 실제로 클릭해서 들어갈 수 있는
노트 파일을 만든다 - 사용자가 그 노드에 대해 직접 메모를 남길 수 있게 하고, 나중에
brain consolidation 때 노드 단위로 비교/병합할 수 있게 하기 위함이다.

매칭 기준: 정규화 완전일치, alias 겹침에 더해 dedup.py의 labels_match()(MinHash
자카드 유사도, dedupe_labels()와 같은 기준)까지 포함한다 - graph_builder.py가
쓰는 판정과 동일한 함수를 공유해야, 그래프 뷰의 "대표 라벨"과 이 모듈이 고정한
display_label이 살짝 다른 표기(예: 단수/복수)일 때도 같은 노드로 인식되고, 두
시스템이 서로 다르게 판단해 어긋나는 문제가 생기지 않는다.

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
import os
import re
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import yaml

from paper_notes.dedup import labels_match, normalize_label

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
_DIR_BY_TYPE = {"concept": "_concepts", "entity": "_entities"}
_ATTACHMENTS_DIR_BY_TYPE = {"concept": "concepts", "entity": "entities"}
# graph_builder.py도 첨부 이미지를 노드 md 본문에서 찾아 그래프의 attachment
# 노드로 만들 때 같은 확장자 목록을 써야 하므로 공개 상수로 둔다.
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

# 노드 파일이 실제로 저장되는 기본 위치: 이 프로젝트 폴더(paper_notes/의 부모).
# resolve_or_create_node 등은 store_root를 인자로 받으므로(테스트에서는 임시
# 디렉터리를 대신 넘겨 격리한다), 실제 호출부(app.py 등)에서 이 상수를 쓴다.
NODE_STORE_ROOT = str(Path(__file__).resolve().parent.parent)


class DuplicateNodeError(Exception):
    """create_node_manual()이 사용자가 입력한 이름과 비슷한(정규화 완전일치·alias·
    MinHash 유사도) 노드가 이미 있다고 판단했을 때 던진다. slug가 완전히 같아서
    막는 FileExistsError와 달리, 이건 "그래도 새로 만들기"로 사용자가 우회할 수
    있다(force=True) - 퍼지 매칭은 오탐(비슷해 보이지만 실제론 다른 개념) 가능성이
    있어 무조건 막기보다는 사용자에게 물어보는 쪽이 안전하다."""

    def __init__(self, match: dict) -> None:
        self.match = match
        super().__init__(f"'{match['display_label']}'과(와) 비슷한 노드가 이미 있습니다.")

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


def _names(label: str, aliases: list[str]) -> list[str]:
    return [n for n in [label, *aliases] if n and n.strip()]


def _matches_node(label: str, aliases: list[str], node: dict) -> bool:
    """새 label/aliases가 기존 node와 같은 개념인지 판단한다. 먼저 정규화
    완전일치나 alias 겹침으로 저렴하게 확인하고, 거기서 못 잡으면(예: 단수/복수
    차이처럼 표기만 살짝 다른 경우) graph_builder.py의 dedupe_labels()와 같은
    기준(labels_match, MinHash 자카드 유사도)까지 확인한다 - 두 시스템이 같은
    개념을 서로 다르게 판단해 그래프 뷰와 노드 파일이 어긋나는 문제를 막기 위해
    같은 판정 함수를 공유한다."""
    new_keys = _identity_keys(label, aliases)
    node_keys = _identity_keys(node["display_label"], node.get("aliases") or [])
    if new_keys & node_keys:
        return True

    new_names = _names(label, aliases)
    node_names = _names(node["display_label"], node.get("aliases") or [])
    return any(labels_match(a, b) for a in new_names for b in node_names)


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


def get_user_section(store_root: str, node_type: str, slug: str) -> str:
    """사용자가 직접 쓴 메모(원본 마크다운, 자동 생성 영역 제외)만 반환한다.
    편집 UI가 textarea를 채울 때 쓴다."""
    path = _node_dir(store_root, node_type) / f"{slug}.md"
    return _extract_user_section(path)


def get_auto_section(store_root: str, node_type: str, slug: str) -> str:
    """자동 생성 영역(description + 등장 논문 목록)만 반환한다. get_user_section()과
    짝을 이뤄, 노드 화면이 이 부분은 읽기 전용으로 보여주고 user-notes 아래만
    편집 가능하게 나눠 렌더링할 수 있게 한다."""
    path = _node_dir(store_root, node_type) / f"{slug}.md"
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8")
    start = text.find(_AUTO_MARKER)
    end = text.find(_USER_MARKER)
    if start == -1 or end == -1:
        return ""
    return text[start + len(_AUTO_MARKER) : end].strip()


def update_user_section(store_root: str, node_type: str, slug: str, user_markdown: str) -> None:
    """사용자가 편집한 메모를 저장한다. frontmatter/자동 생성 영역(등장 논문
    목록)은 건드리지 않고 user_section만 교체한다. 병합돼 사라진 redirect
    스텁에는 쓸 수 없다(더 이상 독립된 노드가 아니므로)."""
    path = _node_dir(store_root, node_type) / f"{slug}.md"
    frontmatter = _read_frontmatter(path)
    if not frontmatter.get("slug"):
        raise FileNotFoundError(f"노드 파일을 찾을 수 없습니다: {path}")
    if frontmatter.get("redirect_to"):
        raise ValueError(f"'{slug}'는 다른 노드로 병합되어 더 이상 독립된 노드가 아닙니다.")
    _write_node_file(path, frontmatter, user_markdown)


def save_attachment(store_root: str, node_type: str, slug: str, filename: str, content: bytes) -> str:
    """이미지 첨부파일을 저장하고, md 본문에서 참조할 상대경로를 반환한다
    (예: "attachments/concepts/self-attention/173..-ab12.png"). 업로드된
    파일명은 신뢰하지 않고(경로 조작 방지) 확장자만 취해 서버가 충돌 없는
    이름을 새로 붙인다. attachments/는 node_store.py의 다른 파일들과 같은 위치
    (NODE_STORE_ROOT)에 두고, app.py가 /attachments로 정적 서빙한다."""
    path = _node_dir(store_root, node_type) / f"{slug}.md"
    frontmatter = _read_frontmatter(path)
    if not frontmatter.get("slug"):
        raise FileNotFoundError(f"노드 파일을 찾을 수 없습니다: {path}")
    if frontmatter.get("redirect_to"):
        raise ValueError(f"'{slug}'는 다른 노드로 병합되어 더 이상 독립된 노드가 아닙니다.")

    ext = Path(filename).suffix.lower()
    if ext not in IMAGE_EXTENSIONS:
        raise ValueError(f"지원하지 않는 이미지 형식입니다: {ext or '(확장자 없음)'}")

    type_dir = _ATTACHMENTS_DIR_BY_TYPE[node_type]
    folder = Path(store_root) / "attachments" / type_dir / slug
    folder.mkdir(parents=True, exist_ok=True)

    generated_name = f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}{ext}"
    (folder / generated_name).write_bytes(content)

    return f"attachments/{type_dir}/{slug}/{generated_name}"


def build_node_index(nodes: list[dict]) -> dict[str, dict]:
    """{정규화된 display_label 또는 alias: 노드} 역인덱스를 만든다. 완전일치/alias
    매칭을 노드 수와 무관하게 O(1)로 만들기 위함이다 - 인덱스 없이는 라벨 하나를
    찾을 때마다 노드 목록 전체를 처음부터 훑어야 해서(find_node_fuzzy의 기존
    선형 탐색), 노드가 수만 개로 늘어나면 그래프 하나 그릴 때 라벨마다 수만 번씩
    비교가 반복돼 느려진다. 같은 정규화 키를 여러 노드가 주장하는 경우(드묾)는
    먼저 등록된 쪽을 우선한다 - 결과가 매번 달라지지 않게."""
    index: dict[str, dict] = {}
    for node in nodes:
        for name in _names(node["display_label"], node.get("aliases") or []):
            index.setdefault(normalize_label(name), node)
    return index


# store_root/node_type별로 (디렉터리 서명, 파싱된 노드 목록, 역인덱스)를 프로세스
# 메모리에 캐싱한다. 파일 내용까지는 캐시하지 않고 "이 디렉터리가 지난번과
# 똑같은지"만 저렴하게(os.scandir, 내용은 안 읽음) 확인해서 무효화 여부를 판단한다.
_LIST_NODES_CACHE: dict[tuple[str, str], tuple[tuple, list[dict], dict[str, dict]]] = {}


def _dir_signature(folder: Path) -> tuple:
    """폴더 안 .md 파일들의 (이름, 수정시각) 목록. 내용을 읽지 않고 메타데이터만
    보므로 전체 파싱보다 훨씬 저렴하다 - review_merge_candidates.py처럼 다른
    프로세스가 파일을 바꿔도, 다음 list_nodes() 호출에서 mtime 차이로 정확히
    감지된다."""
    try:
        return tuple(
            sorted((entry.name, entry.stat().st_mtime_ns) for entry in os.scandir(folder) if entry.name.endswith(".md"))
        )
    except FileNotFoundError:
        return ()


def _cached_nodes_and_index(store_root: str, node_type: str) -> tuple[list[dict], dict[str, dict]]:
    folder = _node_dir(store_root, node_type)
    key = (store_root, node_type)
    signature = _dir_signature(folder)

    cached = _LIST_NODES_CACHE.get(key)
    if cached is not None and cached[0] == signature:
        return cached[1], cached[2]

    result = _existing_nodes(store_root, node_type)
    index = build_node_index(result)
    _LIST_NODES_CACHE[key] = (signature, result, index)
    return result, index


def list_nodes(store_root: str, node_type: str) -> list[dict]:
    """존재하는 모든 노드 파일의 frontmatter 목록을 반환한다(병합돼 사라진
    redirect 스텁은 제외). 그래프 뷰처럼 여러 label을 한꺼번에 조회해야 할 때는
    이 목록을 한 번만 불러와 find_node_fuzzy()에 재사용해야 한다 - label 하나마다
    폴더를 다시 스캔하면 노드 수만큼 스캔이 반복돼 요청이 느려진다.

    디렉터리 내용이 지난 호출과 똑같으면(_dir_signature 비교) 프로세스 메모리에
    캐싱된 결과를 그대로 반환하고, 파일이 추가/삭제/수정됐을 때만 실제로 다시
    읽고 파싱한다."""
    return _cached_nodes_and_index(store_root, node_type)[0]


def node_index(store_root: str, node_type: str) -> dict[str, dict]:
    """list_nodes()와 같은 캐시를 공유하는 역인덱스(build_node_index 결과)를
    반환한다. 여러 라벨을 조회해야 하는 호출부는 이걸 한 번만 받아서
    find_node_fuzzy(..., index=...)에 넘겨 재사용해야 한다 - 매 라벨마다
    새로 만들면 인덱스를 쓰는 의미가 없어진다."""
    return _cached_nodes_and_index(store_root, node_type)[1]


def find_node_fuzzy(
    nodes: list[dict], label: str, aliases: list[str] | None = None, index: dict[str, dict] | None = None
) -> dict | None:
    """list_nodes()로 미리 불러온 노드 목록에서 label/aliases와 매칭되는(정확
    일치, alias, 또는 MinHash 퍼지 매칭) 노드를 찾아 그 frontmatter 전체를
    반환한다. 매칭이 없으면 None.

    index(node_index() 결과)가 주어지면 정규화 완전일치/alias는 먼저 O(1) 사전
    조회로 확인한다 - 대부분의 재등장 라벨은 여기서 바로 끝나고, 인덱스에 없는
    (완전히 새로운 표기의) 라벨만 아래의 기존 선형 탐색 + MinHash 퍼지 매칭으로
    넘어간다. 노드 수가 많아져도 흔한 경우의 비용이 늘어나지 않게 하기 위함."""
    if index is not None:
        for name in _names(label, aliases or []):
            hit = index.get(normalize_label(name))
            if hit is not None:
                return hit

    for node in nodes:
        if _matches_node(label, aliases or [], node):
            return node
    return None


def find_node_slug_fuzzy(
    nodes: list[dict], label: str, aliases: list[str] | None = None, index: dict[str, dict] | None = None
) -> str | None:
    """find_node_fuzzy()와 같지만 slug만 필요한 호출부를 위한 편의 함수."""
    node = find_node_fuzzy(nodes, label, aliases, index)
    return node["slug"] if node else None


def _write_node_file(path: Path, frontmatter: dict, user_section: str) -> None:
    frontmatter_yaml = yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False).strip()

    # description/note는 논문 노트의 레퍼런스 표에 있던 설명을 그대로 복사해온 것 -
    # 최초 생성 시에만 채워지므로 없을 수도 있다(그 이전에 만들어진 노드, 혹은 값을
    # 안 넘긴 호출부).
    auto_lines = []
    if frontmatter.get("description"):
        auto_lines.append(frontmatter["description"])
    if frontmatter.get("note"):
        auto_lines.append(f"*{frontmatter['note']}*")
    if auto_lines:
        auto_lines.append("")
    auto_lines.append("## 등장 논문")
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
    description: str = "",
    note: str = "",
    concept_slug: str | None = None,
) -> str:
    """label/aliases에 해당하는 concept/entity 노드 파일을 찾아 갱신하거나 새로
    만들고, 최종 slug를 반환한다.

    기존 노드가 둘 이상 걸리면(새 alias가 서로 다른 두 기존 노드를 잇는 경우,
    혹은 MinHash 퍼지 매칭으로 두 노드가 동시에 걸리는 경우) 파일을 바로 합치지
    않는다 - 가장 먼저 생성된 노드를 대표(primary)로 삼아 갱신하고, 나머지는
    _merge_candidates.json에 병합 후보로만 기록한다.

    description/note는 논문 요약 파이프라인이 이미 만들어둔 텍스트(레퍼런스 표용
    설명/비고)를 그대로 복사해오는 것이라 별도 API 호출이 필요 없다. display_label과
    같은 이유로 최초 생성 시에만 채워지고 이후 갱신에서는 바뀌지 않는다(어느 논문의
    설명이 "더 낫다"를 판단할 기준이 없음).

    concept_slug는 entity 타입일 때만 의미 있다 - 이 논문에서 이 entity가 어느
    concept 밑에 묶이는지(같은 entity라도 논문마다 다를 수 있음)를, entity 자신의
    sources 항목 하나하나에 기록한다. 호출부(app.py의 run_pipeline)가 concept을
    먼저 처리해서 얻은 최종 slug를 넘겨줘야 한다 - 그래야 이 논문에서 Claude가 쓴
    원본 concept 라벨이 아니라, 실제로 확정된 노드를 가리키게 된다.
    """
    existing = _existing_nodes(store_root, node_type)
    matches = [n for n in existing if _matches_node(label, aliases, n)]

    if not matches:
        return _create_node(
            store_root, node_type, label, aliases, source_slug, source_title, category, description, note,
            concept_slug=concept_slug,
        )

    matches.sort(key=lambda n: n.get("created_at", ""))
    primary = matches[0]
    _update_node(primary["path"], label, aliases, source_slug, source_title, concept_slug=concept_slug)

    for other in matches[1:]:
        _record_merge_candidate(store_root, node_type, primary["slug"], other["slug"], label, source_slug)

    return primary["slug"]


def create_node_manual(
    store_root: str,
    node_type: str,
    label: str,
    source_slug: str | None,
    source_title: str | None,
    category: str | None = None,
    anchor_id: str | None = None,
    force: bool = False,
    concept_slug: str | None = None,
) -> str:
    """사용자가 그래프 화면에서 직접 만드는 concept/entity 노드.

    slug(label을 정규화한 것)가 기존 파일과 완전히 같으면 force와 무관하게 항상
    막는다 - 그건 "비슷한 개념" 수준이 아니라 "같은 파일"이라, 그대로 진행하면
    기존 파일의 frontmatter와 사용자가 이미 남긴 개인 메모까지 통째로 사라진다.

    force=False(기본값)면 정확 일치 다음으로, resolve_or_create_node()가 쓰는 것과
    같은 기준(정규화 완전일치·alias·MinHash 유사도, find_node_fuzzy)으로 비슷한
    기존 노드가 있는지도 확인해 있으면 DuplicateNodeError를 던진다 - 사용자가 이미
    있는 개념/용어를 모르고 다시 만드는 걸 막기 위함(직접 만들 때는 resolve_or_create_node()
    와 달리 자동 병합은 하지 않는다 - 사용자 의도와 다르게 엉뚱한 노드에 조용히
    합쳐지면 안 되므로, 호출부가 사용자에게 확인받은 뒤 force=True로 다시 부르는
    구조). force=True면 이 퍼지 매칭 확인을 건너뛰고 새로 만든다("그래도 새로
    만들기"를 사용자가 명시적으로 선택한 경우).

    source_slug/source_title을 None으로 두면 sources가 빈 orphan 노드가 만들어진다 -
    그래프 배경 우클릭으로 만드는 노드가 이 경로를 쓴다(어느 논문과 연결할지는 나중에
    드래그로 정한다). carrier가 있는 기존 호출부(진입점 1/3)는 그대로 값을 넘긴다.

    anchor_id는 orphan 생성 시 그래프에서 가장 가까웠던 다른 노드의 id(그래프 표시용,
    예: "concept:Foo" 또는 논문 slug)를 그대로 저장해둔다 - 브라우저 메모리에만 있는
    화면 좌표 캐시는 새로고침/서버 재시작으로 사라지므로, "이 노드는 원래 저 노드
    근처에 있었다"는 최소한의 힌트를 파일에 남겨서 다음 세션에도 그 근처에 다시
    나타나게 한다(graph_builder.py가 읽어서 그래프 응답에 실어준다).

    concept_slug는 entity를 진입점 3(concept 뷰의 "+ 엔티티 추가")로 만들 때만
    쓴다 - carrier 논문에서 이 entity가 바로 그 concept 밑에 묶인다는 걸 기록한다."""
    slug = _slugify(label)
    path = _node_dir(store_root, node_type) / f"{slug}.md"
    if path.is_file():
        raise FileExistsError(f"'{label}'과(와) 이름이 같은 노드가 이미 있습니다.")

    if not force:
        nodes = list_nodes(store_root, node_type)
        idx = node_index(store_root, node_type)
        match = find_node_fuzzy(nodes, label, [], idx)
        if match:
            raise DuplicateNodeError(match)

    return _create_node(
        store_root, node_type, label, [], source_slug, source_title, category,
        anchor_id=anchor_id, concept_slug=concept_slug,
    )


def delete_node(store_root: str, node_type: str, slug: str) -> dict:
    """concept/entity 노드 파일과 그 첨부 이미지 폴더를 삭제하고, 삭제 전
    frontmatter를 그대로 반환한다. 호출부(app.py)가 이 frontmatter의
    display_label/aliases/sources로 - 이 노드를 참조하던 논문들의 frontmatter에서도
    참조를 걷어내야 하므로(안 그러면 그래프에 실제 파일 없는 "죽은" 라벨만 남음) -
    반환값이 필요하다."""
    path = _node_dir(store_root, node_type) / f"{slug}.md"
    if not path.is_file():
        raise FileNotFoundError(f"노드 파일을 찾을 수 없습니다: {slug}")
    frontmatter = _read_frontmatter(path)
    path.unlink()

    type_dir = _ATTACHMENTS_DIR_BY_TYPE.get(node_type)
    if type_dir:
        attachments_dir = Path(store_root) / "attachments" / type_dir / slug
        if attachments_dir.is_dir():
            shutil.rmtree(attachments_dir)

    return frontmatter


def remove_source(store_root: str, source_slug: str) -> None:
    """source_slug(논문 slug)를 모든 concept/entity 노드 파일의 sources[]에서 제거한다.

    두 곳에서 쓰인다: (1) 논문 삭제 시 - vault/Supabase에서 노트를 지워도 지금까진
    node_store 참조가 죽은 채로 영구히 남았다. (2) 같은 논문을 다시 처리해 덮어쓸 때 -
    옛 처리 결과의 참조를 먼저 걷어내야, 이번엔 안 나온 concept/entity가 옛 흔적으로
    계속 남지 않는다.

    제거 후 sources가 비면(다른 논문도 이 노드를 참조하지 않으면) 파일 자체를 삭제한다 -
    아무 논문도 안 쓰는 개념/용어를 굳이 남겨둘 이유가 없다. sources가 남아있으면
    _write_node_file()로 다시 써서 "## 등장 논문" 자동 섹션도 같이 갱신하고, 사용자가
    남긴 개인 메모(user_section)는 그대로 보존한다."""
    for node_type, dir_name in _DIR_BY_TYPE.items():
        folder = Path(store_root) / dir_name
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.md")):
            frontmatter = _read_frontmatter(path)
            sources = frontmatter.get("sources") or []
            if not any(s.get("slug") == source_slug for s in sources):
                continue
            frontmatter["sources"] = [s for s in sources if s.get("slug") != source_slug]
            if frontmatter["sources"]:
                user_section = _extract_user_section(path)
                _write_node_file(path, frontmatter, user_section)
            else:
                path.unlink()


def find_entities_by_concept(store_root: str, concept_slug: str) -> list[dict]:
    """이 concept 밑에 걸린 entity 노드들을 찾는다 - entity 각각의 sources 항목
    중 concept_slug가 일치하는 게 하나라도 있으면 포함시킨다(entity는 논문마다
    다른 concept 밑에 묶일 수 있으므로, sources 항목 단위로 확인해야 한다).
    concept 삭제 시 "딸린 entity도 같이 지울지" 물어보는 화면(app.py)이 이 함수로
    무엇이 딸려있는지 미리 확인한다. 예전에는 논문 frontmatter를 훑어 라벨을
    퍼지 매칭해야 했지만, 이제 entity 자신의 sources에 concept_slug가 그대로
    적혀 있어 직접 비교만 하면 된다."""
    return [
        n for n in list_nodes(store_root, "entity")
        if any(s.get("concept_slug") == concept_slug for s in (n.get("sources") or []))
    ]


def set_source_concept_slug(store_root: str, entity_slug: str, paper_slug: str, concept_slug: str) -> bool:
    """이미 있는 entity 노드 파일의 sources 중 특정 논문 항목 하나에 concept_slug를
    채워 넣는다. migrate_concept_slugs.py(1회성 마이그레이션 - 예전에는 논문
    frontmatter에만 있던 entity-concept 그룹핑을 entity 자신의 sources로 옮김)가
    쓴다. 실제로 값이 바뀌었으면 True, 항목을 못 찾았거나 이미 같은 값이면
    False를 반환한다."""
    path = _node_dir(store_root, "entity") / f"{entity_slug}.md"
    if not path.is_file():
        return False
    frontmatter = _read_frontmatter(path)
    sources = frontmatter.get("sources") or []
    changed = False
    for s in sources:
        if s.get("slug") == paper_slug and s.get("concept_slug") != concept_slug:
            s["concept_slug"] = concept_slug
            changed = True
    if not changed:
        return False
    frontmatter["sources"] = sources
    user_section = _extract_user_section(path)
    _write_node_file(path, frontmatter, user_section)
    return True


def _create_node(
    store_root: str,
    node_type: str,
    label: str,
    aliases: list[str],
    source_slug: str | None,
    source_title: str | None,
    category: str | None,
    description: str = "",
    note: str = "",
    anchor_id: str | None = None,
    concept_slug: str | None = None,
) -> str:
    slug = _slugify(label)
    path = _node_dir(store_root, node_type) / f"{slug}.md"
    frontmatter = {
        "slug": slug,
        "type": node_type,
        "display_label": label,
        "aliases": aliases,
        "description": description,
        "note": note,
        "sources": (
            [{"slug": source_slug, "title": source_title, "concept_slug": concept_slug}] if source_slug else []
        ),
        "anchor_id": anchor_id,
        "created_at": _now_iso(),
    }
    if node_type == "concept":
        frontmatter["category"] = category
    _write_node_file(path, frontmatter, user_section="")
    return slug


def link_node_to_paper(
    store_root: str, node_type: str, node_slug: str, paper_slug: str, paper_title: str,
    concept_slug: str | None = None,
) -> None:
    """이미 존재하는(orphan이든 아니든) concept/entity 노드 파일의 sources[]에 논문을
    하나 추가한다. _update_node()가 하는 일 중 sources 갱신 부분만 떼어낸 것과 같다 -
    라벨/alias 재매칭은 하지 않는다(호출부가 이미 정확히 어느 노드인지 slug로 알고
    있으므로 다시 퍼지 매칭할 이유가 없다). 그래프에서 노드를 다른 노드로 드래그해
    연결하는 제스처가 이 함수를 쓴다.

    concept_slug는 entity를 concept으로 드래그해 연결하는 경우에만 쓴다 - 이
    논문에서는 그 concept 밑에 묶인다는 걸 sources 항목에 기록한다."""
    path = _node_dir(store_root, node_type) / f"{node_slug}.md"
    if not path.is_file():
        raise FileNotFoundError(f"노드 파일을 찾을 수 없습니다: {node_slug}")
    frontmatter = _read_frontmatter(path)
    sources = frontmatter.get("sources") or []
    if not any(s.get("slug") == paper_slug for s in sources):
        sources.append({"slug": paper_slug, "title": paper_title, "concept_slug": concept_slug})
    frontmatter["sources"] = sources
    # 실제로 논문에 연결됐으니 이제 orphan이 아니다 - 그래프가 더 이상 anchor_id를
    # 안 쓰긴 하지만(sources가 있으면 무시함), frontmatter에 죽은 값으로 계속
    # 남겨두지 않기 위해 지운다.
    frontmatter["anchor_id"] = None
    user_section = _extract_user_section(path)
    _write_node_file(path, frontmatter, user_section)


def add_alias(store_root: str, node_type: str, slug: str, alias: str) -> list[str]:
    """사용자가 노드 화면에서 직접 별칭을 추가한다. LLM이 판단한 별칭이 항상
    정확하다는 보장은 없으므로(놓친 표기가 있거나, 반대로 실제로는 다른 개념인데
    같다고 오판했을 수도 있음), 사용자가 직접 보완/수정할 수 있게 한다. 별칭을
    추가해두면 다음부터 그 표기로 들어오는 라벨도 find_node_fuzzy()의 O(1) 인덱스
    조회로 바로 이 노드에 연결된다.

    이미 다른 노드가 같은(정규화 기준) display_label/별칭을 쓰고 있으면 막는다 -
    그대로 추가하면 인덱스에서 이 표기의 "주인"이 둘이 되어(먼저 등록된 쪽이
    이기지만 어느 쪽이 먼저인지 사용자는 알 수 없음), Multi-head Latent
    Attention/MLA 사이에서 실제로 겪었던 것과 같은 혼란이 다시 생긴다. 두 노드가
    정말 같은 개념이면 별칭 추가가 아니라 병합(execute_merge)을 써야 한다.
    갱신된 aliases 목록을 반환한다."""
    alias = alias.strip()
    if not alias:
        raise ValueError("별칭을 입력하세요.")

    path = _node_dir(store_root, node_type) / f"{slug}.md"
    frontmatter = _read_frontmatter(path)
    if not frontmatter.get("slug"):
        raise FileNotFoundError(f"노드 파일을 찾을 수 없습니다: {slug}")

    key = normalize_label(alias)
    if normalize_label(frontmatter["display_label"]) == key:
        raise ValueError("이미 이 노드의 표시 이름과 같습니다.")

    owner = node_index(store_root, node_type).get(key)
    if owner is not None and owner["slug"] != slug:
        raise DuplicateNodeError(owner)

    aliases = frontmatter.get("aliases") or []
    if not any(normalize_label(a) == key for a in aliases):
        aliases.append(alias)
    frontmatter["aliases"] = aliases
    user_section = _extract_user_section(path)
    _write_node_file(path, frontmatter, user_section)
    return aliases


def remove_alias(store_root: str, node_type: str, slug: str, alias: str) -> list[str]:
    """사용자가 노드 화면에서 직접 별칭을 지운다 - LLM이 잘못 판단해서 붙인 별칭
    (실제로는 다른 개념인데 같다고 오판한 경우 등)을 사용자가 바로잡을 수 있게
    한다. 갱신된 aliases 목록을 반환한다."""
    path = _node_dir(store_root, node_type) / f"{slug}.md"
    frontmatter = _read_frontmatter(path)
    if not frontmatter.get("slug"):
        raise FileNotFoundError(f"노드 파일을 찾을 수 없습니다: {slug}")

    key = normalize_label(alias)
    aliases = [a for a in (frontmatter.get("aliases") or []) if normalize_label(a) != key]
    frontmatter["aliases"] = aliases
    user_section = _extract_user_section(path)
    _write_node_file(path, frontmatter, user_section)
    return aliases


def _update_node(
    path: Path, label: str, aliases: list[str], source_slug: str, source_title: str,
    concept_slug: str | None = None,
) -> None:
    frontmatter = _read_frontmatter(path)

    # display_label은 최초 생성 시 표기로 고정한다("나중 표기가 항상 더 낫다"는
    # 보장이 없다 - OCR 품질 등으로 오히려 나중 논문이 더 못생긴 표기를 줄 수도
    # 있다). slug와 마찬가지로 노드 정체성은 최초 생성 시 확정, 이후 변경 없음.
    existing_aliases = set(frontmatter.get("aliases") or [])
    existing_aliases.update(a for a in aliases if a)
    frontmatter["aliases"] = sorted(existing_aliases)

    sources = frontmatter.get("sources") or []
    # 같은 논문이 이미 sources에 있으면(재처리 등) 다시 추가하지 않는다 - 재처리
    # 시에는 remove_source()가 먼저 옛 참조를 지우므로, "같은 논문인데 concept_slug만
    # 다르게 다시 들어오는" 충돌은 실제로 발생하지 않는다.
    if not any(s["slug"] == source_slug for s in sources):
        sources.append({"slug": source_slug, "title": source_title, "concept_slug": concept_slug})
    frontmatter["sources"] = sources

    user_section = _extract_user_section(path)
    _write_node_file(path, frontmatter, user_section)
