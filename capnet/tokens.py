"""分词与字段权重：search（内存）与 store（持久化索引）共用的唯一实现。"""
from __future__ import annotations

import math
import re

_CJK = re.compile(r"[一-鿿]")
_LATIN = re.compile(r"[a-z0-9]+")

FIELD_WEIGHTS = {"intent": 1.0, "name": 0.6, "desc": 0.3}


def idf_table(docs: list[str]) -> dict[str, float]:
    n = len(docs)
    df: dict[str, int] = {}
    for d in docs:
        for t in set(tokenize(d)):
            df[t] = df.get(t, 0) + 1
    return {t: math.log(1.0 + n / c) for t, c in df.items()}


def field_relevance(query: str, fields: dict[str, set[str]], idf: dict[str, float],
                    weights: dict[str, float] = FIELD_WEIGHTS,
                    unseen_idf: float | None = None) -> float:
    """idf 加权重叠率 × 字段权重，归一化到 [0,1]；卡与工作流共用。

    unseen_idf：语料没见过的多字 token 按最大稀有度计入分母。否则查询的真实主题
    （"装修""马拉松"）被静默丢弃，剩下"方案/计划"这类泛词撑起虚高相关度。
    """
    q_tokens = set(tokenize(query))
    hit = total = 0.0
    for t in q_tokens:
        w_idf = idf.get(t, unseen_idf if unseen_idf is not None and len(t) >= 2 else 0.0)
        if w_idf <= 0.0:
            continue
        total += w_idf
        hit += w_idf * max((weights[f] for f, ts in fields.items() if t in ts), default=0.0)
    return hit / total if total > 0 else 0.0


def tokenize(text: str) -> list[str]:
    text = text.lower()
    tokens = _LATIN.findall(text)
    cjk_chars = _CJK.findall(text)
    tokens.extend(cjk_chars)
    tokens.extend(a + b for a, b in zip(cjk_chars, cjk_chars[1:]))
    return tokens
