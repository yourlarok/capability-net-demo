"""检索漏斗：确定性过滤 -> 语义召回 -> 打分精排。

分词与字段权重定义在 tokens.py（与 store 的持久化索引共用同一实现）。
相关度：idf 加权重叠率 × 字段权重（intents 1.0 / name 0.6 / desc 0.3），归一化到 [0,1]。
个性化：UserProfile 的领域亲和乘子作用于 final，有界且可检查。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .cards import Card
from .domains import UserProfile
from .scoring import Breakdown, ScoreContext, Stats, score_card
from .tokens import field_relevance, idf_table, tokenize

__all__ = ["tokenize", "relevance", "search", "search_store", "as_options", "RankedCard", "FunnelTrace"]


def _idf(cards: list[Card]) -> dict[str, float]:
    return idf_table([c.searchable_text for c in cards])


def _field_tokens(card: Card) -> dict[str, set[str]]:
    """分字段匹配面：intents 是 curator 维护的检索面，权重最高；
    描述/域名只作弱信号，防止蹭词污染相关度。"""
    return {
        "intent": set(tokenize(" ".join(card.intents))),
        "name": set(tokenize(card.name)),
        "desc": set(tokenize(card.description + " " + card.domain)),
    }


def relevance(query: str, card: Card, idf: dict[str, float]) -> float:
    return field_relevance(query, _field_tokens(card), idf)


@dataclass
class RankedCard:
    card: Card
    relevance: float
    breakdown: Breakdown
    final: float
    stats: Stats = field(default_factory=Stats)


@dataclass
class FunnelTrace:
    total: int = 0
    after_filter: int = 0
    after_recall: int = 0
    ranked: list[RankedCard] = field(default_factory=list)
    gap: bool = False  # True = 注册表答不了这个查询（无结果或特征词未命中），应记缺口


# 通用语素：能力描述的构词常客，单独命中不能证明覆盖（"合成/服务"不等于会做）
GENERIC_MORPHEMES = {
    "合成", "服务", "生成", "系统", "平台", "工具", "计算", "引擎", "管理", "处理",
    "分析", "制作", "设计", "开发", "编辑",
}


def distinctive_tokens(query: str, idf: dict[str, float], unseen_idf: float) -> set[str]:
    """查询的特征词：多字（≥2）、非通用语素、idf 处于高档（≥70% max）的 token。

    单字 CJK 只用于召回加权，不参与覆盖判定（"计"蹭"设计"不算命中）；
    未见过的 token 按 unseen_idf（≈ ln(1+n)）计——稀有词往往是关键意图。
    """
    toks = {t for t in set(tokenize(query)) if len(t) >= 2 and t not in GENERIC_MORPHEMES}
    if not toks:
        return set()
    scores = {t: idf.get(t, unseen_idf) for t in toks}
    hi = max(scores.values())
    return {t for t, s in scores.items() if s >= hi * 0.7}


def _matches_distinctive(card: Card, dtoks: set[str]) -> bool:
    if not dtoks:
        return True
    fields = _field_tokens(card)
    return any(t in fs for t in dtoks for fs in fields.values())


def _mark_gap(trace: FunnelTrace, dtoks: set[str], window: int = 3) -> FunnelTrace:
    """top-window 内没有任何卡命中特征词 → 缺口。"""
    trace.gap = not trace.ranked or not any(
        _matches_distinctive(rc.card, dtoks) for rc in trace.ranked[:window])
    return trace


def _popularity_prior(card: Card) -> float | None:
    """源外部流行度 -> 认可度冷启动先验（0.2~0.9）。

    users 或 normalized 缺失视为"无数据"，返回 None 走全局默认先验；
    normalized=0 是真实的源内垫底信号，与无数据区分。
    """
    pop = card.popularity or {}
    users, norm = pop.get("users"), pop.get("normalized")
    if users is None or norm is None:
        return None
    return 0.2 + 0.7 * max(0.0, min(1.0, norm))


def _rerank(recalled: list[tuple[float, Card]], stats_getter, ctx: ScoreContext,
            profile: UserProfile | None, k: int, trace: FunnelTrace) -> FunnelTrace:
    for rel, card in recalled:
        stats = stats_getter(card.id)
        bd = score_card(
            stats, ctx,
            cost=card.cost_per_call, latency=card.latency, healthy=card.healthy,
            recognition_prior=_popularity_prior(card),
        )
        affinity = profile.affinity(card.domain) if profile else 1.0
        trace.ranked.append(RankedCard(card=card, relevance=rel, breakdown=bd,
                                       final=rel * bd.score * affinity, stats=stats))
    trace.ranked.sort(key=lambda x: -x.final)
    trace.ranked = [rc for rc in trace.ranked if rc.final > 0.0][:k]
    return trace


def search(
    cards: list[Card],
    stats_by_id: dict[str, Stats],
    query: str | None,
    ctx: ScoreContext,
    domain: str | None = None,
    k: int = 5,
    recall_size: int = 50,
    min_relevance: float = 0.02,
    profile: UserProfile | None = None,
) -> FunnelTrace:
    trace = FunnelTrace(total=len(cards))

    pool = [c for c in cards if c.healthy]
    if domain:
        pool = [c for c in pool if c.domain == domain or c.domain.startswith(domain + ".")]
    trace.after_filter = len(pool)

    idf = _idf(cards)
    dtoks = distinctive_tokens(query, idf, math.log(1.0 + len(cards))) if query else set()
    if query:
        scored = [(relevance(query, c, idf), c) for c in pool]
        scored = [x for x in scored if x[0] >= min_relevance]
        scored.sort(key=lambda x: -x[0])
        recalled = scored[:recall_size]
    else:
        recalled = [(1.0, c) for c in pool]
    trace.after_recall = len(recalled)

    out = _rerank(recalled, lambda cid: stats_by_id.get(cid, Stats()), ctx, profile, k, trace)
    return _mark_gap(out, dtoks) if query else out


def search_store(
    store,
    query: str | None,
    ctx: ScoreContext,
    domain: str | None = None,
    k: int = 5,
    recall_size: int = 50,
    min_relevance: float = 0.02,
    profile: UserProfile | None = None,
    now: float = 0.0,
) -> FunnelTrace:
    """store 快速通道：召回走持久化倒排索引（SQL），精排只在 top-recall 上进行。"""
    counts = store.counts()
    trace = FunnelTrace(total=counts["entries"], after_filter=counts["approved"])
    dtoks = set()
    if query:
        dtoks = distinctive_tokens(query, store.idf_for(tokenize(query)),
                                   math.log(1.0 + max(counts["entries"], 1)))
        hits = store.recall(query, domain, recall_size, min_relevance)
    else:
        hits = [(eid, 1.0) for eid in store.list_domain(domain)][:recall_size]
    trace.after_recall = len(hits)
    entries = store.get_entries([eid for eid, _ in hits])
    stats_map = store.stats_for(list(entries), now, ctx.weights.half_life)
    recalled = [(rel, entries[eid]) for eid, rel in hits if eid in entries]
    out = _rerank(recalled, lambda cid: stats_map.get(cid, Stats()), ctx, profile, k, trace)
    return _mark_gap(out, dtoks) if query else out


def as_options(trace: FunnelTrace, max_options: int = 3) -> list[dict]:
    """把 top-k 候选变成可直接呈现给用户的选项卡。

    每个选项带用途介绍、成本、延迟、质量分与数据量；
    数据不足（选中 < 30 次）的卡标注"新卡·探索性试用"，让用户知情。
    """
    options = []
    for i, rc in enumerate(trace.ranked[:max_options]):
        c, b, s = rc.card, rc.breakdown, rc.stats
        intro = (
            f"{c.description}｜单次成本 {c.cost_per_call}｜延迟 {c.latency}｜"
            f"质量分 {b.quality:.2f}｜基于 {int(s.selections)} 次调用"
        )
        if not c.auto_callable:
            intro += f"｜人工使用({c.accessibility})：agent 无法自动调用，推荐用户人工操作"
        if s.selections < 30:
            intro += "｜新卡·探索性试用"
        options.append({
            "label": c.name + ("（推荐）" if i == 0 else ""),
            "value": c.id,
            "intro": intro,
            "final": round(rc.final, 4),
            "name": c.name,
            "description": c.description,
            "domain": c.domain,
            "vendor": c.vendor,
            "cost": c.cost_per_call,
            "latency": c.latency,
            "calls": int(s.selections),
            "quality": round(b.quality, 3),
            "recognition": round(b.recognition, 3),
            "ctr": round(b.ctr, 3),
            "ucb": round(b.ucb, 3),
            "is_new": s.selections < 30,
            "recommended": i == 0,
        })
    return options
