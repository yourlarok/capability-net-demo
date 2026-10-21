"""能力社区：基石层 + 社区层，靠对话中的隐式信号自我生长。

两层
  基石层  data/workflows 里系统维护、评审过的 flow（expert-curation / practice-summary / template-import）。
          社区信号只给它打分、提示"哪一步常被跳过"，不会改写或删除它。
  社区层  从使用中长出来的 flow（conversation-induction）：agent 走完一个没有现成 flow 的多步骤任务，
          finish_task 留下的思路轨迹就是一次"注册"；多位独立用户走出同一条路 → 升级为「已验证」，
          成功率掉下去 → 自动下架。社区 flow 的 id 与基石层不冲突，不能覆盖基石。

隐式信号（收集是隐式的，用户不需要做任何事）
  agent 在任务结束时调 finish_task，按对话中观察到的行为填：
    outcome      accepted 用户接受了结果 / partial 部分可用 / abandoned 中途放弃
    corrections  用户纠正、表示不满、要求重来的次数（"不对""不是这个意思""重来"）
    skipped      计划里没做的阶段
    steps        实际走过的阶段、所用工具（= 用户在推荐里选了哪个）、返工次数、方法
  → 工具打分（credit）、flow 评分（FlowStats）、社区 flow 归纳（induce）共用这一条记录。

上报方式（开始前选定，存 ~/.capnet/config.json 的 share）
  auto  每次自动上报清理后的思路，不再逐次询问
  ask   每次先给用户看预览，同意才上报
  off   只在本机生效（本机的 flow 评分与"我上次这样做过"照常可用），不上报
  未选择时按 off 处理，并提示 agent 先问用户。
"""
from __future__ import annotations

import json
import os
import shutil
import time
import urllib.request
from collections import Counter
from dataclasses import dataclass, field

from .paths import capnet_home, hub_dir

SHARE_MODES = ("auto", "ask", "off")
SHARE_LABEL = {"auto": "自动上报（已清理的思路）", "ask": "每次先确认再上报", "off": "只在本机生效，不上报"}

FOUNDATION_KINDS = ("expert-curation", "practice-summary", "template-import")
VERIFIED_SUPPORT = 3      # 独立贡献者数达到它、且成功率达标 → 社区·已验证
MIN_SUCCESS = 0.6         # 低于它的社区 flow 不展示（下架）
PRIOR = {"foundation": 0.8, "verified": 0.7, "new": 0.45}  # 没有使用数据时的信任先验
PRIOR_RUNS = 5.0          # 先验相当于多少次使用
SYNC_TTL = 6 * 3600


# ---------- 上报方式 ----------

def _config_path() -> str:
    return os.path.join(capnet_home(), "config.json")


def load_config() -> dict:
    try:
        with open(_config_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def get_share() -> str | None:
    mode = load_config().get("share")
    return mode if mode in SHARE_MODES else None


def set_share(mode: str) -> None:
    if mode not in SHARE_MODES:
        raise ValueError(f"share 只能是 {'/'.join(SHARE_MODES)}")
    cfg = load_config()
    cfg["share"] = mode
    os.makedirs(capnet_home(), exist_ok=True)
    with open(_config_path(), "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)


# ---------- flow 评分 ----------

@dataclass
class FlowStats:
    runs: int = 0
    accepted: int = 0
    abandoned: int = 0
    corrections: int = 0
    contributors: set = field(default_factory=set)
    skipped: Counter = field(default_factory=Counter)

    def add(self, ev: dict) -> None:
        self.runs += 1
        self.accepted += bool(ev.get("accepted"))
        self.abandoned += ev.get("outcome") == "abandoned"
        self.corrections += int(ev.get("corrections") or 0)
        if ev.get("contributor"):
            self.contributors.add(ev["contributor"])
        self.skipped.update(set(ev.get("skipped") or []))

    @property
    def success_rate(self) -> float | None:
        return self.accepted / self.runs if self.runs else None

    @property
    def avg_corrections(self) -> float:
        return self.corrections / self.runs if self.runs else 0.0

    def often_skipped(self, min_share: float = 0.5, min_runs: int = 3) -> list[tuple[str, float]]:
        if self.runs < min_runs:
            return []
        return [(s, n / self.runs) for s, n in self.skipped.most_common() if n / self.runs >= min_share]

    def to_dict(self) -> dict:
        return {"runs": self.runs, "accepted": self.accepted, "abandoned": self.abandoned,
                "corrections": self.corrections, "contributors": len(self.contributors),
                "skipped": dict(self.skipped)}

    @classmethod
    def from_dict(cls, d: dict) -> "FlowStats":
        st = cls(runs=d.get("runs", 0), accepted=d.get("accepted", 0), abandoned=d.get("abandoned", 0),
                 corrections=d.get("corrections", 0), skipped=Counter(d.get("skipped") or {}))
        st.contributors = {f"remote{i}" for i in range(d.get("contributors", 0))}  # 只保留计数
        return st


def flow_events_path(root: str | None = None) -> str:
    return os.path.join(root or hub_dir(), "flow_events.jsonl")


def load_flow_stats(root: str | None = None) -> dict[str, FlowStats]:
    out: dict[str, FlowStats] = {}
    path = flow_events_path(root)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    ev = json.loads(line)
                    out.setdefault(ev["workflow_id"], FlowStats()).add(ev)
    return out


def flow_event(item: dict) -> dict | None:
    """清理后的轨迹 → 一条 flow 评价事件（没有依据 flow 的轨迹不产生）。"""
    if not item.get("workflow_id"):
        return None
    return {"workflow_id": item["workflow_id"], "contributor": item["contributor"],
            "accepted": bool(item["accepted"]), "outcome": item.get("outcome", ""),
            "corrections": int(item.get("corrections") or 0), "skipped": item.get("skipped") or [],
            "t": item["t"]}


def tier(wf, stats: FlowStats | None = None) -> str:
    """foundation 基石 / verified 社区·已验证 / new 社区·新。"""
    if wf.source.get("kind") in FOUNDATION_KINDS:
        return "foundation"
    support = max(int((wf.community or {}).get("support", 0)), len(stats.contributors) if stats else 0)
    return "verified" if support >= VERIFIED_SUPPORT else "new"


def trust(wf, stats: FlowStats | None) -> float:
    """信任分 ∈ (0,1]：层级先验 + 实际接受率（贝叶斯收缩）− 纠正次数惩罚。"""
    prior = PRIOR[tier(wf, stats)]
    if not stats or not stats.runs:
        return prior
    rate = (stats.accepted + prior * PRIOR_RUNS) / (stats.runs + PRIOR_RUNS)
    return max(0.05, rate * (1.0 - 0.05 * min(stats.avg_corrections, 4.0)))


# ---------- 社区 flow：归纳、存储、同步 ----------

def community_dir(root: str | None = None) -> str:
    return os.path.join(root or hub_dir(), "community")


def cache_path() -> str:
    return os.path.join(capnet_home(), "community_cache.json")


def update_community(root: str | None = None, foundation=None) -> list[dict]:
    """从 hub 全部轨迹重新归纳社区 flow（支持度 1 即可上架为「新」，成功率不达标的下架）。"""
    from .traces import induce, load_traces, workflow_to_dict
    root = root or hub_dir()
    traces = load_traces(os.path.join(root, "traces.jsonl"))
    res = induce(traces, existing=foundation or [], min_support=1, min_success=MIN_SUCCESS)
    out_dir = community_dir(root)
    tmp = out_dir + ".tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    rows = []
    for r in res:
        if r.reinforces:  # 与基石 flow 同构：只记评分，不另建
            continue
        d = workflow_to_dict(r.workflow)
        d["community"] = {"support": r.support, "success_rate": round(r.success_rate, 3),
                          "status": "verified" if r.support >= VERIFIED_SUPPORT else "new",
                          "tool_share": r.tool_share}
        with open(os.path.join(tmp, f"{r.workflow.workflow_id}.json"), "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        rows.append(d)
    shutil.rmtree(out_dir, ignore_errors=True)
    os.replace(tmp, out_dir)
    return rows


def _dir_flows(root: str | None = None) -> list[dict]:
    d, out = community_dir(root), []
    if os.path.isdir(d):
        for fn in sorted(os.listdir(d)):
            if fn.endswith(".json"):
                with open(os.path.join(d, fn), encoding="utf-8") as f:
                    out.append(json.load(f))
    return out


def load_community(root: str | None = None) -> list[dict]:
    """本机 hub 的社区 flow + 从远端 hub 同步来的缓存（同 id 以远端为准）。"""
    flows = {item["workflow_id"]: item for item in _dir_flows(root)}
    for item in _read_cache().get("flows", []):
        flows[item["workflow_id"]] = item
    return list(flows.values())


def remote_stats() -> dict[str, FlowStats]:
    return {k: FlowStats.from_dict(v) for k, v in (_read_cache().get("stats") or {}).items()}


def _read_cache() -> dict:
    try:
        with open(cache_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def export(root: str | None = None) -> dict:
    """hub 对外：GET /v1/flows 的内容（只含本 hub 归纳出的社区 flow 与评分）。"""
    return {"flows": _dir_flows(root), "stats": {k: v.to_dict() for k, v in load_flow_stats(root).items()}}


def sync(hub_url: str | None = None, force: bool = False, timeout: float = 5.0) -> dict:
    """从社区 hub 拉取社区 flow 与评分到本机缓存。失败不影响使用（沿用旧缓存）。"""
    hub_url = hub_url or os.environ.get("CAPNET_HUB_URL")
    if not hub_url:
        return {"status": "skipped", "message": "未配置 CAPNET_HUB_URL，只使用本机数据"}
    cache = _read_cache()
    if not force and time.time() - cache.get("synced_at", 0) < SYNC_TTL:
        return {"status": "fresh", "flows": len(cache.get("flows", []))}
    try:
        with urllib.request.urlopen(hub_url.rstrip("/") + "/v1/flows", timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (OSError, ValueError) as e:
        return {"status": "error", "message": f"同步失败，沿用旧缓存：{e}"}
    data["synced_at"] = time.time()
    os.makedirs(capnet_home(), exist_ok=True)
    with open(cache_path(), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    return {"status": "synced", "flows": len(data.get("flows", []))}


# ---------- 渲染 ----------

TIER_BADGE = {"foundation": "🏛 基石流程（系统维护、已评审）",
              "verified": "✅ 社区流程 · 已验证",
              "new": "🌱 社区流程 · 新（走过的人还少，属于探索性推荐）"}


def stats_line(wf, stats: FlowStats | None) -> str:
    t = tier(wf, stats)
    parts = [TIER_BADGE[t]]
    if stats and stats.runs:
        parts.append(f"{stats.runs} 次使用 · {len(stats.contributors)} 人 · 接受率 {stats.success_rate:.0%}"
                     + (f" · 平均纠正 {stats.avg_corrections:.1f} 次" if stats.corrections else ""))
    elif wf.community:
        parts.append(f"{wf.community.get('support', 0)} 人走通 · 接受率 {wf.community.get('success_rate', 0):.0%}")
    else:
        parts.append("还没有使用反馈")
    return " · ".join(parts)


def stats_dict(wf, stats: FlowStats | None) -> dict:
    return {"tier": tier(wf, stats), "trust": round(trust(wf, stats), 3),
            **(stats.to_dict() if stats else {"runs": 0})}
