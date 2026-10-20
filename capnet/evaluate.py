"""批量领域评测：一组真实说法的任务，量化路由层能否"找到模板并配齐工具"。

指标：
- 命中@1：期望工作流排第一（expect 可写 "a|b" 表示歧义题两者皆可）；
- 拒识：域外题（expect=null）正确判为"无成熟工作流"；
- 槽位覆盖：命中计划中有卡可填的必选槽位比例；
- 自动化率：已填槽位中至少有一张 agent 可调用卡的比例（其余只能推荐人工）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from .planner import Plan, plan
from .registry import Registry


@dataclass
class Row:
    domain: str
    query: str
    expect: str | None
    plan: Plan

    @property
    def got(self) -> str | None:
        return self.plan.workflow.workflow_id if self.plan.matched else None

    @property
    def ok(self) -> bool:
        if self.expect is None:
            return self.got is None
        return self.got in self.expect.split("|")


def run_eval(reg: Registry, path: str) -> tuple[list[Row], dict]:
    with open(path, encoding="utf-8") as f:
        items = json.load(f)
    rows = [Row(it["domain"], it["query"], it.get("expect"), plan(it["query"], reg)) for it in items]
    inscope = [r for r in rows if r.expect is not None]
    outscope = [r for r in rows if r.expect is None]
    matched = [r for r in rows if r.ok and r.plan.matched]
    slot_gaps: dict[str, int] = {}
    for r in matched:
        for sl in r.plan.gaps:
            slot_gaps[sl.domain] = slot_gaps.get(sl.domain, 0) + 1
    summary = {
        "n": len(rows),
        "hit@1": sum(r.ok for r in inscope) / max(1, len(inscope)),
        "reject": sum(r.ok for r in outscope) / max(1, len(outscope)),
        "coverage": sum(r.plan.coverage for r in matched) / max(1, len(matched)),
        "auto": sum(r.plan.auto_ratio for r in matched) / max(1, len(matched)),
        "slot_gaps": dict(sorted(slot_gaps.items(), key=lambda x: -x[1])),
    }
    return rows, summary


def render_report(rows: list[Row], s: dict) -> str:
    out = ["# capnet 批量领域评测", "",
           f"- 题量 {s['n']}（域内 {sum(r.expect is not None for r in rows)} / 域外 {sum(r.expect is None for r in rows)}）",
           f"- 工作流命中@1：**{s['hit@1']:.0%}**",
           f"- 域外拒识率：**{s['reject']:.0%}**",
           f"- 命中计划的槽位覆盖：**{s['coverage']:.0%}**",
           f"- 已填槽位的自动化率：**{s['auto']:.0%}**（其余只能推荐人工工具）",
           f"- 缺卡槽位：{s['slot_gaps'] or '无'}",
           "", "## 逐条", "",
           "| ✓ | 领域 | 任务 | 期望 | 实得 | 匹配度 | 覆盖 | 自动 |",
           "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        p = r.plan
        cov = f"{p.coverage:.0%}" if p.matched else "-"
        auto = f"{p.auto_ratio:.0%}" if p.matched else "-"
        out.append(f"| {'✅' if r.ok else '❌'} | {r.domain} | {r.query} | {r.expect or '（域外）'} | "
                   f"{r.got or '无'} | {p.relevance:.2f} | {cov} | {auto} |")
    return "\n".join(out)
