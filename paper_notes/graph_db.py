"""node_store(.md 파일)를 source of truth로 두고, 그걸 그대로 미러링한 Neo4j
그래프를 GraphRAG 검색 전용 인덱스로 쓴다. Neo4j에는 아무것도 직접 쓰지 않는다 -
항상 node_store.py를 먼저 갱신한 뒤, 그 결과를 이 모듈이 다시 Neo4j에 반영하는
방향으로만 흐른다(Supabase가 vault 백업 미러인 것과 같은 구조). 그래서
node_store.py에 이미 있는 중복 검사·불변식(entity의 paperless/paper-backed
전환 규칙 등)은 하나도 다시 안 짜도 된다 - Neo4j는 그 결과가 도착하는 곳일 뿐,
그 결과를 스스로 판단하지 않는다.

검색은 세 축을 합친 하이브리드다:
1. 그래프 트래버설(Cypher) - concept/entity/paper 사이의 실제 관계를 따라간다
2. 벡터 검색 - description/note를 임베딩(paper_notes/embeddings.py, 로컬 모델)해서
   의미가 비슷한 노드를 찾는다
3. 풀텍스트 검색 - 정확한 단어/구절이 일치하는 노드를 찾는다(벡터 검색이
   놓치기 쉬운 고유명사·약어에 강함)
Neo4j 5.11+가 이 셋을 전부 네이티브로 지원해서, 별도 벡터DB/검색엔진 없이
Neo4j 하나로 구성한다.
"""
from __future__ import annotations

import os
import threading

from paper_notes.embeddings import embed_passage, embed_query, embedding_dimension
from paper_notes.node_store import NODE_STORE_ROOT, get_user_section, list_nodes

_driver = None
_driver_lock = threading.Lock()

_LABEL_BY_TYPE = {"concept": "Concept", "entity": "Entity", "paper": "Paper"}


class Neo4jNotConfigured(RuntimeError):
    """NEO4J_URI/NEO4J_USER/NEO4J_PASSWORD가 .env에 없을 때. 그래프 검색 관련
    기능(MCP 서버)만 못 쓰게 하고, 앱의 나머지 기능(그래프 뷰, 노드 편집 등)은
    이 모듈과 무관하게 그대로 동작해야 하므로 import 시점이 아니라 실제 호출
    시점에 던진다."""


def get_driver():
    global _driver
    if _driver is None:
        with _driver_lock:
            if _driver is None:
                uri = os.getenv("NEO4J_URI")
                user = os.getenv("NEO4J_USER")
                password = os.getenv("NEO4J_PASSWORD")
                if not uri or not user or not password:
                    raise Neo4jNotConfigured(
                        "NEO4J_URI/NEO4J_USER/NEO4J_PASSWORD가 .env에 설정되어 있지 않습니다."
                    )
                import neo4j

                _driver = neo4j.GraphDatabase.driver(uri, auth=(user, password))
    return _driver


def close_driver() -> None:
    global _driver
    if _driver is not None:
        _driver.close()
        _driver = None


def ensure_schema() -> None:
    """제약조건 + 벡터 인덱스 + 풀텍스트 인덱스를 만든다. 전부 IF NOT EXISTS라
    몇 번을 다시 불러도 안전하다(멱등) - 앱 시작 시나 벌크 동기화 전에 호출."""
    dim = embedding_dimension()
    driver = get_driver()
    with driver.session() as session:
        session.run("CREATE CONSTRAINT paper_slug IF NOT EXISTS FOR (n:Paper) REQUIRE n.slug IS UNIQUE")
        session.run("CREATE CONSTRAINT concept_slug IF NOT EXISTS FOR (n:Concept) REQUIRE n.slug IS UNIQUE")
        session.run("CREATE CONSTRAINT entity_slug IF NOT EXISTS FOR (n:Entity) REQUIRE n.slug IS UNIQUE")

        for label in ("Concept", "Entity"):
            session.run(
                f"""
                CREATE VECTOR INDEX {label.lower()}_embedding IF NOT EXISTS
                FOR (n:{label}) ON (n.embedding)
                OPTIONS {{indexConfig: {{
                    `vector.dimensions`: $dim,
                    `vector.similarity_function`: 'cosine'
                }}}}
                """,
                dim=dim,
            )

        # 필드 목록이 바뀔 때마다 IF NOT EXISTS만으로는 기존 인덱스가 새 정의로
        # 안 바뀌므로, 매번 지우고 다시 만든다 - 인덱스일 뿐이라 재생성 비용이
        # 크지 않고(Neo4j가 백그라운드로 다시 채움), 항상 지금 코드의 필드 목록과
        # 실제 인덱스가 어긋나지 않게 보장하는 쪽을 택했다.
        session.run("DROP INDEX node_fulltext IF EXISTS")
        session.run(
            """
            CREATE FULLTEXT INDEX node_fulltext IF NOT EXISTS
            FOR (n:Concept|Entity) ON EACH [n.display_label, n.description, n.note, n.user_notes]
            """
        )


def _node_text(frontmatter: dict) -> str:
    """임베딩에 넣을 텍스트 - 이름/별칭/설명/메모를 합친다(설명만 넣으면 짧은
    노드는 신호가 너무 적다)."""
    parts = [
        frontmatter.get("display_label", ""),
        ", ".join(frontmatter.get("aliases") or []),
        frontmatter.get("description", ""),
        frontmatter.get("note", ""),
    ]
    return "\n".join(p for p in parts if p)


def sync_node(node_type: str, slug: str) -> None:
    """concept/entity 노드 하나를 node_store 상태 그대로 Neo4j에 반영한다.
    노드 속성(임베딩 포함)을 갱신하고, 이 노드로 들어오는 관계는 지금의 sources
    목록 기준으로 통째로 다시 만든다(하나씩 add/remove를 따라가지 않고 항상
    "현재 상태로 맞추기"만 하면 되므로, node_store.py의 각 함수가 sources를
    어떻게 바꿨는지 몰라도 항상 정확하다).

    관계 방향은 항상 "참조하는 쪽 -> 참조되는 쪽"이다: (Paper)-[:LINKED_TO]->
    (Concept|Entity), (Concept)-[:LINKED_TO]->(Entity). 즉 concept/entity
    노드 자신은 이 스키마에서 항상 관계의 **도착점**이고, sources[]가 그
    도착점으로 들어오는 관계 전부를 결정한다(concept->entity 에지조차,
    concept 자신의 sources가 아니라 그 entity의 sources[].concept_slug가
    결정한다 - 그래서 concept을 리싱크할 때 이 에지를 새로 만들 일은 없고,
    지우지도 않아야 한다). 그래서 정리 대상은 **나가는(outgoing)** 관계가
    아니라 **들어오는(incoming)** 관계다 - 반대로 지우면 sources를 다
    비워도(예: 논문 연결을 전부 끊어도) 예전 (Paper)-[:LINKED_TO]->(이 노드)
    관계가 하나도 안 지워져 Neo4j에 죽은 에지가 영구히 남는다(실제로 발견된
    버그 - orphan이 된 concept이 Neo4j에서는 계속 예전 논문과 연결된
    것처럼 보였다)."""
    label = _LABEL_BY_TYPE[node_type]
    nodes = list_nodes(NODE_STORE_ROOT, node_type)
    frontmatter = next((n for n in nodes if n["slug"] == slug), None)
    if frontmatter is None:
        delete_node_from_graph(node_type, slug)
        return

    embedding = embed_passage(_node_text(frontmatter))

    # Neo4j 속성은 출처별로 셋으로 나눈다 - description/note는 frontmatter의 값을
    # 그대로(둘 다 논문 요약 파이프라인이 채운 AI 생성 텍스트, resolve_or_create_node
    # 참고 - 서로 합치지 않고 각자 자기 이름의 속성에만 들어간다), user_notes는
    # node_store의 user-notes 섹션(사용자가 직접 쓴 원문, 또는 대화 중 add_note로
    # 덧붙여진 내용)을 별도 속성으로 둔다. 세 속성 모두 섞이지 않아야 검색 결과를
    # 읽는 쪽(Claude 등)이 어디서 나온 텍스트인지 헷갈리지 않는다.
    user_notes = get_user_section(NODE_STORE_ROOT, node_type, slug)

    driver = get_driver()
    with driver.session() as session:
        session.run(
            f"""
            MERGE (n:{label} {{slug: $slug}})
            SET n.display_label = $display_label,
                n.aliases = $aliases,
                n.description = $description,
                n.note = $note,
                n.categories = $categories,
                n.embedding = $embedding,
                n.user_notes = $user_notes
            """,
            slug=slug,
            display_label=frontmatter.get("display_label", slug),
            aliases=frontmatter.get("aliases") or [],
            description=frontmatter.get("description", ""),
            note=frontmatter.get("note", ""),
            categories=frontmatter.get("categories") or [],
            embedding=embedding,
            user_notes=user_notes,
        )

        # 이 노드로 들어오는 관계를 전부 지우고 현재 sources로 다시 만든다 -
        # 증분 동기화보다 훨씬 단순하고, 어떤 순서로 호출되든 항상 올바르다.
        session.run(f"MATCH ()-[r:LINKED_TO]->(n:{label} {{slug: $slug}}) DELETE r", slug=slug)

        # concept_slug가 가리키는 concept이 이미 지워졌을 수 있다(예: 사용자가
        # concept만 지우고 entity는 남겨둔 경우) - graph_builder.py도 이때
        # "논문에 직접 연결"로 취급하므로(죽은 참조로 그래프에서 사라지지 않게),
        # 여기서도 같은 기준(지금 실제로 존재하는 concept slug 집합)으로 판단해야
        # 그래프 뷰와 Neo4j 미러가 어긋나지 않는다.
        valid_concept_slugs = (
            {n["slug"] for n in list_nodes(NODE_STORE_ROOT, "concept")} if node_type == "entity" else set()
        )

        for source in frontmatter.get("sources") or []:
            paper_slug = source.get("slug")
            concept_slug = source.get("concept_slug") if node_type == "entity" else None
            if concept_slug and concept_slug not in valid_concept_slugs:
                concept_slug = None
            if concept_slug:
                session.run(
                    """
                    MATCH (c:Concept {slug: $concept_slug})
                    MATCH (e:Entity {slug: $entity_slug})
                    MERGE (c)-[:LINKED_TO]->(e)
                    """,
                    concept_slug=concept_slug,
                    entity_slug=slug,
                )
            elif paper_slug:
                session.run(
                    f"""
                    MATCH (p:Paper {{slug: $paper_slug}})
                    MATCH (n:{label} {{slug: $slug}})
                    MERGE (p)-[:LINKED_TO]->(n)
                    """,
                    paper_slug=paper_slug,
                    slug=slug,
                )


def sync_paper(slug: str, title: str, tags: list[str] | None = None) -> None:
    driver = get_driver()
    with driver.session() as session:
        session.run(
            "MERGE (p:Paper {slug: $slug}) SET p.title = $title, p.tags = $tags",
            slug=slug,
            title=title,
            tags=tags or [],
        )


def delete_node_from_graph(node_type: str, slug: str) -> None:
    label = _LABEL_BY_TYPE[node_type]
    driver = get_driver()
    with driver.session() as session:
        session.run(f"MATCH (n:{label} {{slug: $slug}}) DETACH DELETE n", slug=slug)


def full_resync(vault_path: str) -> dict:
    """지금 vault + node_store 전체 상태로 Neo4j를 처음부터 다시 만든다(1회성
    벌크 동기화, 또는 드리프트가 의심될 때 재실행하는 안전한 재구축). 기존
    데이터를 전부 지우고 다시 쓰므로 매번 최종 상태와 정확히 일치한다."""
    from pathlib import Path

    ensure_schema()
    driver = get_driver()
    with driver.session() as session:
        session.run("MATCH (n) DETACH DELETE n")

    paper_count = 0
    autonote_dir = Path(vault_path) / "AutoNote"
    if autonote_dir.is_dir():
        import yaml

        for folder in sorted(autonote_dir.iterdir()):
            if not folder.is_dir():
                continue
            slug = folder.name
            md_path = folder / f"{slug}.md"
            if not md_path.is_file():
                continue
            text = md_path.read_text(encoding="utf-8")
            frontmatter = {}
            if text.startswith("---"):
                end = text.find("\n---", 3)
                if end != -1:
                    frontmatter = yaml.safe_load(text[3:end]) or {}
            tags = frontmatter.get("tags") or []
            if isinstance(tags, str):
                tags = [tags]
            sync_paper(slug, frontmatter.get("title") or slug, tags)
            paper_count += 1

    concept_count = 0
    for n in list_nodes(NODE_STORE_ROOT, "concept"):
        sync_node("concept", n["slug"])
        concept_count += 1

    entity_count = 0
    for n in list_nodes(NODE_STORE_ROOT, "entity"):
        sync_node("entity", n["slug"])
        entity_count += 1

    return {"papers": paper_count, "concepts": concept_count, "entities": entity_count}


_RRF_K = 60  # 정보검색에서 흔히 쓰는 상수(Cormack et al.) - 순위 1~2위 근처의 비중을
             # 과하게 키우지 않으면서도 상위권을 충분히 우대하는 완만한 감쇠를 준다.


def _reciprocal_rank_fusion(*ranked_lists: list[dict]) -> list[dict]:
    """벡터 검색(코사인 유사도, 0~1)과 풀텍스트 검색(Lucene 점수, 상한 없음)은
    점수 스케일이 완전히 달라서 그냥 숫자로 비교하면 풀텍스트 쪽이 항상
    이겨버린다(관련도와 무관하게 스케일이 커서). 대신 각 랭커 안에서의 "순위"만
    보고 합치는 RRF(Reciprocal Rank Fusion)를 쓴다 - 두 검색 방식이 서로 다른
    걸 잘 찾아내는 상황(벡터는 의미 유사, 풀텍스트는 정확한 단어/약어)에서
    표준적으로 쓰이는 병합 방식이다."""
    fused: dict[tuple[str, str], dict] = {}
    rrf_scores: dict[tuple[str, str], float] = {}
    for ranked in ranked_lists:
        for rank, hit in enumerate(ranked, start=1):
            key = (hit["type"], hit["slug"])
            rrf_scores[key] = rrf_scores.get(key, 0.0) + 1.0 / (_RRF_K + rank)
            fused.setdefault(key, hit)
    for key, hit in fused.items():
        hit["score"] = rrf_scores[key]
    return sorted(fused.values(), key=lambda h: h["score"], reverse=True)


def search(query: str, top_k: int = 10) -> list[dict]:
    """하이브리드 그래프 검색 - 벡터 유사도 + 풀텍스트 매치로 시드 노드를 찾고,
    각 시드에서 1촌 이웃까지 같이 반환한다(그래프 확장). GraphRAG의 핵심
    함수 - MCP의 search_graph 툴이 이걸 그대로 노출한다."""
    driver = get_driver()
    query_vec = embed_query(query)

    with driver.session() as session:
        # db.index.vector.queryNodes는 최신 Neo4j에서 새 SEARCH 문법으로 대체
        # 예정이라는 지원중단 경고를 내지만, 아직 정상 동작한다(경고일 뿐 오류
        # 아님) - 문법이 안정화되면 그때 옮긴다.
        vector_hits = session.run(
            """
            CALL db.index.vector.queryNodes('concept_embedding', $k, $vec) YIELD node, score
            RETURN node.slug AS slug, labels(node)[0] AS type, node.display_label AS label, score
            ORDER BY score DESC
            UNION
            CALL db.index.vector.queryNodes('entity_embedding', $k, $vec) YIELD node, score
            RETURN node.slug AS slug, labels(node)[0] AS type, node.display_label AS label, score
            ORDER BY score DESC
            """,
            k=top_k,
            vec=query_vec,
        ).data()
        vector_hits.sort(key=lambda h: h["score"], reverse=True)

        fulltext_hits = session.run(
            """
            CALL db.index.fulltext.queryNodes('node_fulltext', $q) YIELD node, score
            RETURN node.slug AS slug, labels(node)[0] AS type, node.display_label AS label, score
            ORDER BY score DESC
            LIMIT $k
            """,
            q=query,
            k=top_k,
        ).data()

        top_seeds = _reciprocal_rank_fusion(vector_hits, fulltext_hits)[:top_k]

        results = []
        for seed in top_seeds:
            neighbors = session.run(
                f"""
                MATCH (n:{seed['type']} {{slug: $slug}})
                OPTIONAL MATCH (n)-[:LINKED_TO]-(neighbor)
                RETURN neighbor.slug AS slug, labels(neighbor)[0] AS type, neighbor.display_label AS label
                """,
                slug=seed["slug"],
            ).data()
            results.append(
                {
                    "slug": seed["slug"],
                    "type": seed["type"],
                    "label": seed["label"],
                    "score": seed["score"],
                    "neighbors": [n for n in neighbors if n["slug"]],
                }
            )
        return results
