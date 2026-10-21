"""能力直搜意图纯度评测（data/eval/capability_intent.json）。"""
from __future__ import annotations

import json

from .intent import card_families
from .planner import infer_market, market_fit
from .registry import Registry
from .scoring import ScoreContext
from .search import search


def run(path: str, reg: Registry | None = None, intent_gate: bool = True, k: int = 3,
        declared: bool = False) -> dict:
    """declared=True 模拟 agent 正确声明类型（取 expect），衡量的是族内覆盖与排序，而非意图识别。"""
    reg = reg or Registry.load()
    cases = json.load(open(path, encoding="utf-8"))["cases"]
    ctx = ScoreContext(total_selections=reg.total_selections)
    rows, top1, cross = [], 0, 0
    for c in cases:
        tr = search(reg.cards, reg.stats, c["q"], ctx, k=k * 2, profile=reg.profile, intent_gate=intent_gate,
                    families=c["expect"] if declared else None)
        m = infer_market(c["q"])
        ranked = sorted(tr.ranked, key=lambda r: (-r.tier, -r.final * market_fit(r.card, m)))[:k]
        ok = [bool(card_families(r.card.domain) & set(c["expect"])) for r in ranked]
        top1 += bool(ok and ok[0])
        cross += ok.count(False)
        rows.append({"q": c["q"], "expect": c["expect"], "top": [r.card.id for r in ranked], "ok": ok})
    n = len(cases)
    return {"n": n, "top1": top1 / n, "cross_per_query": cross / n, "rows": rows}
