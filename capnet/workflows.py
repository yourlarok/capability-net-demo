"""工作流层：行业成熟流程的槽位化表示。

工作流 = 阶段 DAG；阶段的工具部分抽象为槽位（domain + 约束 + 原用工具 + 可替换集查询）。
来源以实践归纳为主（practice-summary / conversation-induction），模板导入为辅。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

SOURCE_KINDS = ("practice-summary", "conversation-induction", "template-import")


@dataclass
class Slot:
    slot_id: str
    domain: str
    original_tool: str = ""
    replacement_query: str = ""
    constraints: dict = field(default_factory=dict)
    required: bool = True


@dataclass
class Stage:
    id: str
    name: str
    slots: list[Slot] = field(default_factory=list)


@dataclass
class Workflow:
    workflow_id: str
    name: str
    industry: str
    source: dict
    stages: list[Stage]
    completion_signals: list[str] = field(default_factory=list)


def parse_workflow(item: dict) -> Workflow:
    src = item.get("source", {})
    if src.get("kind") not in SOURCE_KINDS:
        raise ValueError(f"{item.get('workflow_id', '?')} 非法 source.kind: {src.get('kind')}")
    stages = []
    for s in item.get("stages", []):
        slots = [Slot(**sl) for sl in s.get("slots", [])]
        stages.append(Stage(id=s["id"], name=s["name"], slots=slots))
    return Workflow(
        workflow_id=item["workflow_id"], name=item["name"],
        industry=item.get("industry", ""), source=src,
        stages=stages, completion_signals=item.get("completion_signals", []),
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


def capability_gaps(wf: Workflow) -> list[tuple[str, Slot]]:
    """列出工作流中所有需要能力路由的槽位: [(stage_id, slot)]。"""
    return [(s.id, sl) for s in wf.stages for sl in sl_slots(s)]


def sl_slots(stage: Stage) -> list[Slot]:
    return [sl for sl in stage.slots if sl.required]
