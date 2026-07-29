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
                "다이어그램에 들어갈 핵심 개념 노드. 논문의 핵심 파이프라인/구조를 "
                "나타내는 6~8개 정도로 제한할 것 (세부 디테일은 노트 본문에 있으므로 "
                "다이어그램은 큰 흐름만 보여준다)."
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
        output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
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
