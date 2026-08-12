"""concept/entity 라벨 중복 제거: 정규화 매치 + MinHash 근사 유사도.

같은 개념이 논문마다 "Self-Attention", "self attention", "Self Attention Mechanism"처럼
표기가 갈리면 지금까지는 완전히 별개인 그래프 노드로 쪼개졌다. 이를 두 단계로 병합한다:

1. 정규화 후 완전히 같은 문자열 -> 즉시 병합 (대소문자/공백/구두점 차이만 있는 경우)
2. 정규화 후에도 다른 문자열 -> 3-gram 문자 집합의 MinHash로 자카드 유사도를 추정해
   threshold 이상이면 병합 (예: "self attention" vs "self attention mechanism")

MinHash 설계는 graphify(github.com/Graphify-Labs/graphify)의 dedup 파이프라인을 참고했다.
다만 graphify는 대형 코드베이스(수천 개 심볼)를 다루므로 MinHashLSH로 후보를 좁힌 뒤
비교하지만, AutoNote는 논문 vault 하나에 개념 수가 많아야 수백 개 수준이라 LSH 버킷
인덱싱 없이 정규화된 라벨 전체를 pairwise로 직접 비교한다 - 이 규모에서는 O(n^2)이
문제되지 않고, 근사 정확도 손실도 피할 수 있다.
"""
from __future__ import annotations

import functools
import random
import re
import unicodedata
import zlib

_NUM_PERM = 64
_SHINGLE_SIZE = 3
_MERSENNE_PRIME = (1 << 61) - 1
_MAX_HASH = (1 << 32) - 1
_DEFAULT_THRESHOLD = 0.82

_NUMERIC_RE = re.compile(r"\d+")


def normalize_label(label: str) -> str:
    """대소문자/공백/구두점 차이를 흡수하는 정규화 키.

    NFKC로 유니코드 형태를 통일하고 영숫자가 아닌 문자를 공백 하나로 뭉갠 뒤
    casefold한다. 노드 ID에 쓰기 안전한 문자만 남기면서, 표기만 다른 같은 개념을
    이 단계에서 바로 같은 키로 합친다.
    """
    s = unicodedata.normalize("NFKC", label)
    s = re.sub(r"[^\w]+", " ", s, flags=re.UNICODE)
    return re.sub(r"\s+", " ", s).strip().casefold()


def _shingles(text: str, k: int = _SHINGLE_SIZE) -> set[str]:
    if len(text) < k:
        return {text} if text else set()
    return {text[i : i + k] for i in range(len(text) - k + 1)}


@functools.lru_cache(maxsize=None)
def _hash_permutations(num_perm: int, seed: int = 1) -> list[tuple[int, int]]:
    """MinHash용 유니버설 해시 함수 계수 (a, b) num_perm개를 만든다. seed 고정으로
    같은 입력에 대해 항상 같은 서명이 나오게 한다(재현 가능성). 결과가 (num_perm,
    seed)에만 의존하는 순수 함수라 lru_cache로 캐싱한다 - labels_match()처럼
    단건 비교를 대량으로 반복하는 호출부에서 매번 이 순열을 처음부터 다시 만드는
    비용이 실제로 체감될 만큼 컸다."""
    rng = random.Random(seed)
    return [
        (rng.randint(1, _MERSENNE_PRIME - 1), rng.randint(0, _MERSENNE_PRIME - 1))
        for _ in range(num_perm)
    ]


@functools.lru_cache(maxsize=8192)
def _cached_signature(key: str, num_perm: int = _NUM_PERM) -> tuple[int, ...]:
    """정규화된 키의 MinHash 서명을 캐싱한다. 같은 라벨이 여러 비교에 반복
    등장할 때(그래프 노드 하나를 node_store 노드 여러 개와 비교하는 식) 서명을
    매번 다시 계산하지 않게 한다."""
    return minhash_signature(_shingles(key), _hash_permutations(num_perm))


def _base_hash(shingle: str) -> int:
    return zlib.crc32(shingle.encode("utf-8")) & _MAX_HASH


def minhash_signature(
    shingles: set[str], permutations: list[tuple[int, int]]
) -> tuple[int, ...]:
    """shingle 집합을 permutations 개수만큼의 정수 서명으로 압축한다."""
    if not shingles:
        return tuple(0 for _ in permutations)
    base_hashes = [_base_hash(s) for s in shingles]
    return tuple(
        min((a * h + b) % _MERSENNE_PRIME for h in base_hashes) for a, b in permutations
    )


def jaccard_estimate(sig_a: tuple[int, ...], sig_b: tuple[int, ...]) -> float:
    """두 MinHash 서명이 일치하는 자리 비율 = 자카드 유사도 추정치."""
    if not sig_a or not sig_b:
        return 0.0
    matches = sum(1 for x, y in zip(sig_a, sig_b) if x == y)
    return matches / len(sig_a)


def _numeric_tokens_differ(a: str, b: str) -> bool:
    """숫자가 다르면 병합하지 않는다 (예: "resnet 50" vs "resnet 101",
    "gpt 2" vs "gpt 3") - 문자 3-gram만으로는 이런 버전 차이를 구분하지 못한다."""
    nums_a, nums_b = _NUMERIC_RE.findall(a), _NUMERIC_RE.findall(b)
    return nums_a != nums_b and bool(nums_a or nums_b)


def labels_match(label_a: str, label_b: str, threshold: float = _DEFAULT_THRESHOLD, num_perm: int = _NUM_PERM) -> bool:
    """두 라벨이 같은 개념을 가리키는지 단건으로 판단한다 - dedupe_labels()가
    라벨 집합 전체를 배치로 클러스터링할 때 쓰는 것과 동일한 기준(정규화 완전일치,
    또는 숫자 토큰이 같으면서 3-gram MinHash 자카드 유사도가 threshold 이상)을
    라벨 두 개짜리 비교에도 쓸 수 있게 뽑아낸 함수다. node_store.py처럼 논문이
    들어올 때마다 기존 노드 하나하나와 즉시 비교해야 해서 dedupe_labels()의 배치
    클러스터링을 매번 다시 돌릴 수 없는 경우에 쓴다 - 이렇게 같은 판정 함수를
    공유해야, 그래프 뷰(dedupe_labels 기반)와 노드 파일(node_store 기반)이 같은
    개념을 서로 다르게 판단해 어긋나는 문제가 생기지 않는다."""
    key_a, key_b = normalize_label(label_a), normalize_label(label_b)
    if key_a == key_b:
        return True
    if not key_a or not key_b or _numeric_tokens_differ(key_a, key_b):
        return False
    sig_a = _cached_signature(key_a, num_perm)
    sig_b = _cached_signature(key_b, num_perm)
    return jaccard_estimate(sig_a, sig_b) >= threshold


class _UnionFind:
    def __init__(self, items: list[str]) -> None:
        self._parent = {item: item for item in items}

    def find(self, item: str) -> str:
        while self._parent[item] != item:
            self._parent[item] = self._parent[self._parent[item]]
            item = self._parent[item]
        return item

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[ra] = rb


def dedupe_labels(
    labels: set[str],
    aliases: dict[str, list[str]] | None = None,
    threshold: float = _DEFAULT_THRESHOLD,
    num_perm: int = _NUM_PERM,
) -> dict[str, str]:
    """원본 라벨 -> 대표(canonical) 라벨 매핑을 만든다.

    호출자는 이 매핑으로 concept/entity 노드의 ID와 표시 라벨을 결정한다. 같은
    정규화 키를 가진 라벨은 항상 병합되고, 정규화 키가 다르더라도 MinHash 자카드
    유사도가 threshold 이상이면 병합된다(숫자 토큰이 다른 쌍은 예외).

    aliases는 {원본 라벨: [동의어, ...]} 형태로, Claude가 논문 본문을 보고 직접
    판단한 "완전히 같은 대상을 가리키는 다른 표기"다. "Self-Attention"과
    "Self Attention Mechanism"처럼 포함 관계인 라벨은 3-gram 자카드 유사도가
    구조적으로 낮게 나와(길이가 다를수록 합집합이 커짐) threshold를 낮추지 않는
    한 병합되지 않는데, threshold를 낮추면 이번엔 "batch/layer normalization"처럼
    무관한 개념까지 오탐 병합되는 위험이 커진다. alias는 이 문제를 문자열 유사도가
    아니라 모델의 문맥 판단으로 우회한다 - 두 라벨이 같은 alias(혹은 서로의 라벨
    그 자체)를 공유하면 병합한다.
    """
    if not labels:
        return {}
    aliases = aliases or {}

    groups: dict[str, list[str]] = {}
    for label in labels:
        groups.setdefault(normalize_label(label), []).append(label)

    def pick_representative(variants: list[str]) -> str:
        # 짧고 사전순으로 앞선 표기를 대표로 - 결정적이고 재현 가능해야 하므로
        # "가장 흔한 표기"가 아니라 항상 같은 결과가 나오는 규칙을 쓴다.
        return sorted(variants, key=lambda v: (len(v), v))[0]

    keys = sorted(groups.keys())
    signatures = {key: _cached_signature(key, num_perm) for key in keys}

    uf = _UnionFind(keys)
    for i, key_a in enumerate(keys):
        for key_b in keys[i + 1 :]:
            if _numeric_tokens_differ(key_a, key_b):
                continue
            if jaccard_estimate(signatures[key_a], signatures[key_b]) >= threshold:
                uf.union(key_a, key_b)

    # alias 기반 병합: alias 텍스트 자체는 노드가 아니라 두 라벨을 잇는 매개일
    # 뿐이다. 같은 alias를 처음 주장한 라벨 키를 기억해뒀다가, 나중에 같은
    # alias를 대는 다른 라벨이 나오면 그 라벨과 union한다. alias가 vault에
    # 이미 존재하는 다른 라벨의 표기 그 자체인 경우도 곧바로 union한다.
    alias_claimed_by: dict[str, str] = {}
    for label in labels:
        label_key = normalize_label(label)
        for alias in aliases.get(label, []):
            alias_key = normalize_label(alias)
            if not alias_key:
                continue
            if alias_key in groups:
                uf.union(label_key, alias_key)
            if alias_key in alias_claimed_by:
                uf.union(label_key, alias_claimed_by[alias_key])
            else:
                alias_claimed_by[alias_key] = label_key

    merged_variants: dict[str, list[str]] = {}
    for key in keys:
        merged_variants.setdefault(uf.find(key), []).extend(groups[key])

    canonical_by_root = {
        root: pick_representative(variants) for root, variants in merged_variants.items()
    }

    label_to_canonical: dict[str, str] = {}
    for key in keys:
        canonical = canonical_by_root[uf.find(key)]
        for variant in groups[key]:
            label_to_canonical[variant] = canonical

    return label_to_canonical
