"""로컬 임베딩 모델(intfloat/multilingual-e5-small)로 concept/entity 설명을
벡터로 바꾼다. Neo4j 벡터 인덱스에 넣어서 GraphRAG의 시맨틱 검색 축을
담당한다(그래프 트래버설·풀텍스트와 함께 하이브리드 검색을 구성하는 세 축 중
하나 - paper_notes/graph_db.py 참고).

API 키/비용 없이 로컬에서 도는 대신 모델(약 470MB)을 처음 쓸 때 자동으로
다운로드한다. e5 계열 모델은 검색 정확도를 위해 텍스트 앞에 "query: "/
"passage: " 접두어를 붙이는 게 관례다(비대칭 검색 - 저장되는 문서는 passage,
사용자 질의는 query로 서로 다르게 인코딩해야 같은 의미의 텍스트끼리 더 가깝게
나온다). 이 모듈이 그 규칙을 강제해서, 호출부가 접두어를 깜빡해 검색 품질이
조용히 떨어지는 일을 막는다.

모델 로딩(수십~백 MB 가중치를 디스크에서 읽고 초기화하는 것)이 무거워서 첫
호출 때 한 번만 하고 프로세스 전역에 캐싱한다."""
from __future__ import annotations

import os
import threading

_MODEL_NAME = os.environ.get("EMBEDDING_MODEL", "intfloat/multilingual-e5-small")
_model = None
_model_lock = threading.Lock()


def _get_model():
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                from sentence_transformers import SentenceTransformer

                _model = SentenceTransformer(_MODEL_NAME)
    return _model


def embedding_dimension() -> int:
    """Neo4j 벡터 인덱스를 만들 때 벡터 차원을 맞추는 데 쓴다."""
    return _get_model().get_embedding_dimension()


def embed_passage(text: str) -> list[float]:
    """노드에 저장되는 텍스트(description/note 등)를 벡터로 바꾼다."""
    vec = _get_model().encode(f"passage: {text}", normalize_embeddings=True)
    return vec.tolist()


def embed_passages(texts: list[str]) -> list[list[float]]:
    """여러 텍스트를 한 번에 배치로 벡터화한다 - 노드가 많은 벌크 동기화에서
    모델을 텍스트 개수만큼 반복 호출하지 않기 위함."""
    prefixed = [f"passage: {t}" for t in texts]
    vecs = _get_model().encode(prefixed, normalize_embeddings=True)
    return vecs.tolist()


def embed_query(text: str) -> list[float]:
    """사용자 질의(검색어)를 벡터로 바꾼다 - embed_passage()와 다른 접두어를
    쓴다(e5 모델 관례)."""
    vec = _get_model().encode(f"query: {text}", normalize_embeddings=True)
    return vec.tolist()
