from __future__ import annotations

import re


_UNSAFE_CHARS_RE = re.compile(r"[^A-Za-z0-9 \-_.,()']")
_WHITESPACE_RE = re.compile(r"\s+")


def slugify(title: str) -> str:
    """제목을 노트 폴더/파일명 + Supabase Storage 오브젝트 키로 쓸 slug로 만든다.

    영숫자/공백/`-_.,()'`만 허용하는 allow-list 방식이다. 예전엔 Windows
    파일시스템에서 금지된 문자(`\\/:*?"<>|`)만 걷어내는 deny-list였는데, em-dash(—)
    같은 문자는 로컬 파일명으로는 멀쩡히 써져서 문제없어 보이다가 Supabase
    Storage의 오브젝트 키 검증(로컬 파일시스템보다 훨씬 엄격함)에서 "Invalid key"로
    거부당했다. deny-list는 이런 식으로 처음 보는 특수문자(curly quote, 말줄임표
    등)가 나올 때마다 매번 새로 뚫리므로, "안전하다고 확인된 문자만 허용"하는
    allow-list로 바꿔서 같은 부류의 버그가 재발하지 않게 한다.

    slug는 경로에 두 번 들어간다 (AutoNote/<slug>/<slug>.summary.json 등, 가장 긴
    확장자 기준 +13자). Windows의 기본 MAX_PATH(260자) 안에 vault 경로 길이가
    얼마든 여유를 두고 들어가도록 70자로 자른다 — 120자였을 때는 제목이 긴
    논문에서 vault 경로와 합쳐 260자를 넘겨 파일이 존재해도 is_file()이 False를
    반환하는(long path 미지원 환경) 문제가 있었다.
    """
    slug = _UNSAFE_CHARS_RE.sub(" ", title)
    slug = _WHITESPACE_RE.sub(" ", slug).strip()
    return slug[:70].strip() or "untitled"
