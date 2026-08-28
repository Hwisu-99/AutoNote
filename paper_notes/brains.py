"""Brain: Folder보다 한 단계 위의 컨테이너.

사용자는 Brain을 여러 개 만들 수 있고, 그 안에 Folder 전체를 넣거나 폴더 없이
개별 논문을 직접 넣을 수 있다. Folder가 "논문 하나는 폴더 하나에만" 속한다는
불변식을 지키는 것과 똑같이, Folder도 Brain 하나에만 속한다(paper_folders.py의
set_folder_brain 참고) - 계층은 Brain -> Folder -> Paper, 또는 Folder 없이
Brain -> Paper.

이 파일은 컨테이너 자체(무엇이 어디 속하는지)만 로컬 JSON으로 관리한다. Brain이
실제로 LLM(GraphRAG)에 넘길 Graph의 범위를 어떻게 좁히는지는 graph_db.py를
참고 - 거기서 이 모듈의 get_paper_brain_id()를 불러 각 Paper 노드에
brain_id를 태그하고, concept/entity 노드에는 "그 노드가 걸려 있는 논문들이
지금 속한 Brain 목록"을 brain_ids로 계산해 저장한다.

concept/entity 자체는 여러 논문에 걸쳐 공유되는 게 기본 설계라(하나의 개념이
서로 다른 Brain의 논문에 동시에 등장할 수 있음), Brain을 concept/entity 파일에
직접 저장하지 않는다 - 그 개념이 어느 Brain(들)에서 보이는지는 항상 "지금 그
개념이 딸린 논문들이 어느 Brain에 있는가"로 다시 계산되는 값이다. 그래서 같은
개념이 두 Brain에서 동시에(각자의 하위 집합만 보이는 채로) 보일 수 있고, 파일
자체는 하나만 존재해 데이터가 중복되지 않는다.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from paper_notes.paper_folders import (
    list_folders,
    remove_paper_from_all_folders,
    set_folder_brain,
)


def _brains_path(store_root: str) -> Path:
    folder = Path(store_root) / "config"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "_brains.json"


def _load(store_root: str) -> list[dict]:
    path = _brains_path(store_root)
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []


def _save(store_root: str, brains: list[dict]) -> None:
    _brains_path(store_root).write_text(
        json.dumps(brains, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def list_brains(store_root: str) -> list[dict]:
    """생성 순서대로 모든 Brain을 반환한다. 각 Brain은 {id, name, paper_slugs,
    created_at} - paper_slugs는 "폴더 없이 이 Brain에 직접 속한 논문"만 담는다
    (Folder를 통해 속한 논문은 그 Folder의 brain_id로 간접 결정되므로 여기
    중복 저장하지 않는다 - get_paper_brain_id() 참고)."""
    return sorted(_load(store_root), key=lambda b: b.get("created_at", ""))


def create_brain(store_root: str, name: str) -> dict:
    name = name.strip()
    if not name:
        raise ValueError("Brain 이름을 입력하세요.")
    brains = _load(store_root)
    if any(b["name"].strip().lower() == name.lower() for b in brains):
        raise ValueError(f"'{name}' Brain이 이미 있습니다.")

    brain = {
        "id": uuid.uuid4().hex[:10],
        "name": name,
        "paper_slugs": [],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    brains.append(brain)
    _save(store_root, brains)
    return brain


def rename_brain(store_root: str, brain_id: str, new_name: str) -> dict:
    """Brain 이름만 바꾼다 - id는 그대로라 Neo4j의 brain_id 태그나 Folder의
    참조는 전혀 영향받지 않는다(이름은 표시용일 뿐 식별자가 아니다)."""
    new_name = new_name.strip()
    if not new_name:
        raise ValueError("Brain 이름을 입력하세요.")
    brains = _load(store_root)
    target = next((b for b in brains if b["id"] == brain_id), None)
    if target is None:
        raise FileNotFoundError(f"Brain을 찾을 수 없습니다: {brain_id}")
    if any(b["id"] != brain_id and b["name"].strip().lower() == new_name.lower() for b in brains):
        raise ValueError(f"'{new_name}' Brain이 이미 있습니다.")
    target["name"] = new_name
    _save(store_root, brains)
    return target


def delete_brain(store_root: str, brain_id: str) -> dict:
    """Brain 레코드만 지운다 - 그 안의 Folder/논문 자체는 그대로 두고, 전부
    "브레인 없음" 상태로 돌아간다(Folder는 clear_brain_from_folders로 brain_id가
    지워지고, 이 Brain에 직접 속해 있던 논문은 이 레코드가 사라지면서 자동으로
    브레인 없음이 된다). 영향받은 폴더 id와, 브레인 없음으로 돌아간 직속 논문
    slug 목록을 반환한다 - app.py가 이걸로 Neo4j 재동기화 대상을 안다."""
    brains = _load(store_root)
    target = next((b for b in brains if b["id"] == brain_id), None)
    if target is None:
        raise FileNotFoundError(f"Brain을 찾을 수 없습니다: {brain_id}")

    from paper_notes.paper_folders import clear_brain_from_folders

    affected_folder_ids = clear_brain_from_folders(store_root, brain_id)
    direct_paper_slugs = list(target.get("paper_slugs", []))

    remaining = [b for b in brains if b["id"] != brain_id]
    _save(store_root, remaining)

    return {"affected_folder_ids": affected_folder_ids, "direct_paper_slugs": direct_paper_slugs}


def set_paper_brain(store_root: str, paper_slug: str, brain_id: str | None) -> None:
    """paper_slug를 (폴더를 거치지 않고) brain_id Brain에 직접 속하게 한다.
    이미 어느 Folder 안에 있었다면 거기서 빼낸다 - 파일 탐색기에서 파일을
    폴더 밖으로 드래그해 상위 컨테이너에 바로 놓는 것과 같다. brain_id=None이면
    완전히 "브레인 없음" 상태가 된다."""
    remove_paper_from_all_folders(store_root, paper_slug)

    brains = _load(store_root)
    target = None
    for b in brains:
        if brain_id is not None and b["id"] == brain_id:
            target = b
        if paper_slug in b.get("paper_slugs", []):
            b["paper_slugs"].remove(paper_slug)

    if brain_id is not None and target is None:
        raise FileNotFoundError(f"Brain을 찾을 수 없습니다: {brain_id}")

    if target is not None:
        target["paper_slugs"].append(paper_slug)

    _save(store_root, brains)


def get_paper_brain_id(store_root: str, paper_slug: str) -> str | None:
    """이 논문이 지금 어느 Brain에 속하는지 계산한다: 어느 Folder 안에 있으면
    그 Folder의 brain_id, 폴더 없이 Brain에 직접 속해 있으면 그 Brain, 둘 다
    아니면 None("브레인 없음"). graph_db.sync_paper()/sync_node()가 Neo4j에
    brain_id/brain_ids를 태그할 때 이 함수로 매번 다시 계산한다 - 별도로
    캐싱하지 않는 이유는 Folder/Brain 소속이 바뀔 때마다 캐시 무효화까지
    신경 쓰는 것보다, 로컬 JSON 두 개를 읽는 비용이 훨씬 싸기 때문이다."""
    for f in list_folders(store_root):
        if paper_slug in f.get("paper_slugs", []):
            return f.get("brain_id")
    for b in list_brains(store_root):
        if paper_slug in b.get("paper_slugs", []):
            return b["id"]
    return None


def merge_brains(store_root: str, survivor_id: str, loser_id: str) -> dict:
    """loser Brain을 survivor Brain으로 흡수한다: loser에 직접 속한 논문과
    loser를 가리키던 모든 Folder를 survivor로 옮긴 뒤 loser Brain 레코드를
    지운다(Brain Consolidation의 "컨테이너 병합" 부분).

    Folder 이름이 두 Brain에서 우연히 겹쳐도(각자 자기 Brain 안에서만 유일하면
    되므로 있을 수 있는 상황) 여기서 자동으로 합치지 않는다 - 서로 다른 id를
    유지한 채 같은 Brain 아래 나란히 남고, 필요하면 사용자가 나중에 수동으로
    정리한다.

    concept/entity 수준의 중복(같은 개념이 양쪽 Brain에 따로 노드로 있던 경우)도
    여기서 처리하지 않는다 - 그건 논문 단위가 아니라 개념 단위 판단이라
    node_store.py의 기존 병합 후보 큐(_merge_candidates.json)와
    execute_merge()를 그대로 쓴다. 이 함수는 그 판단에 넘길 후보 자체를 줄이는
    앞단계(같은 Brain 아래 papers를 모아 dedup.py가 비교할 대상 범위를 정하는
    것)만 담당한다.

    반환값: {"survivor_id", "moved_folder_ids", "moved_paper_slugs"} - app.py가
    이걸로 Neo4j에서 어떤 Paper/Concept/Entity를 재동기화해야 하는지 안다."""
    if survivor_id == loser_id:
        raise ValueError("같은 Brain끼리는 합칠 수 없습니다.")

    brains = _load(store_root)
    survivor = next((b for b in brains if b["id"] == survivor_id), None)
    loser = next((b for b in brains if b["id"] == loser_id), None)
    if survivor is None:
        raise FileNotFoundError(f"Brain을 찾을 수 없습니다: {survivor_id}")
    if loser is None:
        raise FileNotFoundError(f"Brain을 찾을 수 없습니다: {loser_id}")

    moved_paper_slugs = list(loser.get("paper_slugs", []))
    survivor["paper_slugs"] = sorted(set(survivor.get("paper_slugs", [])) | set(moved_paper_slugs))

    moved_folder_ids = [f["id"] for f in list_folders(store_root) if f.get("brain_id") == loser_id]
    for folder_id in moved_folder_ids:
        set_folder_brain(store_root, folder_id, survivor_id)

    remaining = [b for b in brains if b["id"] != loser_id]
    _save(store_root, remaining)

    return {
        "survivor_id": survivor_id,
        "moved_folder_ids": moved_folder_ids,
        "moved_paper_slugs": moved_paper_slugs,
    }
