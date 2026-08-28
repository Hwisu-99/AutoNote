from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import yaml
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from paper_notes.claude_client import summarize_paper
from paper_notes.extractor import extract_text
from paper_notes.graph_builder import build_graph
from paper_notes.node_store import (
    IMAGE_EXTENSIONS,
    NODE_STORE_ROOT,
    DuplicateNodeError,
    add_alias,
    add_category,
    create_node_manual,
    delete_node,
    find_entities_by_concept,
    find_node_fuzzy,
    find_node_slugs_by_paper,
    get_auto_section,
    get_user_section,
    link_node_to_paper,
    list_nodes,
    node_index,
    remove_alias,
    remove_category,
    remove_source,
    remove_source_from_node,
    rename_display_label,
    resolve_or_create_node,
    save_attachment,
    unlink_concept_from_entity,
    update_user_section,
)
from paper_notes.brains import (
    create_brain,
    delete_brain,
    get_paper_brain_id,
    list_brains,
    merge_brains,
    rename_brain,
    set_paper_brain,
)
from paper_notes.graph_db import Neo4jNotConfigured
from paper_notes.graph_db import delete_node_from_graph as _gdb_delete_node
from paper_notes.graph_db import retag_node_brain as _gdb_retag_node_brain
from paper_notes.graph_db import retag_paper_brain as _gdb_retag_paper_brain
from paper_notes.graph_db import search as graph_db_search
from paper_notes.graph_db import sync_node as _gdb_sync_node
from paper_notes.graph_db import sync_paper as _gdb_sync_paper
from paper_notes.obsidian_writer import delete_note as delete_local_note
from paper_notes.obsidian_writer import write_note, write_summary_json
from paper_notes.paper_folders import (
    create_folder,
    delete_folder,
    list_folders,
    set_folder_brain,
    set_paper_folder,
)
from paper_notes.supabase_writer import delete_note as delete_remote_note
from paper_notes.supabase_writer import list_papers, upload_note, upload_summary
from paper_notes.utils import slugify

load_dotenv()

app = FastAPI(title="AutoNote Paper Summarizer")


def get_vault_path() -> str:
    vault_path = os.getenv("OBSIDIAN_VAULT_PATH")
    if not vault_path or not Path(vault_path).is_dir():
        raise HTTPException(
            status_code=500,
            detail="OBSIDIAN_VAULT_PATH가 설정되어 있지 않거나 존재하지 않는 폴더입니다. .env를 확인하세요.",
        )
    return vault_path


# node_store(.md 파일)를 바꾸는 모든 엔드포인트가, 성공하면 그 변경을 Neo4j
# 미러(paper_notes/graph_db.py)에도 반영한다 - GraphRAG 검색(MCP)이 항상 최신
# 상태를 보게 하기 위함. Neo4j가 아직 설정 안 됐거나(.env 미기입) 일시적으로
# 응답이 없어도 이 동기화 실패가 방금 성공한 실제 변경(node_store)까지 되돌리거나
# 사용자에게 에러로 보여선 안 되므로, 항상 조용히 삼키고 로그만 남긴다(Supabase
# 업로드 실패를 처리하는 기존 패턴과 같다).
def _sync_node(node_type: str, slug: str) -> None:
    try:
        _gdb_sync_node(node_type, slug)
    except Neo4jNotConfigured:
        pass
    except Exception as exc:  # noqa: BLE001
        print(f"  [경고] Neo4j 동기화 실패({node_type}:{slug}): {exc}")


def _sync_paper(slug: str, title: str, tags: list[str] | None = None) -> None:
    try:
        _gdb_sync_paper(slug, title, tags)
    except Neo4jNotConfigured:
        pass
    except Exception as exc:  # noqa: BLE001
        print(f"  [경고] Neo4j 논문 동기화 실패({slug}): {exc}")


# Brain 재동기화(논문 1개당 최대 1 + N번의 Neo4j 왕복 - 노드 개수만큼)는 원격
# Neo4j(Aura)라 왕복마다 네트워크 지연이 붙어서, 응답 안에서 기다리면 Folder
# 배정(로컬 JSON만 씀)과 다르게 몇백 ms~1초 넘게 걸릴 수 있다. 그래서 아래
# Brain 엔드포인트들은 이 함수를 FastAPI BackgroundTasks로 돌린다 - HTTP
# 응답은 로컬 변경(진짜 저장소)이 끝나는 즉시 나가고, Neo4j 태깅은 응답을
# 보낸 뒤 이어서 실행된다. 그 대신 실패해도 더 이상 HTTP 에러로 알릴 방법이
# 없으므로(응답이 이미 나갔음), 마지막 실패 하나를 여기 기억해뒀다가
# /api/neo4j-sync-status로 조회할 수 있게 한다 - papers.js가 Brain 배정
# 액션 직후 잠깐 뒤에 이걸 확인해서 사용자에게 보여준다(조용히 삼키던 이전
# 방식과 다르게, 실패를 실제로 드러낸다).
_last_brain_sync_error: dict | None = None


def _resync_paper_brain(paper_slug: str) -> None:
    """paper_slug의 Folder/Brain 소속이 바뀐 뒤, Neo4j의 Paper.brain_id와 그
    논문이 걸린 모든 concept/entity의 brain_ids를 다시 계산해 반영한다. 논문
    내용(title/tags)도, 그 concept/entity들의 설명·별칭·sources[]도 전혀 안
    바뀌었으므로(오직 "어느 Brain 소속인가"만 바뀜) 무거운 sync_paper()/
    sync_node()(임베딩 재계산 + 관계 전체 재생성) 대신 각각의 가벼운 버전인
    retag_paper_brain()/retag_node_brain()만 쓴다 - Brain 배정을 옮길 때마다
    체감 지연이 컸던 지점이라, 실제로 바뀐 속성(brain_id/brain_ids) 하나만
    SET하는 경로로 분리했다. 영향받는 concept/entity는
    node_store.find_node_slugs_by_paper()로 찾는다.

    항상 백그라운드 작업으로 스케줄돼서(호출부의 BackgroundTasks.add_task 참고)
    HTTP 응답이 이미 나간 뒤에 실행된다."""
    global _last_brain_sync_error
    try:
        _gdb_retag_paper_brain(paper_slug)
        for node_type, node_slug in find_node_slugs_by_paper(NODE_STORE_ROOT, paper_slug):
            _gdb_retag_node_brain(node_type, node_slug)
    except Neo4jNotConfigured:
        pass
    except Exception as exc:  # noqa: BLE001
        message = f"Neo4j Brain 재태깅 실패({paper_slug}): {exc}"
        print(f"  [경고] {message}")
        _last_brain_sync_error = {"message": message, "at": datetime.now(timezone.utc).isoformat()}


def _delete_node_from_graph(node_type: str, slug: str) -> None:
    try:
        _gdb_delete_node(node_type, slug)
    except Neo4jNotConfigured:
        pass
    except Exception as exc:  # noqa: BLE001
        print(f"  [경고] Neo4j 노드 삭제 동기화 실패({node_type}:{slug}): {exc}")


def _event(stage: str, percent: int, message: str, **extra) -> str:
    return json.dumps({"stage": stage, "percent": percent, "message": message, **extra}, ensure_ascii=False) + "\n"


_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    try:
        frontmatter = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        frontmatter = {}
    return frontmatter, text[match.end() :]


# Obsidian wikilink syntax: [[target]], [[target|alias]], embeds !\[\[target]]
_WIKILINK_RE = re.compile(r"!?\[\[([^\]|#]+)(?:\|[^\]]+)?\]\]")


def _resolve_wikilinks(vault_path: str, body: str) -> dict[str, dict]:
    """본문의 [[wikilink]] 대상들을 note(vault) 또는 concept/entity(node_store)로
    풀어서 {원본 타깃 텍스트: {type, slug}}를 반환한다. 프론트가 이걸로 위키링크를
    실제로 클릭 가능하게 만들지(어디로 보낼지) 판단한다. node 파일 자신이 만드는
    "## 등장 논문" 링크는 대상이 이미 논문 slug라 바로 맞아떨어지고, 논문 본문의
    concept/entity 위키링크는 그 논문이 직접 뽑은 원본 라벨이라 node_store와
    퍼지 매칭(find_node_fuzzy)까지 거쳐야 한다 - 별도 alias 힌트 없이도 매칭되는데,
    resolve_or_create_node()가 논문을 처리할 때마다 그 논문이 준 alias를 이미
    node_store 파일 자신의 aliases에 누적해왔기 때문이다."""
    targets = set()
    for m in _WIKILINK_RE.finditer(body):
        target = m.group(1).strip()
        if target.lower().endswith(".excalidraw"):
            continue
        targets.add(target)
    if not targets:
        return {}

    concept_nodes = list_nodes(NODE_STORE_ROOT, "concept")
    entity_nodes = list_nodes(NODE_STORE_ROOT, "entity")
    concept_idx = node_index(NODE_STORE_ROOT, "concept")
    entity_idx = node_index(NODE_STORE_ROOT, "entity")

    links: dict[str, dict] = {}
    for target in targets:
        if (Path(vault_path) / "AutoNote" / target / f"{target}.md").is_file():
            links[target] = {"type": "note", "slug": target}
            continue
        concept_match = find_node_fuzzy(concept_nodes, target, index=concept_idx)
        if concept_match:
            links[target] = {"type": "concept", "slug": concept_match["slug"]}
            continue
        entity_match = find_node_fuzzy(entity_nodes, target, index=entity_idx)
        if entity_match:
            links[target] = {"type": "entity", "slug": entity_match["slug"]}
    return links


async def _summarize_cancellable(paper_text: str, request: Request) -> tuple[dict, float] | None:
    """summarize_paper를 실행하되, 클라이언트 연결이 끊기면 실제 Claude API 요청을
    취소하고 None을 반환한다 (진행 중이던 요청이 그대로 완료될 때까지 기다리지 않음)."""
    task = asyncio.ensure_future(summarize_paper(paper_text))

    while True:
        done, _ = await asyncio.wait({task}, timeout=0.5)
        if task in done:
            return task.result()
        if await request.is_disconnected():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            return None


async def run_pipeline(tmp_path: str, vault_path: str, request: Request, overwrite_slug: str | None = None):
    try:
        if await request.is_disconnected():
            return

        yield _event("extract", 10, "PDF에서 텍스트 추출 중...")
        paper_text = extract_text(tmp_path)
        if not paper_text.strip():
            yield _event("error", 100, "PDF에서 텍스트를 추출할 수 없습니다.")
            return
        text_hash = hashlib.sha256(paper_text.encode("utf-8")).hexdigest()

        if await request.is_disconnected():
            return

        yield _event("summarize", 30, "Claude로 논문 분석 중... (시간이 조금 걸립니다)")
        result = await _summarize_cancellable(paper_text, request)
        if result is None:
            print("  [취소됨] 사용자가 취소하여 Claude 요청을 중단했습니다.")
            return
        summary, api_cost_usd = result

        # overwrite_slug가 있으면(같은 논문을 재처리하는 경우) 새 제목으로 slug를 다시
        # 뽑지 않고 기존 slug를 그대로 재사용한다 - 같은 논문이어도 Claude가 매번 제목을
        # 조금씩 다르게 내서(대소문자, 부제 표기 등) slugify 결과가 매번 달라지면 매번
        # 새 폴더가 생겨버린다.
        title_slug = overwrite_slug or slugify(summary["title"])

        if await request.is_disconnected():
            return

        yield _event("nodes", 65, "concept/entity 노드 파일 갱신 중...")
        # concept/entity를 Neo4j에 동기화할 때 (Paper)-[:LINKED_TO]->(Concept|Entity)
        # 에지를 걸려면 그 시점에 Paper 노드가 이미 있어야 한다(graph_db.sync_node()의
        # MATCH (p:Paper {slug: ...})가 0건이면 그 아래 MERGE도 조용히 스킵되고 에러도
        # 안 남는다) - 그래서 concept/entity 루프보다 먼저 paper부터 동기화해야 한다.
        _sync_paper(title_slug, summary["title"], summary.get("tags", []))
        try:
            if overwrite_slug:
                for node_type, node_slug, was_deleted in remove_source(NODE_STORE_ROOT, overwrite_slug):
                    if was_deleted:
                        _delete_node_from_graph(node_type, node_slug)
                    else:
                        _sync_node(node_type, node_slug)
            # concept을 먼저 처리해 최종 slug를 모아둔다 - entity가 이 논문에서
            # 어느 concept 밑에 묶이는지는 Claude가 이 논문만 보고 낸 원본 라벨이
            # 아니라, 실제로 확정된(기존 노드에 병합됐을 수도 있는) concept의
            # slug를 가리켜야 한다.
            concept_slug_by_id: dict[str, str] = {}
            for c in summary.get("concepts", []):
                concept_slug_by_id[c["id"]] = resolve_or_create_node(
                    NODE_STORE_ROOT, "concept", c["label"], c.get("aliases", []),
                    title_slug, summary["title"], category=c.get("category"),
                    description=c.get("description", ""), note=c.get("note", ""),
                )
                _sync_node("concept", concept_slug_by_id[c["id"]])
            for e in summary.get("entities", []):
                entity_slug = resolve_or_create_node(
                    NODE_STORE_ROOT, "entity", e["label"], e.get("aliases", []),
                    title_slug, summary["title"],
                    description=e.get("description", ""), note=e.get("note", ""),
                    concept_slug=concept_slug_by_id.get(e.get("concept_id")) if e.get("concept_id") else None,
                )
                _sync_node("entity", entity_slug)
        except Exception as exc:  # noqa: BLE001 - 노드 파일 갱신 실패가 파이프라인 전체를 막지 않음
            print(f"  [경고] concept/entity 노드 파일 갱신 실패: {exc}")

        yield _event("note", 90, "Obsidian 노트 저장 중...")
        note_path = write_note(vault_path, summary, title_slug)

        focus_graph = build_graph(vault_path, title_slug, only_focus=True)
        node_summary: dict[str, list[str]] = {}
        for node in focus_graph["nodes"]:
            node_summary.setdefault(node["type"], []).append(node["label"])

        yield _event("upload", 95, "Supabase Storage에 노트 업로드 중...")
        supabase_path: str | None = None
        supabase_error: str | None = None
        try:
            supabase_path = upload_note(note_path, title_slug)
        except Exception as exc:  # noqa: BLE001 - 업로드 실패는 파이프라인 전체를 실패시키지 않음
            supabase_error = str(exc)
            print(f"  [경고] Supabase 업로드 실패: {exc}")

        result = {
            "title": summary["title"],
            "tldr": summary["tldr"],
            "note_path": note_path,
            "api_cost_usd": round(api_cost_usd, 4),
            "supabase_path": supabase_path,
            "supabase_error": supabase_error,
            "title_slug": title_slug,
            "node_summary": node_summary,
            "text_hash": text_hash,
        }

        summary_json_path = write_summary_json(vault_path, title_slug, result)
        try:
            upload_summary(summary_json_path, title_slug)
        except Exception as exc:  # noqa: BLE001 - 업로드 실패는 파이프라인 전체를 실패시키지 않음
            print(f"  [경고] 요약 JSON 업로드 실패: {exc}")

        yield _event("done", 100, "완료!", **result)
    except Exception as exc:  # noqa: BLE001 - surface any failure to the UI
        yield _event("error", 100, f"오류: {exc}")
    finally:
        os.unlink(tmp_path)


@app.post("/api/process")
async def process_paper(request: Request, file: UploadFile = File(...), overwrite_slug: str | None = Form(None)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="PDF 파일만 지원합니다.")

    vault_path = get_vault_path()

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    return StreamingResponse(
        run_pipeline(tmp_path, vault_path, request, overwrite_slug), media_type="application/x-ndjson"
    )


def _find_paper_by_hash(vault_path: str, text_hash: str) -> dict | None:
    """vault의 논문 폴더들을 훑어 같은 text_hash를 가진 .summary.json이 있는지 찾는다.
    text_hash 필드가 없는(이 필드 도입 전에 처리된) 옛 논문은 비교 대상에서 자연히
    빠진다 - 별도 백필 없이도 하위 호환됨."""
    autonote_dir = Path(vault_path) / "AutoNote"
    if not autonote_dir.is_dir():
        return None
    for folder in autonote_dir.iterdir():
        if not folder.is_dir():
            continue
        summary_path = folder / f"{folder.name}.summary.json"
        if not summary_path.is_file():
            continue
        try:
            data = json.loads(summary_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if data.get("text_hash") == text_hash:
            return {"slug": folder.name, "title": data.get("title", folder.name)}
    return None


@app.post("/api/check-duplicate")
async def check_duplicate(file: UploadFile = File(...)):
    """업로드된 PDF가 이미 처리된 논문과 같은지, Claude를 부르기 전에 무료로 먼저
    확인한다. 제목 문자열은 처리할 때마다 Claude가 다르게 낼 수 있어(대소문자, 부제
    표기 등) 신뢰할 수 없으므로, 추출된 원문 텍스트의 해시로 비교한다."""
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="PDF 파일만 지원합니다.")

    vault_path = get_vault_path()

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name
    try:
        paper_text = extract_text(tmp_path)
    finally:
        os.unlink(tmp_path)

    text_hash = hashlib.sha256(paper_text.encode("utf-8")).hexdigest()
    match = _find_paper_by_hash(vault_path, text_hash)
    return {"duplicate": match is not None, **(match or {})}


@app.get("/api/graph")
async def get_graph(focus: list[str] = Query(default=[]), only_focus: bool = False):
    """focus는 ?focus=a&focus=b처럼 여러 번 줄 수 있다 - 사이드바에서 여러 논문을
    동시에 켜면(멀티 토글) 그 논문들의 focus 그래프를 합쳐서 보여준다."""
    vault_path = get_vault_path()
    return build_graph(vault_path, focus, only_focus)


@app.get("/api/papers")
async def get_papers():
    """저장된 논문 목록을 {slug, title} 쌍으로 반환한다. slug(=저장 파일명)는 경로
    길이 제한 때문에 잘릴 수 있어 화면 표시용으로 부적합하므로, 로컬 vault 노트의
    frontmatter title(원본 전체 제목)을 같이 읽어 붙인다 - 프론트는 표시엔 title,
    그래프 포커스/삭제 등 실제 동작엔 slug를 쓴다."""
    try:
        slugs = list_papers()
    except Exception as exc:  # noqa: BLE001 - Supabase 미설정/오류 시 빈 목록으로 응답
        return {"papers": [], "error": str(exc)}

    vault_path = os.getenv("OBSIDIAN_VAULT_PATH")
    papers = []
    for slug in slugs:
        title = slug
        if vault_path:
            md_path = Path(vault_path) / "AutoNote" / slug / f"{slug}.md"
            if md_path.is_file():
                frontmatter, _ = _parse_frontmatter(md_path.read_text(encoding="utf-8"))
                title = frontmatter.get("title") or slug
        papers.append({"slug": slug, "title": title})
    return {"papers": papers}


class _CreateFolderPayload(BaseModel):
    name: str


@app.get("/api/paper-folders")
async def get_paper_folders():
    return {"folders": list_folders(NODE_STORE_ROOT)}


@app.post("/api/paper-folders")
async def post_paper_folder(payload: _CreateFolderPayload):
    try:
        return create_folder(NODE_STORE_ROOT, payload.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/paper-folders/{folder_id}")
async def delete_paper_folder(folder_id: str):
    try:
        delete_folder(NODE_STORE_ROOT, folder_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"deleted": folder_id}


class _SetPaperFolderPayload(BaseModel):
    folder_id: str | None = None


@app.put("/api/papers/{slug}/folder")
async def put_paper_folder(slug: str, payload: _SetPaperFolderPayload):
    try:
        set_paper_folder(NODE_STORE_ROOT, slug, payload.folder_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"slug": slug, "folder_id": payload.folder_id}


class _CreateBrainPayload(BaseModel):
    name: str


class _RenameBrainPayload(BaseModel):
    name: str


class _SetFolderBrainPayload(BaseModel):
    brain_id: str | None = None


class _SetPaperBrainPayload(BaseModel):
    brain_id: str | None = None


def _resync_papers_in_folders(folder_ids: list[str]) -> None:
    """folder_ids에 속한 모든 폴더의 논문들을 한 번에 재동기화한다 - Brain
    삭제/병합처럼 폴더 단위로 Brain 소속이 바뀌는 경우, 그 폴더 안 논문 각각의
    brain_id와 그 논문들이 걸린 concept/entity의 brain_ids까지 한 번에
    다시 계산해 Neo4j에 반영해야 하므로. 이것 자체가 (여러 논문에 걸쳐)
    백그라운드 작업 하나로 스케줄되고, 안에서는 그냥 순차적으로 돌린다 - 이미
    요청/응답 밖이라 여기서 더 쪼갤 이유가 없다."""
    if not folder_ids:
        return
    folder_id_set = set(folder_ids)
    for folder in list_folders(NODE_STORE_ROOT):
        if folder["id"] in folder_id_set:
            for paper_slug in folder.get("paper_slugs", []):
                _resync_paper_brain(paper_slug)


@app.get("/api/neo4j-sync-status")
async def get_neo4j_sync_status():
    """가장 최근의 Brain 백그라운드 재동기화 실패(있다면)를 반환한다 - Brain
    배정/삭제/병합은 이제 백그라운드로 도니 실패해도 그 요청의 HTTP 응답에는
    실릴 수 없다(이미 나간 뒤라서). papers.js가 그 액션 직후 잠깐 뒤에 이걸
    확인해서 사용자에게 보여준다. 성공하면 값이 안 바뀌므로, 프론트는 액션을
    시작한 시각 이후의 에러인지(`at`)까지 같이 확인해야 오래된 실패를 다시
    보여주지 않는다."""
    return {"last_error": _last_brain_sync_error}


@app.get("/api/brains")
async def get_brains():
    return {"brains": list_brains(NODE_STORE_ROOT)}


@app.post("/api/brains")
async def post_brain(payload: _CreateBrainPayload):
    try:
        return create_brain(NODE_STORE_ROOT, payload.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/api/brains/{brain_id}")
async def put_brain(brain_id: str, payload: _RenameBrainPayload):
    try:
        return rename_brain(NODE_STORE_ROOT, brain_id, payload.name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/brains/{brain_id}")
async def delete_brain_endpoint(brain_id: str, background_tasks: BackgroundTasks):
    """Brain 레코드만 지운다 - 안에 있던 Folder/논문은 "브레인 없음"으로
    돌아갈 뿐 그대로 남는다. 영향받은 논문들의 Neo4j brain_id/brain_ids
    재동기화는 응답을 막지 않는 백그라운드 작업으로 돌린다(로컬 삭제 자체는
    이미 끝난 뒤라 응답을 더 기다리게 할 이유가 없다 - 위 _last_brain_sync_error
    설명 참고)."""
    try:
        result = delete_brain(NODE_STORE_ROOT, brain_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    for paper_slug in result["direct_paper_slugs"]:
        background_tasks.add_task(_resync_paper_brain, paper_slug)
    background_tasks.add_task(_resync_papers_in_folders, result["affected_folder_ids"])
    return {"deleted": brain_id, **result}


@app.put("/api/paper-folders/{folder_id}/brain")
async def put_paper_folder_brain(folder_id: str, payload: _SetFolderBrainPayload, background_tasks: BackgroundTasks):
    """폴더 하나를 통째로 어느 Brain 소속으로 옮긴다 - 그 폴더 안 모든 논문이
    간접적으로 그 Brain에 속하게 되므로, 폴더 안 논문 전체를 백그라운드로
    재동기화한다."""
    try:
        folder = set_folder_brain(NODE_STORE_ROOT, folder_id, payload.brain_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    for paper_slug in folder.get("paper_slugs", []):
        background_tasks.add_task(_resync_paper_brain, paper_slug)
    return folder


@app.put("/api/papers/{slug}/brain")
async def put_paper_brain(slug: str, payload: _SetPaperBrainPayload, background_tasks: BackgroundTasks):
    """논문 하나를 Folder를 거치지 않고 Brain에 직접 넣거나(brain_id 지정)
    브레인 없음으로 되돌린다(brain_id=None)."""
    try:
        set_paper_brain(NODE_STORE_ROOT, slug, payload.brain_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    background_tasks.add_task(_resync_paper_brain, slug)
    return {"slug": slug, "brain_id": payload.brain_id}


@app.post("/api/brains/{loser_id}/merge-into/{survivor_id}")
async def post_merge_brains(loser_id: str, survivor_id: str, background_tasks: BackgroundTasks):
    """loser_id Brain을 survivor_id Brain으로 흡수한다(Brain Consolidation의
    컨테이너 병합 단계 - concept/entity 수준 중복 정리는 별도로 기존
    dedup.py/_merge_candidates.json 파이프라인을 쓴다)."""
    try:
        result = merge_brains(NODE_STORE_ROOT, survivor_id, loser_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    for paper_slug in result["moved_paper_slugs"]:
        background_tasks.add_task(_resync_paper_brain, paper_slug)
    background_tasks.add_task(_resync_papers_in_folders, result["moved_folder_ids"])
    return result


@app.get("/api/graph-search")
async def get_graph_search(q: str, top_k: int = 10, brain_id: str | None = None):
    """GraphRAG 하이브리드 검색(벡터+풀텍스트+그래프 확장, paper_notes/graph_db.py
    참고) - MCP 서버의 search_graph 툴이 그대로 감싸서 쓴다. brain_id를 주면
    그 Brain에 속한 Paper/Concept/Entity로만 결과를 좁힌다(graph_db.search()의
    brain_id 필터 참고)."""
    try:
        return {"results": graph_db_search(q, top_k, brain_id)}
    except Neo4jNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/concepts")
async def get_concepts(paper_slug: str):
    """paper_slug 논문에 이미 연결된(그 논문이 sources에 있는) concept 노드
    목록을 {slug, label} 쌍으로 반환한다. entity를 직접 만들 때 "어느 concept에
    연결할지" 드롭다운을 채우는 용도.

    예전엔 시스템 전체 concept 중에서 고를 수 있었는데, 그러면 지금 만드는
    entity의 carrier 논문과 실제로는 한 번도 연결된 적 없는 concept을 골라버릴
    수 있었다 - concept_slug만 보고 concept->entity 에지가 그려지는 반면
    paper->concept 에지는 없는 비일관 상태(그래프에 이 논문의 흔적이 안 남음)가
    생기는 원인이었다. 항상 그 논문 자신에 연결된 concept 중에서만 고르게 해서
    이 문제를 근본적으로 막는다."""
    nodes = list_nodes(NODE_STORE_ROOT, "concept")
    matching = [n for n in nodes if any(s.get("slug") == paper_slug for s in (n.get("sources") or []))]
    return {"concepts": [{"slug": n["slug"], "label": n["display_label"]} for n in matching]}


def _duplicate_node_http_exception(exc: DuplicateNodeError) -> HTTPException:
    """create_node_manual()이 퍼지 매칭으로 비슷한 기존 노드를 찾았을 때(force=False)
    던지는 DuplicateNodeError를, 프론트가 "그래도 새로 만들기" 선택지를 보여줄 수
    있도록 detail을 문자열이 아니라 구조화된 객체로 담아 409를 반환한다. 사용자가
    그래도 만들기로 하면 프론트는 같은 요청을 force=true로 다시 보낸다."""
    return HTTPException(
        status_code=409,
        detail={
            "type": "similar_exists",
            "message": str(exc),
            "existing": {"slug": exc.match["slug"], "label": exc.match["display_label"]},
        },
    )


class _AddConceptPayload(BaseModel):
    label: str
    category: str
    force: bool = False


@app.post("/api/papers/{slug}/concepts")
async def add_concept(slug: str, payload: _AddConceptPayload):
    """사용자가 그래프/논문 화면에서 직접 concept을 추가한다. 이름이 완전히 같은
    노드가 이미 있으면 항상 409, 비슷한(퍼지 매칭) 노드가 있으면 force=false일 때만
    409(프론트가 "그래도 새로 만들지" 물어봄) - force=true면 그 확인을 건너뛰고
    만든다. create_node_manual()이 sources에 이 논문을 바로 기록하므로(node_store가
    유일한 소스), 논문 쪽에 별도로 쓸 것이 없다."""
    label = payload.label.strip()
    if not label:
        raise HTTPException(status_code=400, detail="개념 이름을 입력하세요.")

    vault_path = get_vault_path()
    note_path = Path(vault_path) / "AutoNote" / slug / f"{slug}.md"
    if not note_path.is_file():
        raise HTTPException(status_code=404, detail="논문 노트를 찾을 수 없습니다.")
    frontmatter, _ = _parse_frontmatter(note_path.read_text(encoding="utf-8"))
    title = frontmatter.get("title") or slug

    try:
        node_slug = create_node_manual(
            NODE_STORE_ROOT, "concept", label, slug, title, category=payload.category, force=payload.force
        )
    except DuplicateNodeError as exc:
        raise _duplicate_node_http_exception(exc) from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    _sync_node("concept", node_slug)
    return {"slug": node_slug}


class _AddEntityPayload(BaseModel):
    label: str
    concept_slug: str | None = None
    force: bool = False


@app.post("/api/papers/{slug}/entities")
async def add_entity(slug: str, payload: _AddEntityPayload):
    """사용자가 그래프/논문/concept 화면에서 직접 entity를 추가한다. concept_slug를
    주면 그래프에서 concept -> entity로 연결되고, 없으면 이 논문에 직접 연결된다
    (기존 LLM 추출 entity와 그래프 상 동일한 동작). force 처리는 add_concept()와
    같다."""
    label = payload.label.strip()
    if not label:
        raise HTTPException(status_code=400, detail="엔티티 이름을 입력하세요.")

    vault_path = get_vault_path()
    note_path = Path(vault_path) / "AutoNote" / slug / f"{slug}.md"
    if not note_path.is_file():
        raise HTTPException(status_code=404, detail="논문 노트를 찾을 수 없습니다.")
    frontmatter, _ = _parse_frontmatter(note_path.read_text(encoding="utf-8"))
    title = frontmatter.get("title") or slug

    try:
        node_slug = create_node_manual(
            NODE_STORE_ROOT, "entity", label, slug, title, force=payload.force, concept_slug=payload.concept_slug
        )
    except DuplicateNodeError as exc:
        raise _duplicate_node_http_exception(exc) from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    _sync_node("entity", node_slug)
    return {"slug": node_slug}


class _CreateOrphanNodePayload(BaseModel):
    type: str
    label: str
    category: str | None = None
    anchor_id: str | None = None
    force: bool = False


@app.post("/api/nodes")
async def create_orphan_node(payload: _CreateOrphanNodePayload):
    """그래프 배경 우클릭으로 만드는 순수 orphan concept/entity - 어느 논문에도 아직
    연결되지 않는다(carrier 없음). 어느 논문과 연결할지는 나중에 그래프에서 다른
    노드로 드래그해서(POST /api/nodes/{type}/{slug}/link) 따로 정한다.

    anchor_id는 생성 당시 그래프에서 가장 가까웠던 다른 노드의 id를 그대로 저장해둔다 -
    브라우저 새로고침이나 서버 재시작으로 프론트의 위치 캐시가 사라져도, 그래프를 다시
    그릴 때 이 힌트로 그 노드 근처에 나타나게 하기 위함(graph_builder.py 참고).

    force 처리는 add_concept()/add_entity()와 같다 - 비슷한 노드가 이미 있으면
    기본적으로 409(similar_exists)를 반환해 프론트가 확인창을 띄우게 하고,
    force=true면 그 확인을 건너뛴다."""
    if payload.type not in ("concept", "entity"):
        raise HTTPException(status_code=400, detail="type은 concept 또는 entity여야 합니다.")
    label = payload.label.strip()
    if not label:
        raise HTTPException(status_code=400, detail="이름을 입력하세요.")

    try:
        node_slug = create_node_manual(
            NODE_STORE_ROOT, payload.type, label, None, None,
            category=payload.category, anchor_id=payload.anchor_id, force=payload.force,
        )
    except DuplicateNodeError as exc:
        raise _duplicate_node_http_exception(exc) from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _sync_node(payload.type, node_slug)
    return {"slug": node_slug}


class _LinkNodePayload(BaseModel):
    paper_slug: str | None = None
    concept_slug: str | None = None


@app.post("/api/nodes/{node_type}/{slug}/link")
async def link_node(node_type: str, slug: str, payload: _LinkNodePayload):
    """그래프에서 concept/entity 노드(orphan이든 이미 다른 논문에 연결돼 있던 것이든)를
    다른 논문 위로 드래그해서 연결할 때 호출된다. node_store가 유일한 소스라
    노드 파일의 sources[]만 갱신하면 된다(논문 쪽엔 더 이상 쓸 게 없음).

    paper_slug가 없으면(orphan concept에 orphan entity를 직접 붙이는 경우 -
    붙일 논문 자체가 없음) entity를 concept_slug로만 연결한다. concept 자신은
    이 경로를 쓸 수 없다(concept은 논문 없이 다른 무언가에 "연결"될 방법이
    없음 - concept_slug 같은 자기 필드가 없다)."""
    if node_type not in ("concept", "entity"):
        raise HTTPException(status_code=404, detail="알 수 없는 노드 타입입니다.")

    if payload.paper_slug is None:
        if node_type != "entity" or not payload.concept_slug:
            raise HTTPException(
                status_code=400, detail="paper_slug 없이는 entity를 concept_slug로만 연결할 수 있습니다."
            )
        try:
            link_node_to_paper(NODE_STORE_ROOT, node_type, slug, None, None, concept_slug=payload.concept_slug)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        _sync_node(node_type, slug)
        return {"ok": True}

    vault_path = get_vault_path()
    note_path = Path(vault_path) / "AutoNote" / payload.paper_slug / f"{payload.paper_slug}.md"
    if not note_path.is_file():
        raise HTTPException(status_code=404, detail="연결할 논문 노트를 찾을 수 없습니다.")
    frontmatter, _ = _parse_frontmatter(note_path.read_text(encoding="utf-8"))
    paper_title = frontmatter.get("title") or payload.paper_slug

    try:
        link_node_to_paper(
            NODE_STORE_ROOT, node_type, slug, payload.paper_slug, paper_title, concept_slug=payload.concept_slug
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    _sync_node(node_type, slug)
    return {"ok": True}


@app.delete("/api/nodes/{node_type}/{slug}/sources/{paper_slug}")
async def delete_node_source(node_type: str, slug: str, paper_slug: str):
    """그래프에서 note↔concept 또는 note↔entity(직접 연결) 에지를 사용자가
    직접 끊을 때 호출된다. 이 노드의 sources[]에서 그 논문 항목 하나만
    지운다 - sources가 다 비어도 파일은 지우지 않고 orphan으로 남긴다(다른
    데이터를 보존하기 위해서 - 완전 삭제는 별도의 노드 삭제 기능을 써야 함)."""
    if node_type not in ("concept", "entity"):
        raise HTTPException(status_code=404, detail="알 수 없는 노드 타입입니다.")

    # concept의 논문 연결을 끊으면 그 concept 밑에 있던 entity들의 sources도
    # 같이 바뀔 수 있다(paperless로 전환되거나, 중복 항목이면 아예 삭제됨 -
    # node_store.remove_source_from_node/_unlink_paper_from_concept_entities
    # 참고). 그 entity들이 누구인지는 concept 파일이 아직 안 바뀐 지금
    # 시점에 미리 찾아둬야 한다(파일이 바뀐 뒤엔 이 concept_slug 연결 자체가
    # 이미 끊겼을 수 있어 못 찾을 수도 있음).
    affected_entity_slugs = (
        [e["slug"] for e in find_entities_by_concept(NODE_STORE_ROOT, slug)] if node_type == "concept" else []
    )

    try:
        removed = remove_source_from_node(NODE_STORE_ROOT, node_type, slug, paper_slug)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not removed:
        raise HTTPException(status_code=404, detail="그 논문과의 연결을 찾을 수 없습니다.")

    _sync_node(node_type, slug)
    for entity_slug in affected_entity_slugs:
        _sync_node("entity", entity_slug)
    return {"ok": True}


@app.delete("/api/nodes/entity/{slug}/concept/{concept_slug}")
async def delete_entity_concept_link(slug: str, concept_slug: str):
    """그래프에서 concept↔entity 에지를 사용자가 직접 끊을 때 호출된다. 이
    entity의 sources[] 중 그 concept_slug와 일치하는 항목을 전부 찾아
    통째로 삭제한다(entity가 여러 논문에서 독립적으로 같은 concept 밑에
    묶였을 수 있어, 하나만 처리하면 나머지 때문에 에지가 그래프에 그대로
    남는다). 논문 유무와 무관하게 삭제하고 entity→note 직접 연결로는
    되돌리지 않는다 - concept과의 관계를 끊는다는 건 그 맥락에서 완전히
    빠진다는 뜻이라(node_store.unlink_concept_from_entity 참고)."""
    try:
        changed = unlink_concept_from_entity(NODE_STORE_ROOT, slug, concept_slug)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not changed:
        raise HTTPException(status_code=404, detail="그 concept과의 연결을 찾을 수 없습니다.")
    _sync_node("entity", slug)
    return {"ok": True}


@app.get("/api/nodes/{node_type}/{slug}")
async def get_node(node_type: str, slug: str):
    """그래프 뷰에서 노드를 클릭했을 때 보여줄 md 내용을 반환한다. note는 vault의
    논문 노트, concept/entity는 node_store.py가 관리하는 별도 노드 파일에서 읽는다."""
    vault_path = get_vault_path()

    if node_type == "note":
        path = Path(vault_path) / "AutoNote" / slug / f"{slug}.md"
        if not path.is_file():
            raise HTTPException(status_code=404, detail="논문 노트를 찾을 수 없습니다.")
        frontmatter, body = _parse_frontmatter(path.read_text(encoding="utf-8"))
        return {
            "type": "note",
            "slug": slug,
            "title": frontmatter.get("title") or slug,
            "meta": {"authors": frontmatter.get("authors"), "tags": frontmatter.get("tags") or []},
            "body_markdown": body.strip(),
            "links": _resolve_wikilinks(vault_path, body),
        }

    if node_type not in ("concept", "entity"):
        raise HTTPException(status_code=404, detail="알 수 없는 노드 타입입니다.")

    dir_name = "_concepts" if node_type == "concept" else "_entities"
    path = Path(NODE_STORE_ROOT) / dir_name / f"{slug}.md"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="노드 파일을 찾을 수 없습니다.")

    frontmatter, body = _parse_frontmatter(path.read_text(encoding="utf-8"))
    if frontmatter.get("redirect_to"):
        raise HTTPException(status_code=404, detail="다른 노드로 병합되어 사라진 노드입니다.")

    return {
        "type": node_type,
        "slug": slug,
        "title": frontmatter.get("display_label") or slug,
        "meta": {
            "aliases": frontmatter.get("aliases") or [],
            "categories": frontmatter.get("categories") or [],
            "sources": frontmatter.get("sources") or [],
        },
        "body_markdown": body.strip(),
        # 노드 화면이 자동 생성 영역(읽기 전용)과 user-notes 영역(클릭하면 바로
        # 편집되는 영역)을 따로 렌더링할 수 있도록 body_markdown과 별개로 둘을 나눠서도 준다.
        "auto_markdown": get_auto_section(NODE_STORE_ROOT, node_type, slug),
        "user_markdown": get_user_section(NODE_STORE_ROOT, node_type, slug),
        "links": _resolve_wikilinks(vault_path, body),
    }


class _NotesPayload(BaseModel):
    user_notes_markdown: str


@app.put("/api/nodes/{node_type}/{slug}/notes")
async def put_node_notes(node_type: str, slug: str, payload: _NotesPayload):
    """concept/entity 노드의 개인 메모를 저장한다. 자동 생성 영역(등장 논문
    목록)은 그대로 두고 사용자 메모 부분만 교체한다."""
    if node_type not in ("concept", "entity"):
        raise HTTPException(status_code=404, detail="알 수 없는 노드 타입입니다.")
    try:
        update_user_section(NODE_STORE_ROOT, node_type, slug, payload.user_notes_markdown)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _sync_node(node_type, slug)
    return {"ok": True}


class _AliasPayload(BaseModel):
    alias: str


@app.post("/api/nodes/{node_type}/{slug}/aliases")
async def add_node_alias(node_type: str, slug: str, payload: _AliasPayload):
    """사용자가 노드 화면에서 직접 별칭을 추가한다 - LLM이 놓친 표기를 보완하거나,
    나중에 발견한 다른 이름을 등록해둔다. 갱신된 aliases 목록을 반환한다."""
    if node_type not in ("concept", "entity"):
        raise HTTPException(status_code=404, detail="알 수 없는 노드 타입입니다.")
    try:
        aliases = add_alias(NODE_STORE_ROOT, node_type, slug, payload.alias)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DuplicateNodeError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "type": "alias_taken",
                "message": f"'{payload.alias}'는 이미 다른 노드(\"{exc.match['display_label']}\")가 쓰고 있습니다.",
                "existing": {"slug": exc.match["slug"], "label": exc.match["display_label"]},
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _sync_node(node_type, slug)
    return {"aliases": aliases}


@app.delete("/api/nodes/{node_type}/{slug}/aliases")
async def remove_node_alias(node_type: str, slug: str, payload: _AliasPayload):
    """사용자가 노드 화면에서 직접 별칭을 지운다 - LLM이 잘못 판단해서 붙인 별칭을
    바로잡을 수 있게 한다. 갱신된 aliases 목록을 반환한다."""
    if node_type not in ("concept", "entity"):
        raise HTTPException(status_code=404, detail="알 수 없는 노드 타입입니다.")
    try:
        aliases = remove_alias(NODE_STORE_ROOT, node_type, slug, payload.alias)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _sync_node(node_type, slug)
    return {"aliases": aliases}


class _DisplayLabelPayload(BaseModel):
    label: str


@app.put("/api/nodes/{node_type}/{slug}/display-label")
async def put_node_display_label(node_type: str, slug: str, payload: _DisplayLabelPayload):
    """사용자가 노드의 표시 이름을 직접 바꾼다. slug(파일명, 다른 노드가 이
    concept을 가리킬 때 쓰는 실제 키)는 그대로 두고 display_label만 바뀐다 -
    node_store.rename_display_label() 참고. 예전 이름은 자동으로 별칭이 되어
    다른 논문 본문의 기존 위키링크가 계속 풀린다."""
    if node_type not in ("concept", "entity"):
        raise HTTPException(status_code=404, detail="알 수 없는 노드 타입입니다.")
    try:
        result = rename_display_label(NODE_STORE_ROOT, node_type, slug, payload.label)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DuplicateNodeError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "type": "alias_taken",
                "message": f"'{payload.label}'는 이미 다른 노드(\"{exc.match['display_label']}\")가 쓰고 있습니다.",
                "existing": {"slug": exc.match["slug"], "label": exc.match["display_label"]},
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _sync_node(node_type, slug)
    return result


class _CategoryPayload(BaseModel):
    category: str


@app.post("/api/nodes/concept/{slug}/categories")
async def add_node_category(slug: str, payload: _CategoryPayload):
    """사용자가 concept 화면에서 직접 카테고리를 추가한다 - LLM이 매긴 카테고리가
    마음에 안 들거나, 한 concept이 여러 카테고리에 걸친다고 판단했을 때 보완한다.
    갱신된 categories 목록을 반환한다."""
    try:
        categories = add_category(NODE_STORE_ROOT, slug, payload.category)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _sync_node("concept", slug)
    return {"categories": categories}


@app.delete("/api/nodes/concept/{slug}/categories")
async def remove_node_category(slug: str, payload: _CategoryPayload):
    """사용자가 concept 화면에서 직접 카테고리를 지운다 - LLM이 잘못 매긴 카테고리를
    바로잡을 수 있게 한다. 갱신된 categories 목록을 반환한다."""
    try:
        categories = remove_category(NODE_STORE_ROOT, slug, payload.category)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _sync_node("concept", slug)
    return {"categories": categories}


@app.post("/api/nodes/{node_type}/{slug}/attachments")
async def post_node_attachment(node_type: str, slug: str, file: UploadFile = File(...)):
    """개인 메모에 붙여넣을 이미지를 업로드하고, 마크다운에서 참조할 상대경로를
    반환한다. 실제 이미지는 /attachments로 정적 서빙된다."""
    if node_type not in ("concept", "entity"):
        raise HTTPException(status_code=404, detail="알 수 없는 노드 타입입니다.")

    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="이미지 용량은 10MB를 넘을 수 없습니다.")

    try:
        path = save_attachment(NODE_STORE_ROOT, node_type, slug, file.filename or "", content)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"path": path}


@app.get("/api/nodes/concept/{slug}/linked-entities")
async def get_concept_linked_entities(slug: str):
    """concept 삭제 확인 창을 띄우기 전에, 이 concept 밑에 entity가 걸려 있는지
    미리 알아본다 - 있으면 그래프 UI가 "entity도 같이 지울지" 선택지를 보여준다."""
    concept = next((n for n in list_nodes(NODE_STORE_ROOT, "concept") if n["slug"] == slug), None)
    if not concept:
        raise HTTPException(status_code=404, detail="노드를 찾을 수 없습니다.")
    entities = find_entities_by_concept(NODE_STORE_ROOT, slug)
    return {"entities": [{"slug": e["slug"], "label": e["display_label"]} for e in entities]}


@app.delete("/api/nodes/{node_type}/{slug}")
async def delete_node_endpoint(node_type: str, slug: str, cascade_entities: bool = False):
    """사용자가 그래프에서 직접 만들었든 LLM이 뽑았든, concept/entity 노드를
    통째로 지운다. node_store가 유일한 소스라 논문 쪽에는 정리할 게 없다 - 노드
    파일만 지우면 된다(entity의 sources에 이 concept의 slug가 남아있어도,
    graph_builder.py가 그 slug를 못 찾으면 자동으로 "논문에 직접 연결"로 취급하므로
    죽은 참조로 인한 문제가 없다).

    cascade_entities=true면(concept 삭제일 때만 의미 있음) 그 concept 밑에 걸려있던
    entity 노드들까지 함께 지운다 - 기본값(false)은 기존 동작 그대로: entity는
    안 지우고 concept 소속만 풀어 논문에 직접 연결된 상태로 남긴다."""
    if node_type not in ("concept", "entity"):
        raise HTTPException(status_code=404, detail="알 수 없는 노드 타입입니다.")

    # concept 밑에 걸려있던 entity는 concept 자신을 지우기 전에 찾아둔다(순서는
    # 사실 상관없다 - entity의 sources에 있는 concept_slug는 concept 파일 삭제와
    # 무관하게 그대로 남아있으므로). cascade_entities=false여도 이 목록이
    # 필요하다 - 그 entity들은 파일은 그대로 남지만 Neo4j 미러에서는 이제
    # concept_slug가 죽은 참조가 되었으니 "논문에 직접 연결"로 다시 동기화해야
    # 한다(graph_db.sync_node()의 concept_slug 유효성 검사 참고).
    linked_entity_slugs: list[str] = []
    if node_type == "concept":
        linked_entity_slugs = [e["slug"] for e in find_entities_by_concept(NODE_STORE_ROOT, slug)]

    try:
        delete_node(NODE_STORE_ROOT, node_type, slug)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _delete_node_from_graph(node_type, slug)

    if cascade_entities:
        for entity_slug in linked_entity_slugs:
            try:
                delete_node(NODE_STORE_ROOT, "entity", entity_slug)
            except FileNotFoundError:
                continue
            _delete_node_from_graph("entity", entity_slug)
    else:
        for entity_slug in linked_entity_slugs:
            _sync_node("entity", entity_slug)

    return {"ok": True}


@app.get("/api/papers/{slug}/summary")
async def get_paper_summary(slug: str):
    vault_path = get_vault_path()
    summary_path = Path(vault_path) / "AutoNote" / slug / f"{slug}.summary.json"
    if not summary_path.is_file():
        raise HTTPException(status_code=404, detail="요약 정보를 찾을 수 없습니다.")
    return json.loads(summary_path.read_text(encoding="utf-8"))


@app.get("/api/vault-attachment")
async def get_vault_attachment(note_slug: str, filename: str):
    """Obsidian에서 논문 노트에 직접 붙여넣은 첨부 이미지(본문의 ![[파일명]])를
    서빙한다. node_store 첨부(/attachments)와 달리 이 파일은 vault 안 어디에
    있는지 미리 알 수 없다(Obsidian 첨부 폴더 설정에 따라 노트와 같은 폴더일 수도,
    다른 고정 폴더일 수도 있음) - 노트 폴더를 먼저 보고, 없으면 vault 전체를
    파일명으로 훑는다(클릭 시 1회성 조회라 그래프 렌더링 경로에는 영향 없음)."""
    filename = Path(filename).name
    note_slug = Path(note_slug).name
    if Path(filename).suffix.lower() not in IMAGE_EXTENSIONS:
        raise HTTPException(status_code=400, detail="지원하지 않는 파일 형식입니다.")

    vault_path = get_vault_path()
    co_located = Path(vault_path) / "AutoNote" / note_slug / filename
    if co_located.is_file():
        return FileResponse(co_located)

    for root, _dirs, files in os.walk(vault_path):
        if filename in files:
            return FileResponse(Path(root) / filename)

    raise HTTPException(status_code=404, detail="첨부 이미지를 찾을 수 없습니다.")


@app.delete("/api/papers/{slug}")
async def delete_paper(slug: str):
    vault_path = get_vault_path()

    local_error: str | None = None
    try:
        delete_local_note(vault_path, slug)
    except Exception as exc:  # noqa: BLE001 - 한쪽 삭제 실패가 다른 쪽을 막지 않음
        local_error = str(exc)

    remote_error: str | None = None
    try:
        delete_remote_note(slug)
    except Exception as exc:  # noqa: BLE001 - 한쪽 삭제 실패가 다른 쪽을 막지 않음
        remote_error = str(exc)

    try:
        for node_type, node_slug, was_deleted in remove_source(NODE_STORE_ROOT, slug):
            if was_deleted:
                _delete_node_from_graph(node_type, node_slug)
            else:
                _sync_node(node_type, node_slug)
    except Exception as exc:  # noqa: BLE001 - node_store 정리 실패가 나머지 삭제를 막지 않음
        print(f"  [경고] node_store 참조 정리 실패: {exc}")

    _delete_node_from_graph("paper", slug)

    try:
        set_paper_folder(NODE_STORE_ROOT, slug, None)
    except Exception as exc:  # noqa: BLE001 - 폴더 정리 실패가 나머지 삭제를 막지 않음
        print(f"  [경고] 논문 폴더 정리 실패: {exc}")

    return {"slug": slug, "local_error": local_error, "remote_error": remote_error}


_attachments_dir = Path(NODE_STORE_ROOT) / "attachments"
_attachments_dir.mkdir(parents=True, exist_ok=True)
app.mount("/attachments", StaticFiles(directory=str(_attachments_dir)), name="attachments")

app.mount("/", StaticFiles(directory="static", html=True), name="static")
