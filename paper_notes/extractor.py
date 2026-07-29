from __future__ import annotations

import re

import fitz  # PyMuPDF

# "References" / "Bibliography" / "참고문헌"이 단독으로 한 줄에 있는 경우를 섹션 제목으로 간주.
# 숫자(예: "8. References")나 콜론이 붙는 경우도 허용.
_REFERENCE_HEADER = re.compile(
    r"^\s*(?:[0-9]{1,3}[\.\)]?\s*)?(references|bibliography|참고\s*문헌)\s*[:\-]?\s*$",
    re.IGNORECASE,
)

# 이 비율보다 많이 잘려나가면 중간 지점을 잘못 매칭한 것으로 보고 자르지 않는다.
_MAX_REMOVE_RATIO = 0.4
_PREVIEW_CHARS = 300


def _strip_references(text: str) -> str:
    """참고문헌 섹션부터 문서 끝까지 잘라낸다. Claude에게 불필요한 입력 토큰을 줄이기 위함.
    본문 중간에 "references"라는 단어가 문장 안에 섞여 있는 오탐을 피하려고
    문서 후반부(뒤 절반)에서만 검색하고, 잘리는 비율이 비정상적으로 크면 자르지 않는다."""
    lines = text.split("\n")
    search_start = len(lines) // 2

    for i in range(search_start, len(lines)):
        if not _REFERENCE_HEADER.match(lines[i]):
            continue

        removed = "\n".join(lines[i:])
        removed_ratio = len(removed) / max(len(text), 1)

        if removed_ratio > _MAX_REMOVE_RATIO:
            print(
                f"  [참고문헌 제거 스킵] \"{lines[i].strip()}\" 지점부터 자르면 "
                f"문서의 {removed_ratio:.0%}가 잘려나가 안전을 위해 건너뜁니다."
            )
            continue

        kept = "\n".join(lines[:i]).rstrip()
        preview = " ".join(removed.split())[:_PREVIEW_CHARS]
        print(f"  [참고문헌 제거] \"{lines[i].strip()}\" 지점부터 {len(removed):,}자 삭제 (전체의 {removed_ratio:.0%})")
        print(f"  [잘린 내용 미리보기] {preview}...")
        return kept

    return text


def extract_text(pdf_path: str) -> str:
    """논문 PDF에서 페이지 순서대로 텍스트를 추출해 하나의 문자열로 합치고,
    참고문헌 섹션을 제거한다."""
    doc = fitz.open(pdf_path)
    try:
        pages = [page.get_text() for page in doc]
    finally:
        doc.close()

    full_text = "\n\n".join(pages).strip()
    return _strip_references(full_text)
