"""paper_notes.node_store(concept/entity 노드 파일 생성·갱신·병합 후보 감지)가
정상인지 확인하는 스탠드얼론 스크립트. 외부 서비스 호출 없이 순수 파일 I/O만 검증한다.

사용법:
    python test_node_store.py
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from paper_notes.node_store import (
    execute_merge,
    list_merge_candidates,
    reject_merge_candidate,
    resolve_or_create_node,
)

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}" + (f" - {detail}" if detail and not condition else ""))
    if not condition:
        FAILURES.append(name)


def test_creates_new_node_on_first_mention() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        slug = resolve_or_create_node(tmp, "concept", "Self-Attention", [], "paper-a", "Paper A", category="process")
        path = Path(tmp) / "_concepts" / "self-attention.md"

        check("새 concept 첫 등장 시 노드 파일 생성", path.is_file())
        check("반환된 slug가 파일명과 일치", slug == "self-attention")

        text = path.read_text(encoding="utf-8")
        check("frontmatter에 display_label 포함", 'display_label: Self-Attention' in text)
        check("frontmatter에 category 포함", "category: process" in text)
        check("본문에 출처 논문 링크 포함", "[[paper-a|Paper A]]" in text)


def test_exact_label_match_updates_same_node() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        slug1 = resolve_or_create_node(tmp, "concept", "Self-Attention", [], "paper-a", "Paper A")
        slug2 = resolve_or_create_node(tmp, "concept", "self attention", [], "paper-b", "Paper B")

        concepts_dir = Path(tmp) / "_concepts"
        check("대소문자/공백만 다른 라벨은 같은 노드로 판정", slug1 == slug2)
        check("새 파일이 추가로 생기지 않음", len(list(concepts_dir.glob("*.md"))) == 1)

        text = (concepts_dir / f"{slug1}.md").read_text(encoding="utf-8")
        check("두 출처 논문이 모두 기록됨", "paper-a" in text and "paper-b" in text)


def test_alias_bridges_two_existing_nodes_as_candidate_not_merge() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        slug_a = resolve_or_create_node(tmp, "concept", "MoE", [], "paper-a", "Paper A")
        slug_b = resolve_or_create_node(tmp, "concept", "Mixture-of-Experts Layer", [], "paper-b", "Paper B")

        check("서로 다른 표기는 alias 없이 별개 노드로 생성됨", slug_a != slug_b)

        primary_slug = resolve_or_create_node(
            tmp, "concept", "MoE", ["Mixture-of-Experts Layer"], "paper-c", "Paper C"
        )
        check("bridge 발생 시 더 먼저 생성된 노드가 대표로 반환됨", primary_slug == slug_a)

        concepts_dir = Path(tmp) / "_concepts"
        check(
            "두 노드 파일이 즉시 병합되지 않고 그대로 남아있음(승인 전까지 보류)",
            len(list(concepts_dir.glob("*.md"))) == 2,
        )

        candidates_path = Path(tmp) / "config" / "_merge_candidates.json"
        check("병합 후보가 _merge_candidates.json에 기록됨", candidates_path.is_file())
        if candidates_path.is_file():
            candidates = json.loads(candidates_path.read_text(encoding="utf-8"))
            check(
                "기록된 후보가 올바른 두 slug를 가리킴",
                len(candidates) == 1
                and {candidates[0]["slug_a"], candidates[0]["slug_b"]} == {slug_a, slug_b},
                str(candidates),
            )


def test_user_notes_section_preserved_across_updates() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        slug = resolve_or_create_node(tmp, "concept", "Self-Attention", [], "paper-a", "Paper A")
        path = Path(tmp) / "_concepts" / f"{slug}.md"

        text = path.read_text(encoding="utf-8")
        marker = "user-notes"
        idx = text.index(marker)
        end_of_marker_line = text.index("\n", idx) + 1
        text_with_note = text[:end_of_marker_line] + "이건 내가 직접 쓴 메모다.\n"
        path.write_text(text_with_note, encoding="utf-8")

        resolve_or_create_node(tmp, "concept", "Self-Attention", [], "paper-b", "Paper B")

        updated_text = path.read_text(encoding="utf-8")
        check("갱신 후에도 사용자가 작성한 메모가 그대로 남아있음", "이건 내가 직접 쓴 메모다." in updated_text)
        check("갱신 후 새 출처도 함께 반영됨", "paper-b" in updated_text)


def test_display_label_fixed_at_first_creation() -> None:
    """알려진 설계 결정: 나중 논문이 제공하는 표기가 항상 더 낫다는 보장이 없으므로
    (예: OCR 품질 차이), display_label은 slug처럼 최초 생성 시로 고정하고 이후
    절대 덮어쓰지 않는다."""
    with tempfile.TemporaryDirectory() as tmp:
        resolve_or_create_node(tmp, "concept", "Self-Attention", [], "paper-a", "Paper A")
        resolve_or_create_node(tmp, "concept", "self attention", [], "paper-b", "Paper B")

        path = Path(tmp) / "_concepts" / "self-attention.md"
        text = path.read_text(encoding="utf-8")
        check(
            "나중 논문이 다른 표기를 줘도 display_label은 최초 생성 시 표기 그대로",
            "display_label: Self-Attention" in text,
            text,
        )


def _bridge_moe_candidate(tmp: str) -> tuple[str, str]:
    slug_a = resolve_or_create_node(tmp, "concept", "MoE", [], "paper-a", "Paper A")
    slug_b = resolve_or_create_node(tmp, "concept", "Mixture-of-Experts Layer", [], "paper-b", "Paper B")
    resolve_or_create_node(tmp, "concept", "MoE", ["Mixture-of-Experts Layer"], "paper-c", "Paper C")
    return slug_a, slug_b


def test_execute_merge_keeps_earlier_node_and_stubs_the_other() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        slug_a, slug_b = _bridge_moe_candidate(tmp)
        survivor = execute_merge(tmp, "concept", slug_a, slug_b)

        check("먼저 생성된 노드(MoE)가 생존자", survivor == slug_a)

        concepts_dir = Path(tmp) / "_concepts"
        survivor_text = (concepts_dir / f"{slug_a}.md").read_text(encoding="utf-8")
        stub_text = (concepts_dir / f"{slug_b}.md").read_text(encoding="utf-8")

        check("생존 노드 aliases에 패자 라벨이 흡수됨", "Mixture-of-Experts Layer" in survivor_text)
        check("생존 노드 sources에 두 논문 다 포함", "paper-a" in survivor_text and "paper-b" in survivor_text)
        check("패자 파일은 삭제되지 않고 redirect 스텁으로 남음", "redirect_to: " + slug_a in stub_text)


def test_execute_merge_preserves_both_user_notes() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        slug_a, slug_b = _bridge_moe_candidate(tmp)
        concepts_dir = Path(tmp) / "_concepts"

        for slug, note in [(slug_a, "A에 쓴 메모"), (slug_b, "B에 쓴 메모")]:
            path = concepts_dir / f"{slug}.md"
            text = path.read_text(encoding="utf-8")
            idx = text.index("\n", text.index("user-notes")) + 1
            path.write_text(text[:idx] + note + "\n", encoding="utf-8")

        execute_merge(tmp, "concept", slug_a, slug_b)
        survivor_text = (concepts_dir / f"{slug_a}.md").read_text(encoding="utf-8")
        check("병합 후에도 두 노드의 메모가 모두 보존됨", "A에 쓴 메모" in survivor_text and "B에 쓴 메모" in survivor_text)


def test_execute_merge_marks_candidate_as_merged() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        slug_a, slug_b = _bridge_moe_candidate(tmp)
        execute_merge(tmp, "concept", slug_a, slug_b)

        check("병합 후 pending 후보 목록에서 사라짐", list_merge_candidates(tmp, status="pending") == [])
        merged = list_merge_candidates(tmp, status="merged")
        check("병합 이력이 merged 상태로 남음", len(merged) == 1, str(merged))


def test_reject_merge_candidate_does_not_resurface() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        slug_a, slug_b = _bridge_moe_candidate(tmp)
        reject_merge_candidate(tmp, "concept", slug_a, slug_b)
        check("거부 후 pending 목록에서 사라짐", list_merge_candidates(tmp, status="pending") == [])

        # 같은 alias를 제공하는 논문이 또 들어와도 거부된 후보가 재등록되지 않아야 함
        resolve_or_create_node(tmp, "concept", "MoE", ["Mixture-of-Experts Layer"], "paper-d", "Paper D")
        check(
            "같은 alias가 다시 감지돼도 거부된 후보는 재등록되지 않음",
            list_merge_candidates(tmp, status="pending") == [],
        )


def test_redirect_stub_excluded_from_future_matching() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        slug_a, slug_b = _bridge_moe_candidate(tmp)
        execute_merge(tmp, "concept", slug_a, slug_b)

        # 병합 후 patper-e가 패자였던 라벨을 다시 언급해도 새 노드가 생기지 않고
        # 생존 노드로 바로 연결돼야 한다(흡수된 alias 덕분).
        resolved = resolve_or_create_node(tmp, "concept", "Mixture-of-Experts Layer", [], "paper-e", "Paper E")
        check("병합된 라벨을 다시 언급해도 생존 노드로 연결됨", resolved == slug_a)

        concepts_dir = Path(tmp) / "_concepts"
        check("redirect 스텁으로 인해 새 노드가 잘못 생성되지 않음", len(list(concepts_dir.glob("*.md"))) == 2)


def test_entity_node_has_no_category_field() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        slug = resolve_or_create_node(tmp, "entity", "Softmax", [], "paper-a", "Paper A")
        path = Path(tmp) / "_entities" / f"{slug}.md"
        text = path.read_text(encoding="utf-8")
        check("entity 노드는 concepts 폴더가 아닌 entities 폴더에 생성됨", path.is_file())
        check("entity 노드 frontmatter에는 category 필드가 없음", "category:" not in text)


def main() -> None:
    test_creates_new_node_on_first_mention()
    test_exact_label_match_updates_same_node()
    test_alias_bridges_two_existing_nodes_as_candidate_not_merge()
    test_user_notes_section_preserved_across_updates()
    test_display_label_fixed_at_first_creation()
    test_execute_merge_keeps_earlier_node_and_stubs_the_other()
    test_execute_merge_preserves_both_user_notes()
    test_execute_merge_marks_candidate_as_merged()
    test_reject_merge_candidate_does_not_resurface()
    test_redirect_stub_excluded_from_future_matching()
    test_entity_node_has_no_category_field()

    print()
    if FAILURES:
        print(f"{len(FAILURES)}개 실패: {FAILURES}")
        raise SystemExit(1)
    print("전체 통과.")


if __name__ == "__main__":
    main()
