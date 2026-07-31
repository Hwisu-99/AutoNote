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


# 논문 포맷마다 소제목 표기가 달라 완벽하지 않은 휴리스틱이다. 실패하면
# extract_front_matter()가 전체 텍스트로 안전하게 fallback한다.
_MIN_FRONT_MATTER_CHARS = 500
_FIGURE_CAPTION_RE = re.compile(r"^Figure\s+\d+\s*:.*$", re.MULTILINE)

# 절 번호 표기: 아라비아 숫자("1.", "1") 또는 로마 숫자(IEEE 스타일 "I.", "II.")
_SECTION_NUM = r"(?:[0-9]+|[IVXLCDM]+)\.?\s*"


def _find_heading(lines: list[str], patterns: list[str], start: int = 0, end: int | None = None) -> int | None:
    """patterns를 우선순위 순서로 시도한다: 앞선 패턴이 [start, end) 구간 어디에도
    없을 때만 다음(더 느슨한) 패턴으로 넘어간다. 한 줄씩 훑으며 아무 패턴에나
    걸리는 것을 반환하면, 본문 중간의 약한 매치(예: 일반 "Discussion" 소제목)가
    뒤쪽의 정확한 매치(예: 진짜 "Conclusion")보다 먼저 잡히는 문제가 생긴다.

    start/end로 검색 구간을 앞/뒤로 나누는 이유: "Summary"처럼 abstract의
    동의어이면서 동시에 conclusion의 동의어이기도 한 단어가 있어서, 문서 전체를
    검색하면 앞쪽 Summary(=abstract)를 뒤쪽 conclusion으로 착각할 수 있다."""
    end = len(lines) if end is None else end
    for pattern in patterns:
        for i in range(start, end):
            if re.fullmatch(pattern, lines[i].strip(), re.IGNORECASE):
                return i
    return None


def extract_front_matter(text: str) -> str:
    """Abstract/Introduction/Conclusion/Figure caption만 뽑아 1차(concept 추출)
    호출용 입력을 만든다. 본문 전체 대비 훨씬 작아 1차 호출 비용을 크게 줄인다.
    학술지/컨퍼런스 템플릿마다 소제목 표기가 달라(Summary, Overview, Discussion
    등) 완벽하지 않은 휴리스틱이므로, 섹션 경계를 못 찾아 결과가 너무 짧으면
    (500자 미만) 전체 텍스트를 그대로 반환해 안전하게 fallback한다."""
    lines = text.split("\n")
    n = len(lines)
    front_half, back_half = n // 2, n // 2

    # abstract/introduction은 문서 앞쪽 절반에서만, conclusion은 뒤쪽 절반에서만
    # 찾는다 (_strip_references가 References를 뒤쪽 절반에서만 찾는 것과 같은 이유).
    abstract_idx = _find_heading(
        lines, [r"abstract", r"executive summary", r"summary"], start=0, end=front_half
    )
    intro_idx = _find_heading(
        lines,
        [
            r"introduction",
            rf"{_SECTION_NUM}introduction(?: and related work)?",
            r"overview",
            r"background and motivation",
            rf"{_SECTION_NUM}background",
        ],
        start=0,
        end=front_half,
    )
    conclusion_idx = _find_heading(
        lines,
        [
            # 우선순위 순서: 더 구체적이고 확실한 표기부터 시도하고, 이 구간에
            # 없을 때만 더 느슨한(오탐 위험이 큰) 표기로 넘어간다.
            rf"(?:{_SECTION_NUM})?conclusions?(?: and future work| and limitations)?",
            rf"(?:{_SECTION_NUM})?discussion and conclusions?",
            rf"(?:{_SECTION_NUM})?concluding remarks",
            rf"(?:{_SECTION_NUM})?summary(?: and (?:conclusions?|future work))?",
            rf"(?:{_SECTION_NUM})?discussion",
        ],
        start=back_half,
        end=n,
    )

    print(
        f"  [front matter 탐지] Abstract={'O' if abstract_idx is not None else 'X'} "
        f"Introduction={'O' if intro_idx is not None else 'X'} "
        f"Conclusion={'O' if conclusion_idx is not None else 'X'}"
    )

    parts: list[str] = []

    if abstract_idx is not None and intro_idx is not None and intro_idx > abstract_idx:
        abstract_text = "\n".join(lines[abstract_idx + 1 : intro_idx]).strip()
        if abstract_text:
            parts.append(f"## Abstract\n{abstract_text}")

    if intro_idx is not None:
        end = min(intro_idx + 200, conclusion_idx if conclusion_idx else len(lines))
        intro_text = "\n".join(lines[intro_idx + 1 : end]).strip()
        if intro_text:
            parts.append(f"## Introduction\n{intro_text}")

    if conclusion_idx is not None:
        conclusion_text = "\n".join(lines[conclusion_idx + 1 :]).strip()
        if conclusion_text:
            parts.append(f"## Conclusion\n{conclusion_text}")

    captions = _FIGURE_CAPTION_RE.findall(text)
    if captions:
        parts.append("## Figure Captions\n" + "\n".join(captions))

    front_matter = "\n\n".join(parts)
    if len(front_matter) < _MIN_FRONT_MATTER_CHARS:
        print(
            f"  [front matter 추출 실패] {len(front_matter)}자밖에 못 찾아 전체 "
            "텍스트로 대체합니다."
        )
        return text
    return front_matter
