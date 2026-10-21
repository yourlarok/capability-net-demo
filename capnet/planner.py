"""规划器：复杂任务 -> 成熟工作流 -> 逐阶段方法 + 工具填槽 -> 可执行计划。

  1. find_workflows 召回工作流，相关度过阈值才算"有模板"；否则降级为能力直搜 + 记缺口，
     并提示 agent 自行拆解、事后以轨迹归纳（traces.induce）；
  2. 每个槽位按 domain(+alt_domains) 从注册表取卡，走同一套打分；
     自动可调用卡与人工卡分开标注，agent 不得假装能调用 gui/manual；
  3. 无槽位的阶段由 agent 自身按 method 完成（LLM 能力），显式写明；
  4. 计划附带 Trace 模板：执行完填 ok/retries/accepted 上报，飞轮闭环。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .registry import Registry
from .scoring import ScoreContext
from .cardview import RANK, access_checklist, render_capabilities, slot_table
from .intent import describe
from .search import RankedCard, as_options, search
from .traces import Step, Trace
from .workflows import Slot, Stage, Workflow, find_workflows

MARKET_MISMATCH = 0.5  # 卡声明的市场不含当前市场时的乘子（如国内场景下的 Meta Ads）
MIN_WORKFLOW_RELEVANCE = 0.10  # 实义证据门槛（workflows.evidence）负责拒识，这里只是兜底下限


@dataclass
class SlotFill:
    slot: Slot
    options: list[dict]

    @property
    def gap(self) -> bool:
        return not self.options

    @property
    def has_auto(self) -> bool:
        return any(not o.get("manual") for o in self.options)


@dataclass
class StagePlan:
    stage: Stage
    fills: list[SlotFill] = field(default_factory=list)

    @property
    def agent_only(self) -> bool:
        return not self.stage.slots


@dataclass
class Plan:
    query: str
    workflow: Workflow | None
    relevance: float
    alternatives: list[tuple[float, str]] = field(default_factory=list)
    stages: list[StagePlan] = field(default_factory=list)
    fallback: list[dict] = field(default_factory=list)  # 无工作流时的能力直搜结果
    market: str | None = None
    understanding: str = ""         # 直搜时对需求能力类型的理解（渲染用）
    understanding_source: str = ""  # agent 声明 / 关键词推断
    excluded: int = 0               # 被意图门排除的族外候选数

    @property
    def matched(self) -> bool:
        return self.workflow is not None

    @property
    def fills(self) -> list[SlotFill]:
        return [f for sp in self.stages for f in sp.fills]

    @property
    def gaps(self) -> list[Slot]:
        return [f.slot for f in self.fills if f.gap]

    @property
    def coverage(self) -> float:
        fills = self.fills
        return 1.0 if not fills else sum(not f.gap for f in fills) / len(fills)

    @property
    def auto_ratio(self) -> float:
        filled = [f for f in self.fills if not f.gap]
        return 0.0 if not filled else sum(f.has_auto for f in filled) / len(filled)

    def trace_template(self, contributor: str) -> Trace:
        steps = []
        for sp in self.stages:
            for f in sp.fills:
                steps.append(Step(stage=sp.stage.name, domain=f.slot.domain,
                                  entry_id=f.options[0]["value"] if f.options else ""))
        return Trace(contributor=contributor,
                     industry=self.workflow.industry if self.workflow else "", steps=steps,
                     accepted=False, task=self.query,
                     workflow_id=self.workflow.workflow_id if self.workflow else "")


def _options(ranked: list[RankedCard], k: int) -> list[dict]:
    from .search import FunnelTrace
    opts = as_options(FunnelTrace(ranked=ranked), max_options=k)
    for o, rc in zip(opts, ranked):
        o["manual"] = not rc.card.auto_callable
        o["accessibility"] = rc.card.accessibility
    return opts


def infer_market(query: str) -> str:
    """无显式市场时按查询语言推断：含中文 → cn，否则 global。"""
    return "cn" if re.search(r"[一-鿿]", query) else "global"


def market_fit(card, market: str | None) -> float:
    if not market or not card.markets:
        return 1.0
    return 1.0 if market in card.markets else MARKET_MISMATCH


def fill_slot(reg: Registry, slot: Slot, ctx: ScoreContext, k: int = 3,
              market: str | None = None) -> SlotFill:
    """主 domain 先填；alt_domains 只在主 domain 不足 k 张时补位（防止相邻领域的高热度卡
    抢占，如小说写作工具顶掉营销文案工具）。同一 app 只出现一次；按市场适配降权。"""
    ranked: list[RankedCard] = []
    seen_apps: set[str] = set()
    for doms in ([slot.domain], slot.alt_domains):
        if len(ranked) >= k or not doms:
            break
        trace = search(reg.in_domains(doms), reg.stats, None, ctx, k=k * 3, profile=reg.profile)
        for rc in trace.ranked:
            rc.final *= market_fit(rc.card, market)
        trace.ranked.sort(key=lambda x: -x.final)
        for rc in trace.ranked:
            app = rc.card.app_id or rc.card.id
            if app not in seen_apps and len(ranked) < k:
                seen_apps.add(app)
                ranked.append(rc)
    return SlotFill(slot=slot, options=_options(ranked, k))


def plan(query: str, reg: Registry, k: int = 3, max_cost: float | None = None,
         min_relevance: float = MIN_WORKFLOW_RELEVANCE, task_type: str | None = None,
         market: str | None = None, families: list[str] | None = None) -> Plan:
    market = market or infer_market(query)
    ctx = ScoreContext(total_selections=reg.total_selections, max_cost=max_cost)
    cands = find_workflows(query, reg.workflows, k=3, task_type=task_type)
    alts = [(round(r, 3), w.workflow_id) for r, w in cands]
    if not cands or cands[0][0] < min_relevance:
        trace = search(reg.cards, reg.stats, query, ctx, k=k, profile=reg.profile, families=families,
                       keep_ratio=KEEP_RATIO)
        top = cands[0][0] if cands else 0.0
        return Plan(query=query, workflow=None, relevance=top, alternatives=alts,
                    fallback=[] if trace.gap else _options(trace.ranked, k), market=market,
                    understanding=describe(trace.families),
                    understanding_source="agent 声明" if families else "关键词推断",
                    excluded=trace.off_intent)
    rel, wf = cands[0]
    stages = [StagePlan(stage=st, fills=[fill_slot(reg, sl, ctx, k, market) for sl in st.slots])
              for st in wf.stages]
    return Plan(query=query, workflow=wf, relevance=rel, alternatives=alts[1:], stages=stages, market=market)


KEEP_RATIO = 0.3  # 直搜结果：弱于首选 30% 以下的不凑数
SOURCE_LABEL = {"expert-curation": "专家整理", "induced": "从真实轨迹归纳", "manual": "人工编写"}


def render_plan(p: Plan) -> str:
    """对话窗口里的计划：流程总览 → 准备清单 → 逐阶段（目标/方法/交付物/验收/工具对比表）→ 完成判据。"""
    out: list[str] = []
    if not p.matched:
        out.append(f"# 🧭 没有现成工作流：{p.query}")
        cand = "、".join(f"`{w}`" for _, w in p.alternatives) or "无"
        out.append(f"\n> 最接近的工作流相关度 {p.relevance:.2f}，低于阈值 {MIN_WORKFLOW_RELEVANCE}（候选：{cand}）。"
                   f"\n> 请按常识拆解执行；完成后可汇总思路回传，帮助归纳成新工作流。\n")
        out.append(render_capabilities(p.query, p.fallback, p.understanding, p.understanding_source,
                                       p.market, p.excluded, gap=not p.fallback))
        return "\n".join(out)

    wf = p.workflow
    src = wf.source.get("kind", "")
    out.append(f"# 🧭 {wf.name}")
    out.append("")
    out.append(f"> **任务** {p.query}")
    out.append(f"> **工作流** `{wf.workflow_id}` · 匹配度 {p.relevance:.2f} · {SOURCE_LABEL.get(src, src)}"
               + (f"（{wf.source['derived_from']}）" if wf.source.get("derived_from") else ""))
    out.append(f"> **工具覆盖** {p.coverage:.0%} 的工具位有推荐 · {p.auto_ratio:.0%} 可由 agent 自动执行，其余需你在软件里操作")
    out.append("")
    out.append("**流程** " + " → ".join(f"{RANK[i]} {sp.stage.name}" for i, sp in enumerate(p.stages)))
    if wf.inputs_needed:
        out.append("\n## 📋 开工前需要你提供")
        out += [f"- [ ] {x}" for x in wf.inputs_needed]
    firsts = [f.options[0] for sp in p.stages for f in sp.fills if f.options]
    if firsts:
        out.append("\n## 🔑 接入准备（各工具位首选）")
        out += access_checklist(firsts)
        out.append("\n*API Key 请放进环境变量，不要贴进对话；agent 会先检查变量是否已设置。*")
    for i, sp in enumerate(p.stages):
        st = sp.stage
        out.append(f"\n## {RANK[i]} {st.name}")
        if st.goal:
            out.append(f"**目标**　{st.goal}")
        if st.method:
            out.append("\n**方法**")
            out += [f"{j}. {m}" for j, m in enumerate(st.method, 1)]
        if st.deliverable:
            out.append(f"\n**交付物**　📦 {st.deliverable}")
        if st.checks:
            out.append("\n**验收**")
            out += [f"- ✅ {c}" for c in st.checks]
        if sp.agent_only:
            out.append("\n**工具**　🧠 无需外部工具，agent 按上述方法直接完成")
        for f in sp.fills:
            out.append(f"\n**工具 · {f.slot.slot_id}**（{' / '.join(f.slot.domains)}）")
            out += ["⚠ 注册表暂无可用工具（已记为缺口）"] if f.gap else [""] + slot_table(f.options)
    if wf.completion_signals:
        out.append("\n## 🏁 完成判据")
        out += [f"- {s}" for s in wf.completion_signals]
    out.append("\n*图例：🤖 agent 可直接调用 · 👤 人工工具（只推荐，不代操作）· ⭐ 首选 · 🆕 新卡数据不足*")
    if p.alternatives:
        out.append("\n*其他候选工作流：" + "、".join(f"`{w}`（{r}）" for r, w in p.alternatives) + "*")
    return "\n".join(out)
