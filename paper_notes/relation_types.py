"""concept/entity 사이 semantic 관계 타입의 화이트리스트
(docs/description/relation_types.md 참고).

이 그래프엔 두 종류의 에지만 존재한다: 출처(provenance)를 나타내는
`LINKED_TO`(paper_notes/graph_db.py가 그대로 관리)와, concept/entity
사이의 실제 지식 관계를 나타내는 semantic 에지(여기서 관리하는 타입들).
Paper는 semantic 에지의 endpoint가 될 수 없다 - 도메인 지식 노드가
아니라 컨테이너/출처 노드이기 때문이다.

Cypher는 관계 타입을 파라미터로 못 받는다(`MERGE (a)-[r:$type]->(b)` 같은
문법 자체가 없음) - 그래서 관계 타입 문자열을 Cypher 쿼리에 f-string으로
보간해야 하는데, 검증 없이 보간하면 인젝션 위험이 있다. 이 모듈이 반환하는
화이트리스트를 보간 직전에 항상 멤버십 체크해야 한다(graph_db.py의 sync_node/
app.py의 run_pipeline이 이렇게 쓴다) - "config에 있으니 안전하다"가 아니라
"쓰기 직전마다 확인한다"가 실제 안전을 만든다는 점이 중요하다.

`config/relation_types.json`이 있으면 그걸 쓴다(도메인마다 어휘가 달라질 수
있으므로 사용자가 로컬에서 편집 가능하게 - config/의 다른 파일들과 같은
이유로 이 파일도 .gitignore 대상이다). 없거나 읽기 실패하면 아래 기본
12개로 폴백한다.
"""
from __future__ import annotations

import json
from pathlib import Path

# symmetric=True인 타입(COMPARED_TO/CONTRADICTS/RELATED)은 저장 시점에
# slug 알파벳순으로 from/to를 정규화한다 - 논문마다 어느 쪽을 먼저 언급하든
# 항상 같은 방향의 에지 하나로 합쳐지게 하기 위함
# (docs/description/relation_types.md의 "대칭 타입 정규화" 참고).
DEFAULT_RELATION_TYPES: dict[str, dict] = {
    "PART_OF": {"symmetric": False},
    "IS_A": {"symmetric": False},
    "USES": {"symmetric": False},
    "EXTENDS": {"symmetric": False},
    "IMPROVES_ON": {"symmetric": False},
    "OUTPERFORMS": {"symmetric": False},
    "SOLVES": {"symmetric": False},
    "EVALUATED_ON": {"symmetric": False},
    "LIMITED_BY": {"symmetric": False},
    "COMPARED_TO": {"symmetric": True},
    "CONTRADICTS": {"symmetric": True},
    "RELATED": {"symmetric": True},
}


def _config_path(store_root: str) -> Path:
    return Path(store_root) / "config" / "relation_types.json"


def load_relation_types(store_root: str) -> dict[str, dict]:
    """{TYPE: {"symmetric": bool}} 화이트리스트를 반환한다. 매번 파일을 다시
    읽는다 - 개인 vault 규모에서 파일 하나 읽는 비용은 무시할 만하고, 캐싱
    무효화를 신경 쓰는 것보다 항상 최신 설정을 반영하는 쪽이 더 안전하다."""
    path = _config_path(store_root)
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            types = data.get("types")
            if isinstance(types, dict) and types:
                return types
        except (json.JSONDecodeError, OSError):
            pass
    return DEFAULT_RELATION_TYPES


def is_symmetric(relation_types: dict[str, dict], relation_type: str) -> bool:
    return bool(relation_types.get(relation_type, {}).get("symmetric", False))
