from __future__ import annotations

import json

import anthropic

from paper_notes.extractor import extract_front_matter

MODEL = "claude-sonnet-5"

# USD per 1M tokens (input, output). claude-sonnet-5 uses intro pricing
# (active through 2026-08-31) — update to (3.00, 15.00) after that date.
# Keyed by model so changing MODEL above keeps the cost display accurate.
_PRICE_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-fable-5": (10.00, 50.00),
    "claude-mythos-5": (10.00, 50.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-opus-4-7": (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),  # intro price through 2026-08-31
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
}


def _calculate_cost_usd(usage, model: str) -> float:
    prices = _PRICE_PER_MTOK.get(model)
    if prices is None:
        raise ValueError(
            f"'{model}'의 단가가 _PRICE_PER_MTOK에 없습니다. claude-api 스킬의 가격표를 보고 추가하세요."
        )
    input_per_token, output_per_token = (p / 1_000_000 for p in prices)
    cache_write_per_token = input_per_token * 1.25
    cache_read_per_token = input_per_token * 0.1

    return (
        usage.input_tokens * input_per_token
        + usage.output_tokens * output_per_token
        + (usage.cache_creation_input_tokens or 0) * cache_write_per_token
        + (usage.cache_read_input_tokens or 0) * cache_read_per_token
    )


SYSTEM_PROMPT = (
    "You are a research assistant that reads academic papers and produces a "
    "structured, faithful summary in Korean. Be precise and avoid inventing "
    "details not present in the text."
)

# 1차 호출: Abstract/Introduction/Conclusion/Figure caption만 보고 이 논문이
# 스스로 제시하는 핵심 concept만 뽑는다. 본문 전체를 아직 안 보여주기 때문에
# 선행 연구/비교 대상 개념(예: 이 논문이 개선하는 이전 모델)이 애초에 concept
# 후보로 뽑힐 일이 없다 — "concept에서 제외하라"는 지시보다 훨씬 확실하다.
CONCEPT_SYSTEM_PROMPT = (
    "당신은 지식 그래프(Knowledge Graph) 구축을 위한 논문 분석 전문가입니다. "
    "전달된 텍스트는 논문의 초반부(Abstract/Summary/Overview, "
    "Introduction/Background)와 후반부(Conclusion/Discussion/Concluding Remarks), "
    "그리고 Figure 캡션으로 구성되어 있습니다. 학술지·컨퍼런스 템플릿마다 "
    "소제목 표기가 달라 텍스트에 붙은 ## 레이블이 정확하지 않을 수 있으니, "
    "레이블 자체보다 내용을 보고 이 논문이 스스로 제시하는 핵심 concept만 "
    "판단하십시오."
)

CONCEPT_SCHEMA = {
    "type": "object",
    "properties": {
        "concepts": {
            "type": "array",
            "description": (
                "이 논문이 스스로 제시하는 핵심 개념만 3~7개. 논문이 해결하려는 "
                "핵심 문제, 독자적으로 제안하는 아키텍처/방법론, 주요 기여점만 "
                "포함할 것. 기존 연구/베이스라인/관련 연구에서 유래한 개념은 "
                "아무리 비중 있게 다뤄져도 절대 포함하지 말 것(그건 다음 단계에서 "
                "entity로 처리된다). Figure 캡션이나 소제목에 언급되지 않고 "
                "단발성으로만 등장하는 개념도 제외할 것. 각 label은 원문 표현을 "
                "최대한 그대로 사용할 것."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "label": {"type": "string", "description": "20자 이내로 짧게"},
                    "category": {
                        "type": "string",
                        "enum": ["input", "process", "result", "limitation", "other"],
                        "description": (
                            "input=데이터/입력, process=방법론/모델 구성요소, "
                            "result=결과/성과, limitation=한계/향후과제, other=기타"
                        ),
                    },
                },
                "required": ["id", "label", "category"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["concepts"],
    "additionalProperties": False,
}

# 2차 호출: 논문 전문 + 1차에서 확정된 concept 목록을 보고 요약 본문 전체와
# entity를 뽑는다. 동시에 본문 전체 기준으로 각 concept이 실제로 소제목/반복
# 언급 등으로 뒷받침되는지 재검증해서, 근거가 약한 concept은
# demoted_concept_ids로 표시한다 — 이 목록에 오른 concept은 claude_client에서
# entity로 강등 처리한다.
SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "authors": {"type": "string"},
        "one_line_summary": {
            "type": "string",
            "description": (
                "TLDR 스타일. 15단어(한국어 기준 약 25자) 이내로 극단적으로 압축된 "
                "한 문장. 이 논문의 핵심을 재진술하지 못한다면 실패한 것."
            ),
        },
        "problem": {"type": "string", "description": "이 논문이 다루는 문제"},
        "gap": {
            "type": "string",
            "description": "기존 연구/방법들이 이 문제를 왜, 어떤 지점에서 풀지 못했는가 (연구 공백)",
        },
        "key_idea": {
            "type": "string",
            "description": (
                "이 논문의 핵심 통찰을 1~2문장으로. method의 세부 구현이 아니라 "
                "\"무엇을 다르게 생각했는가\"에 해당하는 상위 레벨 아이디어."
            ),
        },
        "method": {"type": "string", "description": "제안하는 방법론"},
        "key_contributions": {"type": "array", "items": {"type": "string"}},
        "results": {"type": "string"},
        "limitations": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "relationships": {
            "type": "array",
            "description": (
                "개념 노드 간의 관계 (화살표). 논리적 흐름(입력→처리→결과) 순서를 "
                "반영할 것. from_id/to_id는 위에서 주어진 확정 concept 목록의 id만 사용할 것."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "from_id": {"type": "string"},
                    "to_id": {"type": "string"},
                    "label": {"type": "string", "description": "10자 이내로 짧게"},
                },
                "required": ["from_id", "to_id"],
                "additionalProperties": False,
            },
        },
        "entities": {
            "type": "array",
            "description": (
                "논문 본문 전체를 스캔해서, 주어진 concept들을 뒷받침/구체화하는 세부 "
                "모듈·알고리즘·데이터셋·평가지표·하이퍼파라미터, 그리고 이 논문이 "
                "비교·개선 대상으로 삼는 선행 연구의 개념·모델·프레임워크명을 최대 "
                "20개까지 뽑을 것 (논문에 그만큼 없다면 억지로 채우지 말 것). 각 "
                "label은 논문 원문에 실제로 등장하는 표현만 사용하고 지어내거나 "
                "일반화하지 말 것. concept_id를 지정하면 주어진 concept 목록의 해당 "
                "id와 연결되고, 특정 concept에 속하지 않는 독립적인 용어면 "
                "concept_id를 null로 둘 것."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string", "description": "15자 이내로 짧게"},
                    "concept_id": {
                        "anyOf": [{"type": "string"}, {"type": "null"}],
                        "description": "주어진 concept 목록의 id 중 하나. 없으면 null",
                    },
                },
                "required": ["label", "concept_id"],
                "additionalProperties": False,
            },
        },
        "demoted_concept_ids": {
            "type": "array",
            "description": (
                "주어진 concept 목록 중, 본문 전체를 확인했을 때 소제목으로 쓰이지 "
                "않거나 언급 비중이 미미해서 핵심 concept 자격이 없다고 판단되는 "
                "concept의 id 목록. 없으면 빈 배열."
            ),
            "items": {"type": "string"},
        },
    },
    "required": [
        "title",
        "authors",
        "one_line_summary",
        "problem",
        "gap",
        "key_idea",
        "method",
        "key_contributions",
        "results",
        "limitations",
        "tags",
        "relationships",
        "entities",
        "demoted_concept_ids",
    ],
    "additionalProperties": False,
}


async def _extract_concepts(client: anthropic.AsyncAnthropic, paper_text: str) -> tuple[list[dict], float]:
    front_matter = extract_front_matter(paper_text)
    response = await client.messages.create(
        model=MODEL,
        max_tokens=2000,
        system=CONCEPT_SYSTEM_PROMPT,
        output_config={"format": {"type": "json_schema", "schema": CONCEPT_SCHEMA}, "effort": "medium"},
        messages=[{"role": "user", "content": front_matter}],
    )
    text = next(b.text for b in response.content if b.type == "text")
    concepts = json.loads(text)["concepts"]
    cost_usd = _calculate_cost_usd(response.usage, response.model)
    return concepts, cost_usd


async def summarize_paper(paper_text: str) -> tuple[dict, float]:
    """논문을 요약하고 (요약 결과, 이번 호출들의 총 API 비용(USD))를 반환한다.

    1차 호출로 Abstract/Intro/Conclusion/Figure caption만 보고 핵심 concept을
    먼저 확정한 뒤, 2차 호출에서 논문 전문 + 확정된 concept 목록을 보고 나머지
    요약/entity를 뽑으면서 concept이 실제로 본문에서 뒷받침되는지 재검증한다."""
    client = anthropic.AsyncAnthropic()

    concepts, concept_cost = await _extract_concepts(client, paper_text)
    concepts_json = json.dumps(concepts, ensure_ascii=False)

    response = await client.messages.create(
        model=MODEL,
        max_tokens=8000,
        system=SYSTEM_PROMPT,
        # effort를 지정하지 않으면 claude-sonnet-5는 기본값(high)으로 adaptive
        # thinking을 돌려 구조화 요약 작업치고 불필요한 thinking 토큰 비용이 붙는다.
        # medium으로 낮춰 비용을 줄이되, 품질 저하가 보이면 다시 올릴 것.
        output_config={
            "format": {"type": "json_schema", "schema": SCHEMA},
            "effort": "medium",
        },
        messages=[
            {
                "role": "user",
                "content": (
                    "다음 논문 전문을 분석해서 구조화된 요약을 만들어줘. 아래는 이미 "
                    "확정된 핵심 concept 목록이니 참고해서 entity를 연결하고, "
                    "본문 근거가 약한 concept이 있으면 demoted_concept_ids로 표시해줘.\n\n"
                    f"[확정된 concept 목록]\n{concepts_json}\n\n"
                    f"[논문 전문]\n{paper_text}"
                ),
            }
        ],
    )

    text = next(b.text for b in response.content if b.type == "text")
    summary = json.loads(text)
    main_cost = _calculate_cost_usd(response.usage, response.model)

    demoted_ids = set(summary.pop("demoted_concept_ids", []))
    final_concepts = [c for c in concepts if c["id"] not in demoted_ids]
    demoted_labels = [c["label"] for c in concepts if c["id"] in demoted_ids]

    entities = summary.get("entities", [])
    existing_entity_labels = {e["label"] for e in entities}
    for label in demoted_labels:
        if label not in existing_entity_labels:
            entities.append({"label": label, "concept_id": None})

    summary["concepts"] = final_concepts
    summary["entities"] = entities
    summary["relationships"] = [
        r
        for r in summary.get("relationships", [])
        if r["from_id"] not in demoted_ids and r["to_id"] not in demoted_ids
    ]

    return summary, concept_cost + main_cost
