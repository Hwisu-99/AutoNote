from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

import yaml
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from paper_notes.claude_client import summarize_paper
from paper_notes.excalidraw_writer import write_diagram
from paper_notes.extractor import extract_text
from paper_notes.graph_builder import build_graph
from paper_notes.node_store import (
    NODE_STORE_ROOT,
    find_node_fuzzy,
    get_user_section,
    list_nodes,
    node_index,
    remove_source,
    resolve_or_create_node,
    save_attachment,
    update_user_section,
)
from paper_notes.obsidian_writer import delete_note as delete_local_note
from paper_notes.obsidian_writer import write_note, write_summary_json
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


def _frontmatter_label_aliases(frontmatter: dict) -> dict[str, list[str]]:
    """frontmatter의 concepts/entities에서 {label: aliases} 사전을 만든다.
    _resolve_wikilinks()가 본문의 [[label]]을 node_store와 다시 매칭할 때, 처리
    시점(resolve_or_create_node)엔 있었던 alias 정보를 그대로 넘겨주기 위함이다.
    aliases 필드 도입 이전 노트는 concepts가 문자열 리스트였으므로 함께 정규화한다."""
    result: dict[str, list[str]] = {}
    for c in frontmatter.get("concepts") or []:
        if isinstance(c, str):
            result[c] = []
        else:
            result[c["label"]] = c.get("aliases") or []
    for e in frontmatter.get("entities") or []:
        result[e["label"]] = e.get("aliases") or []
    return result


def _resolve_wikilinks(vault_path: str, body: str, label_aliases: dict[str, list[str]] | None = None) -> dict[str, dict]:
    """본문의 [[wikilink]] 대상들을 note(vault) 또는 concept/entity(node_store)로
    풀어서 {원본 타깃 텍스트: {type, slug}}를 반환한다. 프론트가 이걸로 위키링크를
    실제로 클릭 가능하게 만들지(어디로 보낼지) 판단한다. node 파일 자신이 만드는
    "## 등장 논문" 링크는 대상이 이미 논문 slug라 바로 맞아떨어지고, 논문 본문의
    concept/entity 위키링크는 그 논문이 직접 뽑은 원본 라벨이라 node_store와
    퍼지 매칭(find_node_fuzzy)까지 거쳐야 한다.

    label_aliases(보통 _frontmatter_label_aliases()로 만든 이 노트 자신의 concepts/
    entities alias 사전)를 함께 넘기면, 처리 시점(resolve_or_create_node)엔 label+alias
    둘 다로 node_store와 매칭했던 것을 여기서도 재현할 수 있다 - alias 없이 label
    텍스트만으로는 못 잡는 경우(예: 이 논문은 "체크포인트 엔진"이라 쓰고 alias로만
    "Checkpoint Engine"을 줬는데 node_store엔 그 영어 표기로 등록된 경우)가 있어서다."""
    label_aliases = label_aliases or {}
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
        aliases = label_aliases.get(target)
        concept_match = find_node_fuzzy(concept_nodes, target, aliases, index=concept_idx)
        if concept_match:
            links[target] = {"type": "concept", "slug": concept_match["slug"]}
            continue
        entity_match = find_node_fuzzy(entity_nodes, target, aliases, index=entity_idx)
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
        try:
            if overwrite_slug:
                remove_source(NODE_STORE_ROOT, overwrite_slug)
            for c in summary.get("concepts", []):
                resolve_or_create_node(
                    NODE_STORE_ROOT, "concept", c["label"], c.get("aliases", []),
                    title_slug, summary["title"], category=c.get("category"),
                    description=c.get("description", ""), note=c.get("note", ""),
                )
            for e in summary.get("entities", []):
                resolve_or_create_node(
                    NODE_STORE_ROOT, "entity", e["label"], e.get("aliases", []),
                    title_slug, summary["title"],
                    description=e.get("description", ""), note=e.get("note", ""),
                )
        except Exception as exc:  # noqa: BLE001 - 노드 파일 갱신 실패가 파이프라인 전체를 막지 않음
            print(f"  [경고] concept/entity 노드 파일 갱신 실패: {exc}")

        yield _event("diagram", 70, "Excalidraw 개념도 생성 중...")
        excalidraw_filename = write_diagram(vault_path, title_slug, summary)

        yield _event("note", 90, "Obsidian 노트 저장 중...")
        note_path = write_note(vault_path, summary, title_slug, excalidraw_filename)

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
            "excalidraw_filename": excalidraw_filename,
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
async def get_graph(focus: str | None = None, only_focus: bool = False):
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
            "links": _resolve_wikilinks(vault_path, body, _frontmatter_label_aliases(frontmatter)),
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
            "category": frontmatter.get("category"),
            "sources": frontmatter.get("sources") or [],
        },
        "body_markdown": body.strip(),
        # 편집 UI가 textarea를 채울 때 쓰는, 자동 생성 영역을 뺀 사용자 메모 원문
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
    return {"ok": True}


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


@app.get("/api/papers/{slug}/summary")
async def get_paper_summary(slug: str):
    vault_path = get_vault_path()
    summary_path = Path(vault_path) / "AutoNote" / slug / f"{slug}.summary.json"
    if not summary_path.is_file():
        raise HTTPException(status_code=404, detail="요약 정보를 찾을 수 없습니다.")
    return json.loads(summary_path.read_text(encoding="utf-8"))


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
        remove_source(NODE_STORE_ROOT, slug)
    except Exception as exc:  # noqa: BLE001 - node_store 정리 실패가 나머지 삭제를 막지 않음
        print(f"  [경고] node_store 참조 정리 실패: {exc}")

    return {"slug": slug, "local_error": local_error, "remote_error": remote_error}


_attachments_dir = Path(NODE_STORE_ROOT) / "attachments"
_attachments_dir.mkdir(parents=True, exist_ok=True)
app.mount("/attachments", StaticFiles(directory=str(_attachments_dir)), name="attachments")

app.mount("/", StaticFiles(directory="static", html=True), name="static")
