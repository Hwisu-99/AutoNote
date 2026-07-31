from __future__ import annotations

import json

import anthropic

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
        "concepts": {
            "type": "array",
            "description": (
                "다이어그램에 들어갈 핵심 개념 노드. 먼저 Abstract/Introduction/Conclusion에서 "
                "이 논문이 스스로 제시하는 핵심 개념 후보를 파악하고, 그 다음 본문 전체"
                "(Method/Experiments 등)에서 그 후보들이 실제로 반복적으로 다뤄지는지 확인한 "
                "뒤 최종 목록을 정할 것. 반드시 이 논문이 새로 제시/기여하는 것만 포함하고, "
                "이 논문이 비교하거나 개선하는 대상인 선행 연구의 개념·모델·프레임워크 자체는 "
                "concept에 넣지 말고 entities 배열에 넣을 것 (예: 이 논문이 개선하는 이전 "
                "모델 이름은 concept이 아니라 entity). 각 label은 논문 원문에 실제로 등장하는 "
                "표현을 최대한 그대로 사용하고, 지어내거나 과도하게 의역하지 말 것. 6~8개 "
                "정도로 제한."
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
        "relationships": {
            "type": "array",
            "description": "개념 노드 간의 관계 (화살표). 논리적 흐름(입력→처리→결과) 순서를 반영할 것.",
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
                "논문에 등장하는 구체적인 용어/기법/모델명 등 세부 단어 노드. concepts에서 "
                "제외된, 이 논문이 비교·개선 대상으로 삼는 선행 연구의 개념·모델·프레임워크명도 "
                "반드시 여기 포함할 것 — 그 개념을 다루는 다른 논문과의 연결고리 역할을 하므로 "
                "빠뜨리면 안 된다. 그 외 구체적 용어/기법/데이터셋/벤치마크명도 포함하되, "
                "반드시 논문 원문에 실제로 등장하는 표현만 사용하고 지어내거나 일반화하지 "
                "말 것. 최대 20개까지 허용하되, 논문에 그만큼 없다면 억지로 채우지 말 것. "
                "concept_id를 지정하면 concepts 배열의 해당 개념과 연결되고, 특정 개념에 "
                "속하지 않는 독립적인 용어면 concept_id를 null로 둘 것."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string", "description": "15자 이내로 짧게"},
                    "concept_id": {
                        "anyOf": [{"type": "string"}, {"type": "null"}],
                        "description": "concepts 배열의 id 중 하나. 없으면 null",
                    },
                },
                "required": ["label", "concept_id"],
                "additionalProperties": False,
            },
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
        "concepts",
        "relationships",
        "entities",
    ],
    "additionalProperties": False,
}


async def summarize_paper(paper_text: str) -> tuple[dict, float]:
    """논문을 요약하고 (요약 결과, 이번 호출의 API 비용(USD))를 반환한다."""
    client = anthropic.AsyncAnthropic()

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
                    "다음 논문 전문을 분석해서 구조화된 요약을 만들어줘.\n\n"
                    f"{paper_text}"
                ),
            }
        ],
    )

    text = next(b.text for b in response.content if b.type == "text")
    summary = json.loads(text)
    cost_usd = _calculate_cost_usd(response.usage, response.model)
    return summary, cost_usd
