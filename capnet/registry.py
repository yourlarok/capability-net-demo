"""注册表门面：一次加载全部能力目录 + 遥测 + 工作流。

内存路径以前只读 cards.json，策展目录（pro_tools / catalog/*.json）只有建库后可见；
这里统一：data/cards.json + data/pro_tools.json + data/catalog/*.json 全部进路由池。
遥测 = 种子统计 + hub 汇总的轨迹事件（data/hub/events.jsonl），上传的轨迹由此回流到路由打分。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from .cards import Card, flatten, load_apps
from .domains import UserProfile, load_profile
from .paths import DATA, hub_dir, user_workflows_dir
from .scoring import Stats
from .telemetry import Telemetry
from .workflows import Workflow, load_workflows


def catalog_paths(data_dir: str = DATA) -> list[str]:
    paths = [os.path.join(data_dir, "cards.json"), os.path.join(data_dir, "pro_tools.json")]
    cat = os.path.join(data_dir, "catalog")
    if os.path.isdir(cat):
        paths += [os.path.join(cat, f) for f in sorted(os.listdir(cat)) if f.endswith(".json")]
    return [p for p in paths if os.path.exists(p)]


@dataclass
class Registry:
    cards: list[Card]
    stats: dict[str, Stats]
    workflows: list[Workflow]
    profile: UserProfile = field(default_factory=UserProfile)

    @classmethod
    def load(cls, data_dir: str = DATA, half_life: float = 30.0, use_profile: bool = True,
             load_hub: bool = True, include_demo: bool = True) -> "Registry":
        """include_demo=False 去掉示例卡（access.demo，模拟数据、没有真实接入渠道）：对外服务默认如此，
        模拟器 / 评测仍用全量。"""
        cards: list[Card] = []
        seen: set[str] = set()
        for p in catalog_paths(data_dir):
            for app in load_apps(p):
                if not include_demo and app.access.get("demo"):
                    continue
                for c in flatten(app):
                    if c.id in seen:
                        raise ValueError(f"重复条目 id: {c.id}（{p}）")
                    seen.add(c.id)
                    cards.append(c)
        seed = os.path.join(data_dir, "telemetry_seed.json")
        events = os.path.join(hub_dir(), "events.jsonl")
        tel = Telemetry.load(seed_path=seed if os.path.exists(seed) else None,
                             events_path=events if load_hub and os.path.exists(events) else None)
        wf_dir = os.path.join(data_dir, "workflows")
        profile = load_profile(os.path.join(data_dir, "user_profile.json")) if use_profile else UserProfile()
        workflows = load_workflows(wf_dir) if os.path.isdir(wf_dir) else []
        extra = user_workflows_dir()
        if extra and os.path.isdir(extra):  # 安装模式下评审入库的工作流，同 id 覆盖随包版本
            mine = load_workflows(extra)
            ids = {w.workflow_id for w in mine}
            workflows = [w for w in workflows if w.workflow_id not in ids] + mine
        return cls(cards=cards, stats=tel.stats(now=0.0, half_life=half_life),
                   workflows=workflows, profile=profile)

    def in_domains(self, domains: list[str]) -> list[Card]:
        return [c for c in self.cards
                if any(c.domain == d or c.domain.startswith(d + ".") for d in domains)]

    def get_workflow(self, workflow_id: str) -> Workflow | None:
        return next((w for w in self.workflows if w.workflow_id == workflow_id), None)

    @property
    def total_selections(self) -> float:
        return sum(s.selections for s in self.stats.values())
