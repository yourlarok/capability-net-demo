"""执行轨迹：网络的原始输入，飞轮的唯一燃料。

接入方 agent 完成一个任务后上报一条 Trace（只含结构，不含内容）：
  任务行业 + 有序步骤[(stage, domain, entry_id, ok, retries, cost)] + 最终是否被用户接受。
同一条 Trace 有两种用途：
  1. credit()  拆成逐条目遥测事件 -> 喂打分公式（Q/R/C 从此有真实数据）；
  2. induce()  按步骤领域序列聚类，多个独立贡献者成功走过的同一序列 -> 工作流草稿。
贡献者 id 只存哈希；支持度按独立贡献者计，一个人刷一万次也只算 1。
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field

from .workflows import Slot, Stage, Workflow


@dataclass
class Step:
    stage: str
    domain: str
    entry_id: str = ""
    ok: bool = True
    retries: int = 0
    cost: float = 0.0


@dataclass
class Trace:
    contributor: str
    industry: str
    steps: list[Step]
    accepted: bool
    t: float = 0.0
    task: str = ""  # 仅本地保留的任务摘要；上报前由 redact() 清空

    @property
    def signature(self) -> tuple[str, ...]:
        """领域序列（相邻重复合并）：同一套路换了工具仍是同一签名。"""
        out: list[str] = []
        for s in self.steps:
            if not out or out[-1] != s.domain:
                out.append(s.domain)
        return tuple(out)


def hash_contributor(raw: str, salt: str = "capnet") -> str:
    return hashlib.sha256((salt + raw).encode("utf-8")).hexdigest()[:16]


def redact(trace: Trace) -> Trace:
    """上报前脱敏：去掉任务原文，贡献者只留哈希。"""
    return Trace(contributor=hash_contributor(trace.contributor), industry=trace.industry,
                 steps=trace.steps, accepted=trace.accepted, t=trace.t, task="")


def parse_trace(item: dict) -> Trace:
    return Trace(
        contributor=item["contributor"], industry=item.get("industry", ""),
        steps=[Step(**s) for s in item["steps"]], accepted=bool(item["accepted"]),
        t=item.get("t", 0.0), task=item.get("task", ""),
    )


def load_traces(path: str) -> list[Trace]:
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(parse_trace(json.loads(line)))
    return out


def credit(trace: Trace) -> list[tuple[str, str, float]]:
    """Trace -> [(entry_id, kind, value)] 遥测事件。

    每次重试记一次 failure（返工就是隐式差评）；步骤最终成功记 success；
    整条被用户接受时，成功步骤各记一次 approval——终局认可回溯分摊到链上每一环。
    """
    events: list[tuple[str, str, float]] = []
    for s in trace.steps:
        if not s.entry_id:
            continue
        events.append((s.entry_id, "selection", 1.0))
        events.extend((s.entry_id, "failure", 1.0) for _ in range(max(0, s.retries)))
        events.append((s.entry_id, "success" if s.ok else "failure", 1.0))
        if trace.accepted and s.ok:
            events.append((s.entry_id, "approval", 1.0))
    return events


@dataclass
class Induced:
    workflow: Workflow
    support: int            # 成功走过该签名的独立贡献者数
    success_rate: float     # 该签名全部轨迹的接受率
    reinforces: str = ""    # 非空 = 与已有工作流同构，应强化它而非新建
    tool_share: dict = field(default_factory=dict)  # domain -> {entry_id: 次数}


def _slug(sig: tuple[str, ...]) -> str:
    return "induced-" + hashlib.sha1("|".join(sig).encode("utf-8")).hexdigest()[:8]


def _workflow_signature(wf: Workflow) -> tuple[str, ...]:
    out: list[str] = []
    for st in wf.stages:
        for sl in st.slots:
            if not out or out[-1] != sl.domain:
                out.append(sl.domain)
    return tuple(out)


def induce(traces: list[Trace], existing: list[Workflow] | None = None,
           min_support: int = 3, min_success: float = 0.6, min_steps: int = 2) -> list[Induced]:
    """多个独立贡献者反复成功的领域序列 -> 工作流草稿（source.kind=conversation-induction）。

    草稿只是候选：入库仍走评审，与已有工作流同构时标 reinforces 而不重复建。
    """
    by_sig: dict[tuple[str, ...], list[Trace]] = {}
    for tr in traces:
        if len(tr.signature) >= min_steps:
            by_sig.setdefault(tr.signature, []).append(tr)
    known = {_workflow_signature(w): w.workflow_id for w in (existing or [])}

    out: list[Induced] = []
    for sig, group in by_sig.items():
        winners = [tr for tr in group if tr.accepted]
        support = len({tr.contributor for tr in winners})
        rate = len(winners) / len(group)
        if support < min_support or rate < min_success:
            continue
        share: dict[str, Counter] = {d: Counter() for d in sig}
        stage_names: dict[str, Counter] = {d: Counter() for d in sig}
        for tr in winners:
            for s in tr.steps:
                if s.entry_id:
                    share[s.domain][s.entry_id] += 1
                stage_names[s.domain][s.stage] += 1
        stages = []
        for i, d in enumerate(sig):
            top = share[d].most_common(1)
            name = stage_names[d].most_common(1)[0][0]
            stages.append(Stage(id=f"s{i + 1}", name=name, slots=[Slot(
                slot_id=f"{d}#{i + 1}", domain=d,
                original_tool=top[0][0] if top else "",
                replacement_query=f"domain:{d}",
            )]))
        industry = Counter(tr.industry for tr in winners).most_common(1)[0][0]
        wf = Workflow(
            workflow_id=_slug(sig), name=" → ".join(st.name for st in stages), industry=industry,
            source={"kind": "conversation-induction",
                    "derived_from": f"{len(winners)} 条成功轨迹 / {support} 位独立贡献者"},
            stages=stages, completion_signals=["用户接受最终产出"],
        )
        out.append(Induced(workflow=wf, support=support, success_rate=rate,
                           reinforces=known.get(sig, ""),
                           tool_share={d: dict(c) for d, c in share.items()}))
    out.sort(key=lambda x: (-x.support, -x.success_rate))
    return out
