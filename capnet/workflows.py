"""工作流层：行业成熟流程的槽位化表示。

工作流 = 阶段序列；每个阶段 = 做事方法（goal/method/deliverable/checks）+ 工具槽位。
- 方法部分是"工作模式"：没有槽位的阶段由 agent 自身（LLM）或用户按方法完成；
- 槽位部分是"工具抽象"：domain（+ alt_domains）+ 约束 + 原用工具，路由时从注册表填卡。
来源：practice-summary / conversation-induction（实践归纳，主）、expert-curation（行业方法论策展）、
template-import（模板库，辅）。每条都必须可追溯。
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field

from .tokens import field_relevance, idf_table, tokenize

SOURCE_KINDS = ("practice-summary", "conversation-induction", "expert-curation", "template-import")

# 工作流检索面：intents 是策展维护的任务说法，权重最高；阶段名/目标只作弱信号
WF_FIELD_WEIGHTS = {"intent": 1.0, "name": 0.6, "stage": 0.3}


@dataclass
class Slot:
    slot_id: str
    domain: str
    original_tool: str = ""
    replacement_query: str = ""
    constraints: dict = field(default_factory=dict)
    required: bool = True
    alt_domains: list[str] = field(default_factory=list)  # 跨目录分类差异（如 video.gen ≈ video.img2video）

    @property
    def domains(self) -> list[str]:
        return [self.domain, *self.alt_domains]


@dataclass
class Stage:
    id: str
    name: str
    slots: list[Slot] = field(default_factory=list)
    goal: str = ""
    method: list[str] = field(default_factory=list)
    deliverable: str = ""
    checks: list[str] = field(default_factory=list)  # 验收标准 / 关键指标


@dataclass
class Workflow:
    workflow_id: str
    name: str
    industry: str
    source: dict
    stages: list[Stage]
    completion_signals: list[str] = field(default_factory=list)
    intents: list[str] = field(default_factory=list)
    inputs_needed: list[str] = field(default_factory=list)  # 开工前需要用户提供的东西

    def searchable_fields(self) -> dict[str, set[str]]:
        return {
            "intent": set(tokenize(" ".join(self.intents))),
            "name": set(tokenize(self.name + " " + self.industry)),
            "stage": set(tokenize(" ".join(s.name + " " + s.goal for s in self.stages))),
        }

    def searchable_text(self) -> str:
        return " ".join([self.name, self.industry, " ".join(self.intents),
                         " ".join(s.name + " " + s.goal for s in self.stages)])


def parse_workflow(item: dict) -> Workflow:
    src = item.get("source", {})
    if src.get("kind") not in SOURCE_KINDS:
        raise ValueError(f"{item.get('workflow_id', '?')} 非法 source.kind: {src.get('kind')}")
    stages = []
    for s in item.get("stages", []):
        slots = [Slot(**sl) for sl in s.get("slots", [])]
        stages.append(Stage(id=s["id"], name=s["name"], slots=slots, goal=s.get("goal", ""),
                            method=s.get("method", []), deliverable=s.get("deliverable", ""),
                            checks=s.get("checks", [])))
    return Workflow(
        workflow_id=item["workflow_id"], name=item["name"],
        industry=item.get("industry", ""), source=src,
        stages=stages, completion_signals=item.get("completion_signals", []),
        intents=item.get("intents", []), inputs_needed=item.get("inputs_needed", []),
    )


def load_workflows(path: str) -> list[Workflow]:
    """加载单个 JSON 文件或目录下所有 *.json 工作流。"""
    files = []
    if os.path.isdir(path):
        files = [os.path.join(path, f) for f in sorted(os.listdir(path)) if f.endswith(".json")]
    else:
        files = [path]
    out = []
    for fp in files:
        with open(fp, encoding="utf-8") as f:
            out.append(parse_workflow(json.load(f)))
    return out


# 规划类泛词：任何任务描述都会带，命中它们不能证明"有这个模板"
GENERIC_TASK_WORDS = {
    "方案", "计划", "规划", "策划", "设计", "制定", "优化", "改进", "分析", "制作", "怎么", "如何",
    "帮我", "一个", "一套", "一下", "内容", "打造", "提升", "效果", "思路", "how", "to", "the", "a",
}


def evidence(query: str, wf: Workflow) -> set[str]:
    """查询与工作流 intents/name 的实义命中：多字、非泛词。空集 = 没有证据说明这是同一类任务。"""
    f = wf.searchable_fields()
    surface = f["intent"] | f["name"]
    return {t for t in set(tokenize(query))
            if len(t) >= 2 and t not in GENERIC_TASK_WORDS and t in surface}


# 任务类型门：工作流 = "产出或改进一个交付物"的过程。词面相关只说明话题对上了，
# 还要看意图是不是这一类——"电脑播放视频卡顿"话题是视频，任务是排障，不该给营销流程。
# 生产中应由调用方 agent（LLM）直接传 task_type，这里的线索词表只是离线兜底。
TASK_CUES = (
    "做", "制作", "拍", "剪", "录", "改", "优化", "提升", "复盘", "策划", "规划", "起号", "涨粉", "增长",
    "开", "上架", "发布", "改编", "生产", "打造", "转化", "数据", "低", "差", "下滑", "不理想", "没人看",
    "留不住", "跳出", "流失", "创作", "当", "make", "create", "fix", "improve", "grow", "launch",
)
NON_TASK_CUES = (
    "推荐几", "推荐一", "推荐", "哪个好", "哪款", "买什么", "多少钱", "卡顿", "连不上", "报错", "坏了",
    "闪退", "税", "申报", "合同", "法律", "是什么", "什么意思", "经典书", "书单",
)
TASK_TYPES = ("produce", "improve", "grow", "launch", "review")


def is_workflow_task(query: str, task_type: str | None = None) -> bool:
    if task_type is not None:  # 调用方显式声明，优先于词表
        return task_type in TASK_TYPES
    q = query.lower()
    if any(c in q for c in NON_TASK_CUES):
        return False
    return any(c in q for c in TASK_CUES)


def find_workflows(query: str, workflows: list[Workflow], k: int = 3,
                   min_relevance: float = 0.0, task_type: str | None = None) -> list[tuple[float, Workflow]]:
    """任务描述 -> 工作流候选 [(relevance, wf)]，高到低。

    两道门：任务类型门（不是"产出/改进"类请求 → 无候选）+ 实义证据门（无证据 → 相关度 0）。
    """
    if not is_workflow_task(query, task_type):
        return []
    idf = idf_table([w.searchable_text() for w in workflows])
    unseen = math.log(1.0 + len(workflows))
    scored = [(field_relevance(query, w.searchable_fields(), idf, WF_FIELD_WEIGHTS, unseen)
               if evidence(query, w) else 0.0, w) for w in workflows]
    scored = [x for x in scored if x[0] > min_relevance]
    scored.sort(key=lambda x: -x[0])
    return scored[:k]


def capability_gaps(wf: Workflow) -> list[tuple[str, Slot]]:
    """列出工作流中所有需要能力路由的槽位: [(stage_id, slot)]。"""
    return [(s.id, sl) for s in wf.stages for sl in sl_slots(s)]


def sl_slots(stage: Stage) -> list[Slot]:
    return [sl for sl in stage.slots if sl.required]
