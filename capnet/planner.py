"""规划器：复杂任务 -> 成熟工作流 -> 逐阶段方法 + 工具填槽 -> 可执行计划。

  1. find_workflows 召回工作流，相关度过阈值才算"有模板"；否则降级为能力直搜 + 记缺口，
     并提示 agent 自行拆解、事后以轨迹归纳（traces.induce）；
  2. 每个槽位按 domain(+alt_domains) 从注册表取卡，走同一套打分；
     自动可调用卡与人工卡分开标注，agent 不得假装能调用 gui/manual；
  3. 无槽位的阶段由 agent 自身按 method 完成（LLM 能力），显式写明；
  4. 计划附带 Trace 模板：执行完填 ok/retries/accepted 上报，飞轮闭环。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .registry import Registry
from .scoring import ScoreContext
from .search import RankedCard, as_options, search
from .traces import Step, Trace
from .workflows import Slot, Stage, Workflow, find_workflows

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
                     accepted=False, task=self.query)


def _options(ranked: list[RankedCard], k: int) -> list[dict]:
    from .search import FunnelTrace
    opts = as_options(FunnelTrace(ranked=ranked), max_options=k)
    for o, rc in zip(opts, ranked):
        o["manual"] = not rc.card.auto_callable
        o["accessibility"] = rc.card.accessibility
    return opts


def fill_slot(reg: Registry, slot: Slot, ctx: ScoreContext, k: int = 3) -> SlotFill:
    """主 domain 先填；alt_domains 只在主 domain 不足 k 张时补位（防止相邻领域的高热度卡
    抢占，如小说写作工具顶掉营销文案工具）。同一 app 只出现一次。"""
    ranked: list[RankedCard] = []
    seen_apps: set[str] = set()
    for doms in ([slot.domain], slot.alt_domains):
        if len(ranked) >= k or not doms:
            break
        trace = search(reg.in_domains(doms), reg.stats, None, ctx, k=k * 2, profile=reg.profile)
        for rc in trace.ranked:
            app = rc.card.app_id or rc.card.id
            if app not in seen_apps and len(ranked) < k:
                seen_apps.add(app)
                ranked.append(rc)
    return SlotFill(slot=slot, options=_options(ranked, k))


def plan(query: str, reg: Registry, k: int = 3, max_cost: float | None = None,
         min_relevance: float = MIN_WORKFLOW_RELEVANCE, task_type: str | None = None) -> Plan:
    ctx = ScoreContext(total_selections=reg.total_selections, max_cost=max_cost)
    cands = find_workflows(query, reg.workflows, k=3, task_type=task_type)
    alts = [(round(r, 3), w.workflow_id) for r, w in cands]
    if not cands or cands[0][0] < min_relevance:
        trace = search(reg.cards, reg.stats, query, ctx, k=k, profile=reg.profile)
        top = cands[0][0] if cands else 0.0
        return Plan(query=query, workflow=None, relevance=top, alternatives=alts,
                    fallback=[] if trace.gap else _options(trace.ranked, k))
    rel, wf = cands[0]
    stages = [StagePlan(stage=st, fills=[fill_slot(reg, sl, ctx, k) for sl in st.slots])
              for st in wf.stages]
    return Plan(query=query, workflow=wf, relevance=rel, alternatives=alts[1:], stages=stages)


def _opt_line(o: dict) -> str:
    tag = f"人工使用·{o['accessibility']}" if o["manual"] else f"agent 可调用·{o['accessibility']}"
    rec = "（推荐）" if o.get("recommended") else ""
    return f"  - **{o['name']}**{rec} [{tag}] — {o['description']}"


def render_plan(p: Plan) -> str:
    out: list[str] = []
    if not p.matched:
        out.append(f"# 未命中成熟工作流：{p.query}")
        out.append(f"\n最高工作流相关度 {p.relevance:.2f} < 阈值 {MIN_WORKFLOW_RELEVANCE}，"
                   f"候选：{', '.join(w for _, w in p.alternatives) or '无'}。")
        out.append("\n建议：agent 自行拆解执行，完成后上报轨迹，由 `induce` 归纳成工作流草稿。")
        if p.fallback:
            out.append("\n## 可能相关的能力（直搜）")
            out += [_opt_line(o) for o in p.fallback]
        else:
            out.append("\n注册表也没有相关能力：已记为缺口。")
        return "\n".join(out)

    wf = p.workflow
    out.append(f"# {wf.name}")
    out.append(f"\n> 任务：{p.query}")
    out.append(f"> 工作流 `{wf.workflow_id}` · 匹配度 {p.relevance:.2f} · 来源 {wf.source['kind']}"
               f"（{wf.source.get('derived_from', '')}）")
    out.append(f"> 槽位覆盖 {p.coverage:.0%} · 可自动执行槽位 {p.auto_ratio:.0%}")
    if wf.inputs_needed:
        out.append("\n## 开工前需要你提供")
        out += [f"- {x}" for x in wf.inputs_needed]
    for i, sp in enumerate(p.stages, 1):
        st = sp.stage
        out.append(f"\n## {i}. {st.name}" + (f" — {st.goal}" if st.goal else ""))
        if st.method:
            out += [f"{j}. {m}" for j, m in enumerate(st.method, 1)]
        if st.deliverable:
            out.append(f"\n**交付物**：{st.deliverable}")
        if st.checks:
            out.append("**验收/指标**：" + "；".join(st.checks))
        if sp.agent_only:
            out.append("\n*工具*：无需外部能力，agent 按上述方法直接完成。")
        for f in sp.fills:
            out.append(f"\n*工具槽 `{f.slot.slot_id}`*（{' / '.join(f.slot.domains)}）：")
            if f.gap:
                out.append("  - ⚠ 注册表无可用卡 → 缺口")
            else:
                out += [_opt_line(o) for o in f.options]
    if wf.completion_signals:
        out.append("\n## 完成判据")
        out += [f"- {s}" for s in wf.completion_signals]
    if p.alternatives:
        out.append("\n<sub>其他候选工作流：" + ", ".join(f"{w}({r})" for r, w in p.alternatives) + "</sub>")
    return "\n".join(out)
