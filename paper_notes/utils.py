from __future__ import annotations

import re


def slugify(title: str) -> str:
    """제목을 노트 폴더/파일명으로 쓸 slug로 만든다.

    slug는 경로에 두 번 들어간다 (AutoNote/<slug>/<slug>.summary.json 등, 가장 긴
    확장자 기준 +13자). Windows의 기본 MAX_PATH(260자) 안에 vault 경로 길이가
    얼마든 여유를 두고 들어가도록 70자로 자른다 — 120자였을 때는 제목이 긴
    논문에서 vault 경로와 합쳐 260자를 넘겨 파일이 존재해도 is_file()이 False를
    반환하는(long path 미지원 환경) 문제가 있었다.
    """
    slug = re.sub(r'[\\/:*?"<>|]', "", title).strip()
    return slug[:70].strip() or "untitled"
