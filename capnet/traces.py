"""执行轨迹：网络的原始输入，飞轮的唯一燃料。

一条 Trace 只记录"客观解决问题的思路"：
  问题类型（problem，泛化描述）+ 有序步骤[(stage, domain, entry_id, ok, retries, approach)]
  + 经验教训（lessons）+ 最终是否被用户接受。
不含任务原文、文件、产出内容、预算金额；贡献者只存安装 id 的盐化哈希。

同一条 Trace 两用：
  1. credit()  拆成逐条目遥测事件 -> 喂打分公式（Q/R/C 从此有真实数据）；
  2. induce()  按步骤领域序列聚类，多个独立贡献者成功走过的同一序列 -> 工作流草稿，
               各阶段的 method 取自贡献者们的 approach 笔记。
上传前必须经 summarize()：清理 + 生成预览 + 指纹；hub.submit 只接受与预览指纹一致且用户同意的内容。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from collections import Counter
from dataclasses import asdict, dataclass, field

from .privacy import ScrubReport, residual_risks, scrub_text
from .workflows import Slot, Stage, Workflow

DOMAIN_RE = re.compile(r"^[a-z0-9]+(?:\.[a-z0-9_-]+)*$")
MAX_STEPS = 30
MAX_LESSONS = 5


@dataclass
class Step:
    stage: str
    domain: str
    entry_id: str = ""
    ok: bool = True
    retries: int = 0
    cost: float = 0.0
    approach: str = ""  # 这一步"怎么做的"：方法层面的一句话，不含具体内容


@dataclass
class Trace:
    contributor: str
    industry: str
    steps: list[Step]
    accepted: bool
    t: float = 0.0
    task: str = ""  # 仅本地保留的任务原文；summarize() 一律清空
    problem: str = ""  # 泛化的问题类型，如"营销视频播放量低"
    lessons: list[str] = field(default_factory=list)
    workflow_id: str = ""  # 执行时依据的工作流（若有）

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


def install_id(path: str | None = None) -> str:
    """本机随机安装 id（与用户身份无关），只用于"独立贡献者"计数。"""
    from .paths import capnet_home
    path = path or os.path.join(capnet_home(), "install_id")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    iid = uuid.uuid4().hex
    with open(path, "w", encoding="utf-8") as f:
        f.write(iid)
    return iid


def redact(trace: Trace) -> Trace:
    """最小脱敏（不做文本清理）：去掉任务原文，贡献者只留哈希。完整清理用 summarize()。"""
    return Trace(contributor=hash_contributor(trace.contributor), industry=trace.industry,
                 steps=trace.steps, accepted=trace.accepted, t=trace.t, task="",
                 problem=trace.problem, lessons=trace.lessons, workflow_id=trace.workflow_id)


def parse_trace(item: dict) -> Trace:
    return Trace(
        contributor=item["contributor"], industry=item.get("industry", ""),
        steps=[Step(**s) for s in item["steps"]], accepted=bool(item["accepted"]),
        t=item.get("t", 0.0), task=item.get("task", ""), problem=item.get("problem", ""),
        lessons=list(item.get("lessons", [])), workflow_id=item.get("workflow_id", ""),
    )


def trace_dict(trace: Trace) -> dict:
    return asdict(trace)


def fingerprint(item: dict) -> str:
    """预览指纹：规范化 JSON 的 sha256。用户看到的与上传的必须逐字节一致。"""
    canon = json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:20]


def load_traces(path: str) -> list[Trace]:
    out = []
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(parse_trace(json.loads(line)))
    return out


# ---------- 上传前汇总 ----------

@dataclass
class Summary:
    item: dict                 # 清理后的可上传轨迹（纯 dict，即将上传的原样内容）
    preview_id: str            # fingerprint(item)
    report: ScrubReport
    risks: list[str]

    def render(self) -> str:
        it = self.item
        out = ["### 将要上传的内容（仅解决思路，已清理）",
               f"- 问题类型：{it['problem'] or '（未填）'}",
               f"- 行业：{it['industry'] or '（未填）'} · 依据工作流：{it['workflow_id'] or '无'} · "
               f"结果：{'被接受' if it['accepted'] else '未被接受'}",
               "- 步骤："]
        for i, s in enumerate(it["steps"], 1):
            tool = s["entry_id"] or "（未收录工具，已匿名）"
            mark = "✓" if s["ok"] else "✗"
            out.append(f"  {i}. {mark} [{s['domain']}] {s['stage']} · 工具 {tool} · 重试 {s['retries']}"
                       + (f" — {s['approach']}" if s["approach"] else ""))
        if it["lessons"]:
            out.append("- 经验：" + "；".join(it["lessons"]))
        removed = "、".join(f"{k}×{v}" for k, v in self.report.removed.items()) or "无"
        out.append(f"\n已清理：{removed}")
        out.append("不会上传：任务原文、文件与产出内容、成本金额、精确时间、你的身份（贡献者仅为随机安装 id 的哈希）")
        if self.risks:
            out.append("⚠ 请确认：" + "；".join(self.risks))
        out.append(f"预览指纹：`{self.preview_id}`")
        return "\n".join(out)


HASH_RE = re.compile(r"^[0-9a-f]{16}$")


def summarize(draft: dict, known_entries: set[str], private_terms: list[str] | None = None,
              contributor_raw: str | None = None, contributor_hash: str | None = None) -> Summary:
    """原始执行记录 -> 只含客观思路的可上传轨迹 + 预览 + 指纹。

    draft 由 agent 按 skill 指引整理，字段同 Trace；这里不信任 draft，逐字段白名单重建：
    - 文本字段全部过 scrub_text 并限长；task 原文与 cost 直接丢弃；
    - domain 非法 → misc；entry_id 不在注册表 → 置空（私有/内部工具名不外传）；
    - 时间戳截到天。
    contributor_hash：hub 侧复核时沿用已哈希的贡献者，不再二次哈希。
    """
    rep = ScrubReport()
    terms = private_terms or []

    def clean(text, n):
        return scrub_text(str(text or ""), terms, rep, max_len=n)

    steps = []
    for s in list(draft.get("steps", []))[:MAX_STEPS]:
        dom = str(s.get("domain", "")).strip().lower()
        if not DOMAIN_RE.match(dom):
            rep.add("非法领域")
            dom = "misc"
        eid = str(s.get("entry_id", "") or "")
        if eid and eid not in known_entries:
            rep.add("未收录工具名")
            eid = ""
        if s.get("cost"):
            rep.add("成本")
        steps.append({"stage": clean(s.get("stage", ""), 40), "domain": dom, "entry_id": eid,
                      "ok": bool(s.get("ok", True)), "retries": max(0, min(int(s.get("retries", 0) or 0), 20)),
                      "cost": 0.0, "approach": clean(s.get("approach", ""), 160)})
    if draft.get("task"):
        rep.add("任务原文")
    lessons = [clean(x, 120) for x in list(draft.get("lessons", []))[:MAX_LESSONS] if str(x).strip()]
    t = float(draft.get("t") or time.time())
    item = {
        "contributor": contributor_hash or hash_contributor(contributor_raw or install_id()),
        "industry": clean(draft.get("industry", ""), 40),
        "steps": steps,
        "accepted": bool(draft.get("accepted", False)),
        "t": float(int(t // 86400) * 86400),
        "task": "",
        "problem": clean(draft.get("problem", ""), 80),
        "lessons": lessons,
        "workflow_id": str(draft.get("workflow_id", "") or "")[:64],
    }
    blob = " ".join([item["problem"], *item["lessons"], *(s["approach"] + " " + s["stage"] for s in steps)])
    return Summary(item=item, preview_id=fingerprint(item), report=rep, risks=residual_risks(blob))


# ---------- 飞轮：打分与归纳 ----------

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
        approaches: dict[str, Counter] = {d: Counter() for d in sig}
        for tr in winners:
            for s in tr.steps:
                if s.entry_id:
                    share[s.domain][s.entry_id] += 1
                stage_names[s.domain][s.stage] += 1
                if s.approach:
                    approaches[s.domain][s.approach] += 1
        stages = []
        for i, d in enumerate(sig):
            top = share[d].most_common(1)
            name = stage_names[d].most_common(1)[0][0]
            stages.append(Stage(
                id=f"s{i + 1}", name=name,
                method=[a for a, _ in approaches[d].most_common(3)],
                slots=[Slot(slot_id=f"{d}#{i + 1}", domain=d,
                            original_tool=top[0][0] if top else "",
                            replacement_query=f"domain:{d}")]))
        industry = Counter(tr.industry for tr in winners).most_common(1)[0][0]
        problems = Counter(tr.problem for tr in winners if tr.problem)
        lessons = Counter(x for tr in winners for x in tr.lessons)
        wf = Workflow(
            workflow_id=_slug(sig), name=" → ".join(st.name for st in stages), industry=industry,
            source={"kind": "conversation-induction",
                    "derived_from": f"{len(winners)} 条成功轨迹 / {support} 位独立贡献者"},
            stages=stages,
            completion_signals=["用户接受最终产出"] + [x for x, _ in lessons.most_common(3)],
            intents=[p for p, _ in problems.most_common(5)],
        )
        out.append(Induced(workflow=wf, support=support, success_rate=rate,
                           reinforces=known.get(sig, ""),
                           tool_share={d: dict(c) for d, c in share.items()}))
    out.sort(key=lambda x: (-x.support, -x.success_rate))
    return out


def workflow_to_dict(wf: Workflow) -> dict:
    return asdict(wf)
