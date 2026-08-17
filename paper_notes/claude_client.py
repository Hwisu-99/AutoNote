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


# 예전엔 (1) 초록/서론/결론만 보고 concept을 먼저 확정 -> (2) 전문 + 확정 concept으로
# entity/요약을 뽑는 2콜 구조였다. 1차 호출이 좁은 시야로 concept을 못박아버리면 2차
# 호출은 그 틀 안에서만 근거를 찾게 되는 top-down 편향이 있었고, 호출 두 번은 대량
# 처리 시 비용도 배로 든다. 원문 전체를 한 번에 보고 판단하는 1콜 구조로 바꿨다 -
# 구조 파싱(front matter 추출)과 md 렌더링은 원래도 코드가 하고 있었으니, 이번 변경으로
# 파이프라인에서 LLM 과금이 발생하는 지점은 이 호출 하나뿐이다.
SYSTEM_PROMPT = (
    "당신은 학술 논문을 읽고, 처음 보는 사람도 논문의 전체 흐름·핵심 주장·사용 기술을 "
    "한 번에 파악할 수 있는 구조화된 서사형 요약과 지식 그래프(concept/entity/relationship) "
    "를 만드는 연구 어시스턴트입니다.\n\n"
    "반드시 지킬 원칙:\n"
    "1. 주어지는 논문 원문 전체를 근거로 판단하십시오. 초록·서론·결론 같은 일부 섹션만 "
    "보고 성급히 결론짓지 말고, 본문 전체를 스캔해 핵심 개념·주장·근거를 찾으십시오.\n"
    "2. 모든 주장·수치는 원문에 실제로 있는 내용이어야 합니다. 추측하거나 지어내지 "
    "마십시오. 비유나 풀어쓴 설명은 원문에 없는 표현이어도 괜찮지만, 그 설명이 가리키는 "
    "사실관계(무엇이 무엇과 동치인지, 어떤 수치인지 등) 자체는 원문과 일치해야 합니다.\n"
    "3. 결과는 표·불릿 위주로 스캔하기 쉽게 작성하십시오. 긴 줄글 문단은 피하십시오.\n"
    "4. 한국어로 작성하되 고유명사(모델명·기법명·저자명)는 원문 그대로 영문을 유지하십시오."
)

# 문제-해결 서사(긴장 조성 -> 반전) + 압축 문장의 절 단위 분해를 결합한 구조.
# 서사가 "왜 궁금해야 하는지"를 만들고, 압축 문장(core_insight)이 "정답이 뭔지" 짚고,
# 절 단위 분해가 그 정답의 각 부분이 흐릿하게 안 넘어가게 잡아준다. 원문 인용
# 검증(analogy_is_original/source_quote)은 출력 토큰을 아끼려고 뺐다 - 절 단위
# 설명은 짧고 구체적이라 애초에 원문을 통째로 베낄 필요가 적다.
_DEEP_DIVE_SCHEMA = {
    "type": "object",
    "properties": {
        "setup": {
            "type": "string",
            "description": (
                "문제-해결 서사의 도입부. 이 개념이 나오기 전 어떤 긴장/대립이 있었는지 "
                "(예: 두 갈래 길, 상충하는 두 접근법) 2~4문장으로 이야기처럼. 전문용어 최소화."
            ),
        },
        "core_insight": {
            "type": "string",
            "description": (
                "이 개념의 핵심을 압축한 단 하나의 문장 - 서사의 '반전/발견' 지점. 반드시 1문장. "
                "insight_breakdown에서 각각 풀릴 핵심 어구를 최대 3개로 구성할 것(어구가 4개 "
                "이상 필요하면 문장을 더 압축해서 3개로 줄일 것)."
            ),
        },
        "insight_breakdown": {
            "type": "array",
            "description": (
                "core_insight을 절 단위로 쪼개 각각 쉬운 말로 풀이. 반드시 3개 이하로만 - "
                "core_insight 자체를 절 3개로 구성했으니 이 배열도 항목이 3개를 넘으면 안 됨."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "clause": {"type": "string", "description": "core_insight에서 그대로 가져온 구절."},
                    "explanation": {
                        "type": "string",
                        "description": (
                            "그 구절의 의미를 그 구절 안의 전문용어를 재사용하지 않고 1~2문장으로. "
                            "필요하면 아주 작은 숫자 예시를 포함해도 됨."
                        ),
                    },
                },
                "required": ["clause", "explanation"],
                "additionalProperties": False,
            },
        },
        "why_it_matters": {
            "type": "string",
            "description": "이걸 모르면 논문의 어떤 핵심 주장/뒷 섹션을 이해할 수 없는지. 1~2문장.",
        },
    },
    "required": ["setup", "core_insight", "insight_breakdown", "why_it_matters"],
    "additionalProperties": False,
}

SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "authors": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "source_meta": {
            "type": "string",
            "description": (
                "학회/저널명, arXiv ID, 발행 연도 등 원문에서 실제로 확인 가능한 서지 정보를 "
                "한 줄로 (예: 'NeurIPS 2024' 또는 'arXiv:2405.21060, 2024'). 원문에서 확인할 "
                "수 없으면 빈 문자열."
            ),
        },
        "tldr": {
            "type": "string",
            "description": (
                "1~2문장. \"무엇을 왜 어떻게 해결했는지\"가 다 들어가야 한다. 이 논문의 "
                "핵심을 재진술하지 못한다면 실패한 것."
            ),
        },
        "problem_motivation": {
            "type": "array",
            "description": (
                "이 논문 이전에 어떤 문제/한계가 있었는지, 이 논문이 뭘 다르게 시도하는지를 "
                "3~5개의 짧은 불릿으로."
            ),
            "items": {"type": "string"},
        },
        "claims": {
            "type": "array",
            "description": (
                "논문이 스스로 나열한 핵심 기여(보통 Introduction 끝부분에 있음). 지어내지 "
                "말고 본문에 실제로 있는 주장만 포함."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "claim": {"type": "string"},
                    "evidence": {
                        "type": "string",
                        "description": "이 주장을 뒷받침하는 절 번호/표/수치 (예: '§4.2, Table 3 - baseline 대비 +5.3%').",
                    },
                },
                "required": ["claim", "evidence"],
                "additionalProperties": False,
            },
        },
        "concepts": {
            "type": "array",
            "description": (
                "이 논문이 스스로 제시하는 핵심 개념만 3~8개. 논문이 해결하려는 핵심 문제, "
                "독자적으로 제안하는 아키텍처/방법론, 주요 기여점만 포함할 것. 기존 연구/"
                "베이스라인/관련 연구에서 유래한 개념은 아무리 비중 있게 다뤄져도 절대 포함하지 "
                "말 것(그건 entities로 처리한다). 각 label은 원문 표현을 최대한 그대로 사용할 것."
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
                    "aliases": {
                        "type": "array",
                        "description": (
                            "이 개념과 정확히 같은 대상을 가리키는 다른 표기·약어만 (예: "
                            "'Self-Attention'과 'Self-Attention Mechanism'처럼 완전히 동일한 "
                            "지시 대상의 표기 차이). 상위 개념, 하위 개념, 범위가 다른 유사 "
                            "개념, 관련 기법은 절대 포함하지 말 것. 논문에 실제로 등장하는 "
                            "표현만 쓰고, 애매하면 빈 배열로 둘 것. 최대 3개."
                        ),
                        "items": {"type": "string"},
                    },
                    "description": {
                        "type": "string",
                        "description": "이 개념이 무엇인지 한 줄 설명 (압축 레퍼런스 표용).",
                    },
                    "note": {
                        "type": "string",
                        "description": (
                            "레퍼런스 표의 '비고' 칸에 들어갈 보충 설명(예: 기존 방식과의 핵심 "
                            "차이, 특이사항). 없으면 빈 문자열."
                        ),
                    },
                    "deep_dive": {
                        "anyOf": [_DEEP_DIVE_SCHEMA, {"type": "null"}],
                        "description": (
                            "이 concept이 \"이 논문이 독자적으로 제안했고, 이해하지 못하면 뒤 "
                            "내용(다른 핵심 주장/결과)을 이해할 수 없는\" 핵심 개념 3~5개 중 "
                            "하나로 선정됐을 때만 채울 것 - 모든 concept을 다 풀면 문서가 "
                            "산만해지므로 정말 중요한 것만 고를 것. 나머지 concept은 null."
                        ),
                    },
                },
                "required": ["id", "label", "category", "aliases", "description", "note", "deep_dive"],
                "additionalProperties": False,
            },
        },
        "entities": {
            "type": "array",
            "description": (
                "논문 본문 전체를 스캔해서, 주어진 concept들을 뒷받침/구체화하는 세부 "
                "모듈·알고리즘·데이터셋·평가지표·하이퍼파라미터, 그리고 이 논문이 비교·개선 "
                "대상으로 삼는 선행 연구의 개념·모델·프레임워크명을 최대 20개까지 뽑을 것 "
                "(논문에 그만큼 없다면 억지로 채우지 말 것). 각 label은 논문 원문에 실제로 "
                "등장하는 표현만 사용하고 지어내거나 일반화하지 말 것. concept_id를 지정하면 "
                "주어진 concept 목록의 해당 id와 연결되고, 특정 concept에 속하지 않는 독립적인 "
                "용어면 concept_id를 null로 둘 것."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string", "description": "15자 이내로 짧게"},
                    "concept_id": {
                        "anyOf": [{"type": "string"}, {"type": "null"}],
                        "description": "주어진 concept 목록의 id 중 하나. 없으면 null",
                    },
                    "aliases": {
                        "type": "array",
                        "description": (
                            "이 entity와 정확히 같은 대상을 가리키는 다른 표기·약어만(완전히 "
                            "동일한 지시 대상의 표기 차이만, 상위/하위/유사 개념은 제외). 논문에 "
                            "실제로 등장하는 표현만. 애매하면 빈 배열. 최대 3개."
                        ),
                        "items": {"type": "string"},
                    },
                    "description": {
                        "type": "string",
                        "description": "한 줄 설명 (압축 레퍼런스 표용).",
                    },
                    "note": {
                        "type": "string",
                        "description": "레퍼런스 표의 '비고' 칸에 들어갈 보충 설명. 없으면 빈 문자열.",
                    },
                },
                "required": ["label", "concept_id", "aliases", "description", "note"],
                "additionalProperties": False,
            },
        },
        "relationships": {
            "type": "array",
            "description": (
                "개념 노드 간의 관계 (화살표). 논리적 흐름(입력→처리→결과) 순서를 반영할 "
                "것. from_id/to_id는 위 concepts 목록의 id만 사용할 것."
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
        "results": {
            "type": "array",
            "description": (
                "평가/실험 결과를 논문의 실제 섹션 구성에 맞춰 여러 하위 섹션으로 나눠 "
                "기술할 것(예: 벤치마크 카테고리별 표, 사례 연구, 비용 효율 등 - 논문마다 "
                "구성이 다를 수 있으니 억지로 통일하지 말 것). 각 항목은 소제목 + 마크다운 "
                "본문(표나 불릿, 자유형식)."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "section_title": {"type": "string"},
                    "content_markdown": {
                        "type": "string",
                        "description": "마크다운 표나 불릿으로 작성. 원문에 있는 수치만 사용.",
                    },
                },
                "required": ["section_title", "content_markdown"],
                "additionalProperties": False,
            },
        },
        "limitations": {
            "type": "string",
            "description": (
                "논문이 스스로 인정했거나 데이터로 드러낸 한계만. 별도 Limitations 절이 "
                "없으면 '명시적 절 없음, 아래는 각 섹션에 흩어진 내용을 종합'이라고 밝힐 것. "
                "추측성 한계를 지어내지 말 것."
            ),
        },
    },
    "required": [
        "title", "authors", "tags", "source_meta", "tldr", "problem_motivation",
        "claims", "concepts", "entities", "relationships", "results",
        "limitations",
    ],
    "additionalProperties": False,
}

async def summarize_paper(paper_text: str) -> tuple[dict, float]:
    """논문 전문을 한 번에 Claude에 넣어 구조화된 서사형 요약을 만들고, (요약 결과, API
    비용(USD))을 반환한다. 원문 텍스트 블록에 프롬프트 캐싱을 걸어둬서, 같은 논문으로
    프롬프트/스키마를 바꿔가며 반복 테스트할 때 원문 재과금을 줄인다 - 단, 이 캐시는
    system 프롬프트보다 뒤에 있는 블록이라 SYSTEM_PROMPT 자체를 바꾸면 캐시가 깨진다."""
    client = anthropic.AsyncAnthropic()

    response = await client.messages.create(
        model=MODEL,
        max_tokens=16000,
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
                "content": [
                    {
                        "type": "text",
                        "text": f"[논문 전문]\n{paper_text}",
                        "cache_control": {"type": "ephemeral"},
                    },
                    {
                        "type": "text",
                        "text": (
                            "위 논문 전문을 분석해서 구조화된 요약을 만들어줘. system 프롬프트의 "
                            "원칙(원문 근거만 사용, 근거 없는 보충설명은 표시, 표/불릿 위주)을 "
                            "반드시 지켜."
                        ),
                    },
                ],
            }
        ],
    )

    text = next(b.text for b in response.content if b.type == "text")
    summary = json.loads(text)
    cost_usd = _calculate_cost_usd(response.usage, response.model)

    return summary, cost_usd
