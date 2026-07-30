from __future__ import annotations

import asyncio
import contextlib
import json
import os
import tempfile
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from paper_notes.claude_client import summarize_paper
from paper_notes.excalidraw_writer import write_diagram
from paper_notes.extractor import extract_text
from paper_notes.graph_builder import build_graph
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


async def run_pipeline(tmp_path: str, vault_path: str, request: Request):
    try:
        if await request.is_disconnected():
            return

        yield _event("extract", 10, "PDF에서 텍스트 추출 중...")
        paper_text = extract_text(tmp_path)
        if not paper_text.strip():
            yield _event("error", 100, "PDF에서 텍스트를 추출할 수 없습니다.")
            return

        if await request.is_disconnected():
            return

        yield _event("summarize", 30, "Claude로 논문 분석 중... (시간이 조금 걸립니다)")
        result = await _summarize_cancellable(paper_text, request)
        if result is None:
            print("  [취소됨] 사용자가 취소하여 Claude 요청을 중단했습니다.")
            return
        summary, api_cost_usd = result

        title_slug = slugify(summary["title"])

        if await request.is_disconnected():
            return

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
            "one_line_summary": summary["one_line_summary"],
            "note_path": note_path,
            "excalidraw_filename": excalidraw_filename,
            "api_cost_usd": round(api_cost_usd, 4),
            "supabase_path": supabase_path,
            "supabase_error": supabase_error,
            "title_slug": title_slug,
            "node_summary": node_summary,
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
async def process_paper(request: Request, file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="PDF 파일만 지원합니다.")

    vault_path = get_vault_path()

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    return StreamingResponse(run_pipeline(tmp_path, vault_path, request), media_type="application/x-ndjson")


@app.get("/api/graph")
async def get_graph(focus: str | None = None, only_focus: bool = False):
    vault_path = get_vault_path()
    return build_graph(vault_path, focus, only_focus)


@app.get("/api/papers")
async def get_papers():
    try:
        return {"papers": list_papers()}
    except Exception as exc:  # noqa: BLE001 - Supabase 미설정/오류 시 빈 목록으로 응답
        return {"papers": [], "error": str(exc)}


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

    return {"slug": slug, "local_error": local_error, "remote_error": remote_error}


app.mount("/", StaticFiles(directory="static", html=True), name="static")
