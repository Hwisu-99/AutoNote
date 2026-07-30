from __future__ import annotations

import os

import certifi
from supabase import Client, create_client

_client: Client | None = None


def _ensure_valid_ssl_cert_file() -> None:
    """SSL_CERT_FILE이 존재하지 않는 경로를 가리키면(예: 삭제된 miniconda 설치 흔적)
    httpx의 SSL 컨텍스트 생성 자체가 FileNotFoundError로 죽는다. 그 경우 certifi의
    번들로 교체해 우회한다."""
    cert_file = os.environ.get("SSL_CERT_FILE")
    if not cert_file or not os.path.isfile(cert_file):
        os.environ["SSL_CERT_FILE"] = certifi.where()


def _get_client() -> Client:
    """SUPABASE_URL/SUPABASE_KEY로 클라이언트를 만들고 캐싱한다. 설정이 없으면 예외를 던진다."""
    global _client
    if _client is not None:
        return _client

    _ensure_valid_ssl_cert_file()

    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key:
        raise RuntimeError(
            "SUPABASE_URL / SUPABASE_KEY가 설정되어 있지 않습니다. .env를 확인하세요."
        )

    _client = create_client(url, key)
    return _client


def upload_note(note_path: str, title_slug: str) -> str:
    """생성된 마크다운 노트를 Supabase Storage 버킷에 업로드하고, 버킷 내 경로를 반환한다."""
    bucket_name = os.getenv("SUPABASE_BUCKET", "autonote-notes")
    storage_path = f"{title_slug}/{title_slug}.md"

    with open(note_path, "rb") as f:
        content = f.read()

    client = _get_client()
    client.storage.from_(bucket_name).upload(
        storage_path,
        content,
        file_options={"content-type": "text/markdown", "upsert": "true"},
    )

    return storage_path


def upload_summary(json_path: str, title_slug: str) -> str:
    """생성된 처리 결과 요약 JSON을 Supabase Storage 버킷에 업로드하고, 버킷 내 경로를 반환한다."""
    bucket_name = os.getenv("SUPABASE_BUCKET", "autonote-notes")
    storage_path = f"{title_slug}/{title_slug}.summary.json"

    with open(json_path, "rb") as f:
        content = f.read()

    client = _get_client()
    client.storage.from_(bucket_name).upload(
        storage_path,
        content,
        file_options={"content-type": "application/json", "upsert": "true"},
    )

    return storage_path


def delete_note(title_slug: str) -> None:
    """Supabase Storage에서 해당 논문의 노트 파일과 요약 JSON을 삭제한다."""
    bucket_name = os.getenv("SUPABASE_BUCKET", "autonote-notes")
    client = _get_client()
    client.storage.from_(bucket_name).remove(
        [f"{title_slug}/{title_slug}.md", f"{title_slug}/{title_slug}.summary.json"]
    )


def list_papers() -> list[str]:
    """Supabase Storage 버킷 루트의 폴더 목록(= 업로드된 논문 slug 목록)을 반환한다."""
    bucket_name = os.getenv("SUPABASE_BUCKET", "autonote-notes")
    client = _get_client()
    entries = client.storage.from_(bucket_name).list()

    slugs = []
    for entry in entries:
        name = entry.get("name")
        # 버킷 루트에 파일이 아니라 폴더(= 논문)만 남기고, 내부 테스트용 폴더는 제외한다.
        if not name or name.startswith("_") or entry.get("id") is not None:
            continue
        slugs.append(name)

    return sorted(slugs)
