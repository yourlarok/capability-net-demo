"""行为模拟器：验证打分公式的动态行为。

每轮：选一个领域 -> 漏斗给出候选 -> planner 按 softmax(final/T) 选卡 ->
按卡的隐藏 true_quality 抽样验收结果与用户认可 -> 记录事件。
观察：高曝光平庸卡是否下沉、冷启动好卡是否上浮。
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from .cards import Card
from .scoring import ScoreContext, Weights
from .search import FunnelTrace, search
from .telemetry import Telemetry


def softmax_pick(rng: random.Random, items: list, scores: list[float], temperature: float) -> int:
    import math
    if not items:
        return -1
    m = max(scores)
    exps = [math.exp((s - m) / max(temperature, 1e-6)) for s in scores]
    total = sum(exps)
    x = rng.random() * total
    acc = 0.0
    for i, e in enumerate(exps):
        acc += e
        if x <= acc:
            return i
    return len(items) - 1


@dataclass
class SimResult:
    snapshots: list[tuple[int, FunnelTrace]] = field(default_factory=list)
    final: FunnelTrace | None = None
    picks: dict[str, int] = field(default_factory=dict)


def simulate(
    cards: list[Card],
    telemetry: Telemetry,
    domain: str,
    rounds: int = 600,
    seed: int = 7,
    temperature: float = 0.08,
    snapshot_every: int = 100,
    half_life: float = 150.0,
    start_t: float = 0.0,
) -> SimResult:
    rng = random.Random(seed)
    weights = Weights(half_life=half_life)
    result = SimResult()

    for r in range(rounds):
        now = start_t + r
        stats_by_id = telemetry.stats(now, weights.half_life)
        ctx = ScoreContext(
            total_selections=sum(s.selections for s in stats_by_id.values()),
            weights=weights,
        )
        trace = search(cards, stats_by_id, query=None, ctx=ctx, domain=domain, k=5)

        if snapshot_every and r % snapshot_every == 0:
            result.snapshots.append((r, trace))

        idx = softmax_pick(
            rng, trace.ranked, [rc.final for rc in trace.ranked], temperature
        )
        if idx < 0:
            continue
        for i, rc in enumerate(trace.ranked):
            telemetry.record(rc.card.id, "exposure", now)
        chosen = trace.ranked[idx].card
        telemetry.record(chosen.id, "selection", now)
        result.picks[chosen.id] = result.picks.get(chosen.id, 0) + 1

        ok = rng.random() < chosen.true_quality
        telemetry.record(chosen.id, "success" if ok else "failure", now)
        if ok and rng.random() < chosen.true_quality * 0.9:
            telemetry.record(chosen.id, "approval", now)
        if rng.random() < 0.35:
            rating = min(5.0, max(1.0, round(rng.gauss(chosen.true_quality * 5.0, 0.8))))
            telemetry.record(chosen.id, "rating", now, value=rating)

    now = start_t + rounds
    stats_by_id = telemetry.stats(now, weights.half_life)
    ctx = ScoreContext(
        total_selections=sum(s.selections for s in stats_by_id.values()),
        weights=weights,
    )
    result.final = search(cards, stats_by_id, query=None, ctx=ctx, domain=domain, k=5)
    return result
