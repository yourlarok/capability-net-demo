"""能力卡打分引擎。

Score = (wq*Q + wr*R + wc*C + beta*U) * cost_fit * latency_fit * health

Q: 质量，验收成功率的 Wilson 下界（小样本受罚）
R: 认可度，贝叶斯平滑后的认可率与评分均值
C: 曝光效率，选中率（选中/曝光）的 Wilson 下界，曝光只做分母
U: 探索项，UCB 形式，给冷启动卡保底曝光机会
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

Z_95 = 1.96

LATENCY_ORDER = {"fast": 3, "medium": 2, "slow": 1}


def wilson_lower(successes: float, total: float, z: float = Z_95) -> float:
    """二项成功率的 Wilson 置信下界：3/3 打不过 96/100。"""
    if total <= 0:
        return 0.0
    p = successes / total
    denom = 1.0 + z * z / total
    center = p + z * z / (2.0 * total)
    margin = z * math.sqrt(p * (1.0 - p) / total + z * z / (4.0 * total * total))
    return max(0.0, min(1.0, (center - margin) / denom))


def bayesian_rate(positive: float, total: float, prior: float, strength: float) -> float:
    """贝叶斯平滑比率：strength 个先验伪样本把无数据卡拉向先验。"""
    if total + strength <= 0:
        return prior
    return (positive + strength * prior) / (total + strength)


def ucb_bonus(trials: float, total_trials: float) -> float:
    """探索项：实际被调用（选中）次数越少 bonus 越大。

    注意分母用选中数而非曝光数——只被展示未被选中不算试错，
    否则新卡会在从未被调用的情况下耗尽探索预算。
    """
    if total_trials <= 1:
        return 1.0
    return math.sqrt(math.log(total_trials) / (trials + 1.0))


def decay_weight(age: float, half_life: float) -> float:
    """事件新鲜度权重：age 与 half_life 同单位（逻辑轮次或秒）。"""
    if half_life <= 0:
        return 1.0
    return 0.5 ** (max(0.0, age) / half_life)


def cost_fit(cost: float, max_cost: float | None) -> float:
    """成本乘子：预算内为 1，超预算线性衰减，2 倍预算归零。"""
    if max_cost is None or cost <= max_cost:
        return 1.0
    if cost >= 2.0 * max_cost:
        return 0.0
    return (2.0 * max_cost - cost) / max_cost


def latency_fit(card_latency: str, required: str | None) -> float:
    """延迟乘子：满足要求为 1，差一档 0.6，差两档 0.3。"""
    if required is None:
        return 1.0
    have = LATENCY_ORDER.get(card_latency, 2)
    want = LATENCY_ORDER.get(required, 2)
    if have >= want:
        return 1.0
    return 0.6 if have + 1 == want else 0.3


@dataclass
class Weights:
    quality: float = 0.5
    recognition: float = 0.2
    ctr: float = 0.3
    beta: float = 0.15
    prior_strength: float = 20.0
    rating_prior_strength: float = 5.0
    half_life: float = 30.0


@dataclass
class Stats:
    exposures: float = 0.0
    selections: float = 0.0
    successes: float = 0.0
    failures: float = 0.0
    approvals: float = 0.0
    rating_sum: float = 0.0
    rating_count: float = 0.0

    def add(self, other: "Stats") -> None:
        for f_ in vars(self):
            setattr(self, f_, getattr(self, f_) + getattr(other, f_))


@dataclass
class Breakdown:
    quality: float
    recognition: float
    ctr: float
    ucb: float
    base: float
    cost_fit: float
    latency_fit: float
    health: float
    score: float


@dataclass
class ScoreContext:
    total_selections: float
    weights: Weights = field(default_factory=Weights)
    prior_recognition: float = 0.5
    prior_rating: float = 3.0
    max_cost: float | None = None
    required_latency: str | None = None


def score_card(
    stats: Stats,
    ctx: ScoreContext,
    cost: float = 0.0,
    latency: str = "medium",
    healthy: bool = True,
    recognition_prior: float | None = None,
) -> Breakdown:
    w = ctx.weights
    q = wilson_lower(stats.successes, stats.successes + stats.failures)
    prior_r = ctx.prior_recognition if recognition_prior is None else recognition_prior
    r_rate = bayesian_rate(stats.approvals, stats.selections, prior_r, w.prior_strength)
    if stats.rating_count > 0:
        r_rating = bayesian_rate(
            stats.rating_sum, stats.rating_count, ctx.prior_rating, w.rating_prior_strength
        ) / 5.0
        r = 0.5 * r_rate + 0.5 * r_rating
    else:
        r = r_rate
    c = wilson_lower(stats.selections, stats.exposures)
    u = ucb_bonus(stats.selections, ctx.total_selections)
    base = w.quality * q + w.recognition * r + w.ctr * c
    cf = cost_fit(cost, ctx.max_cost)
    lf = latency_fit(latency, ctx.required_latency)
    h = 1.0 if healthy else 0.0
    score = (base + w.beta * u) * cf * lf * h
    return Breakdown(
        quality=q, recognition=r, ctr=c, ucb=u, base=base,
        cost_fit=cf, latency_fit=lf, health=h, score=score,
    )
