"""分词与字段权重：search（内存）与 store（持久化索引）共用的唯一实现。"""
from __future__ import annotations

import re

_CJK = re.compile(r"[一-鿿]")
_LATIN = re.compile(r"[a-z0-9]+")

FIELD_WEIGHTS = {"intent": 1.0, "name": 0.6, "desc": 0.3}


def tokenize(text: str) -> list[str]:
    text = text.lower()
    tokens = _LATIN.findall(text)
    cjk_chars = _CJK.findall(text)
    tokens.extend(cjk_chars)
    tokens.extend(a + b for a, b in zip(cjk_chars, cjk_chars[1:]))
    return tokens
