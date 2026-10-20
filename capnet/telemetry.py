"""遥测：事件日志 + 带时间衰减的聚合。

事件种类: exposure / selection / success / failure / approval / rating(value=1..5)
种子数据视为"当前时刻"的快照（权重 1），后续事件按半衰期衰减，
两部分相加得到用于打分的 Stats。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from .scoring import Stats, decay_weight

EVENT_KINDS = ("exposure", "selection", "success", "failure", "approval", "rating")

_KIND_TO_STAT = {
    "exposure": "exposures",
    "selection": "selections",
    "success": "successes",
    "failure": "failures",
    "approval": "approvals",
}


@dataclass
class Event:
    card_id: str
    kind: str
    t: float
    value: float = 1.0


@dataclass
class Telemetry:
    events: list[Event] = field(default_factory=list)
    seed: dict[str, Stats] = field(default_factory=dict)

    @classmethod
    def load(cls, seed_path: str | None = None, events_path: str | None = None) -> "Telemetry":
        tel = cls()
        if seed_path:
            with open(seed_path, encoding="utf-8") as f:
                for card_id, counts in json.load(f).items():
                    tel.seed[card_id] = Stats(**counts)
        if events_path:
            with open(events_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        tel.events.append(Event(**json.loads(line)))
        return tel

    def record(self, card_id: str, kind: str, t: float, value: float = 1.0) -> None:
        if kind not in EVENT_KINDS:
            raise ValueError(f"未知事件类型: {kind}")
        self.events.append(Event(card_id=card_id, kind=kind, t=t, value=value))

    def stats(self, now: float, half_life: float) -> dict[str, Stats]:
        out: dict[str, Stats] = {}
        for card_id, s in self.seed.items():
            out[card_id] = Stats(**vars(s))
        for ev in self.events:
            w = decay_weight(now - ev.t, half_life)
            st = out.setdefault(ev.card_id, Stats())
            if ev.kind == "rating":
                st.rating_sum += w * ev.value
                st.rating_count += w
            else:
                attr = _KIND_TO_STAT[ev.kind]
                setattr(st, attr, getattr(st, attr) + w * ev.value)
        return out

    def total_exposures(self, now: float, half_life: float) -> float:
        return sum(s.exposures for s in self.stats(now, half_life).values())

    def total_selections(self, now: float, half_life: float) -> float:
        return sum(s.selections for s in self.stats(now, half_life).values())
