"""저장된 논문 패널을 옵시디언 파일 탐색기처럼 폴더로 묶어 보여주기 위한 가상
폴더 저장소. 실제 vault의 논문 파일 위치(AutoNote/<slug>/<slug>.md)는 전혀
건드리지 않고, "이 논문 slug는 이 폴더에 속한다"는 관계만 이 프로젝트 폴더 아래
(node_store.py의 _concepts/_entities와 같은 자리)에 로컬 JSON으로 따로
기록한다 - 나중에 논문 파일 자체도 node_store처럼 로컬로 옮기게 되면 이 폴더
개념을 그 구조에 자연스럽게 합칠 수 있다.

한 논문은 한 번에 최대 하나의 폴더에만 속한다(실제 파일 탐색기에서 파일을 한
폴더로 옮기면 원래 있던 폴더에서는 사라지는 것과 같음) - set_paper_folder()가
이 불변식을 유지한다.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path


def _folders_path(store_root: str) -> Path:
    folder = Path(store_root) / "config"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "_paper_folders.json"


def _load(store_root: str) -> list[dict]:
    path = _folders_path(store_root)
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []


def _save(store_root: str, folders: list[dict]) -> None:
    _folders_path(store_root).write_text(
        json.dumps(folders, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def list_folders(store_root: str) -> list[dict]:
    """생성 순서대로 모든 폴더를 반환한다. 각 폴더는 {id, name, paper_slugs, created_at}."""
    return sorted(_load(store_root), key=lambda f: f.get("created_at", ""))


def create_folder(store_root: str, name: str) -> dict:
    name = name.strip()
    if not name:
        raise ValueError("폴더 이름을 입력하세요.")

    folders = _load(store_root)
    if any(f["name"].strip().lower() == name.lower() for f in folders):
        raise ValueError(f"'{name}' 폴더가 이미 있습니다.")

    folder = {
        "id": uuid.uuid4().hex[:10],
        "name": name,
        "paper_slugs": [],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    folders.append(folder)
    _save(store_root, folders)
    return folder


def delete_folder(store_root: str, folder_id: str) -> None:
    """폴더만 지운다 - 그 안에 있던 논문 자체(vault 파일)는 그대로 두고, 다음부터
    "폴더 없음" 상태로 목록에 다시 나타난다."""
    folders = _load(store_root)
    remaining = [f for f in folders if f["id"] != folder_id]
    if len(remaining) == len(folders):
        raise FileNotFoundError(f"폴더를 찾을 수 없습니다: {folder_id}")
    _save(store_root, remaining)


def set_paper_folder(store_root: str, paper_slug: str, folder_id: str | None) -> None:
    """paper_slug를 folder_id 폴더 하나에만 속하게 한다. 다른 폴더에 이미
    있었다면 거기서는 빠진다. folder_id=None이면 어느 폴더에도 속하지 않는
    상태(목록 맨 위 "폴더 없음" 영역)가 된다."""
    folders = _load(store_root)

    target = None
    for f in folders:
        if folder_id is not None and f["id"] == folder_id:
            target = f
        if paper_slug in f["paper_slugs"]:
            f["paper_slugs"].remove(paper_slug)

    if folder_id is not None and target is None:
        raise FileNotFoundError(f"폴더를 찾을 수 없습니다: {folder_id}")

    if target is not None:
        target["paper_slugs"].append(paper_slug)

    _save(store_root, folders)
