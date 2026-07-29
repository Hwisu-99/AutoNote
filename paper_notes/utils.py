from __future__ import annotations

import re


def slugify(title: str) -> str:
    slug = re.sub(r'[\\/:*?"<>|]', "", title).strip()
    return slug[:120] or "untitled"
