"""轨迹汇总端（hub）+ 客户端提交。

上传是自愿的，路由对所有人完整开放，与是否上传无关。
流程：agent 整理执行记录 -> summarize()（本地清理 + 预览 + 指纹）-> 展示给用户 ->
      用户同意 -> submit(preview_id, consent=True) -> hub.ingest()。

hub.ingest 不信任客户端：再跑一遍同样的清理，按指纹去重，然后
  - 追加 traces.jsonl（归纳用）；
  - 拆成遥测事件追加 events.jsonl（Registry 加载后直接影响路由打分）。
refresh() 从全部轨迹归纳工作流草稿到 data/workflows_drafts/，人工评审后再入正式库。

部署：设 CAPNET_HUB_URL 则 POST 到远端 hub（`demo.py hub serve` 起的服务）；
      未设则写本机 hub 目录（自托管：你的机器就是汇总端）。
"""
from __future__ import annotations

import json
import os
import urllib.request

from .paths import capnet_home, drafts_dir, hub_dir
from .traces import (HASH_RE, Summary, credit, fingerprint, induce, load_traces, parse_trace,
                     summarize, workflow_to_dict)

MAX_BODY = 64 * 1024


# ---------- 待确认预览（用户侧） ----------

def _pending_path(preview_id: str) -> str:
    return os.path.join(capnet_home(), "pending", f"{preview_id}.json")


def save_pending(s: Summary) -> str:
    path = _pending_path(s.preview_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(s.item, f, ensure_ascii=False)
    return path


def discard_pending(preview_id: str) -> bool:
    path = _pending_path(preview_id)
    if os.path.exists(path):
        os.remove(path)
        return True
    return False


def submit(preview_id: str, consent: bool, hub_url: str | None = None,
           known_entries: set[str] | None = None) -> dict:
    """只上传用户看过并同意的那份预览；内容被改动（指纹不符）一律拒绝。"""
    if consent is not True:
        discard_pending(preview_id)
        return {"status": "declined", "message": "用户未同意，预览已删除，未上传任何内容。"}
    path = _pending_path(preview_id)
    if not os.path.exists(path):
        return {"status": "error", "message": f"找不到预览 {preview_id}：请先 summarize 并让用户确认。"}
    with open(path, encoding="utf-8") as f:
        item = json.load(f)
    if fingerprint(item) != preview_id:
        return {"status": "error", "message": "预览内容与指纹不符（被修改过），拒绝上传。"}
    hub_url = hub_url if hub_url is not None else os.environ.get("CAPNET_HUB_URL")
    if hub_url:
        req = urllib.request.Request(hub_url.rstrip("/") + "/v1/traces",
                                     data=json.dumps({"trace": item}, ensure_ascii=False).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    else:
        result = ingest(item, known_entries)
    if result.get("status") in ("accepted", "duplicate"):
        os.remove(path)
    return result


# ---------- 汇总端 ----------

def _known_entries() -> set[str]:
    from .registry import Registry
    return {c.id for c in Registry.load(use_profile=False, load_hub=False).cards}


def ingest(item: dict, known_entries: set[str] | None = None, root: str | None = None) -> dict:
    root = root or hub_dir()
    if not isinstance(item, dict) or not isinstance(item.get("steps"), list):
        return {"status": "error", "message": "轨迹格式错误：缺 steps"}
    contributor = str(item.get("contributor", ""))
    if not HASH_RE.match(contributor):
        return {"status": "error", "message": "贡献者必须是 16 位哈希"}
    known = known_entries if known_entries is not None else _known_entries()
    clean = summarize(item, known, contributor_hash=contributor).item  # 服务端复核清理
    fp = fingerprint(clean)
    os.makedirs(root, exist_ok=True)
    seen_path = os.path.join(root, "seen.txt")
    seen = set()
    if os.path.exists(seen_path):
        with open(seen_path, encoding="utf-8") as f:
            seen = {ln.strip() for ln in f}
    if fp in seen:
        return {"status": "duplicate", "id": fp}
    with open(os.path.join(root, "traces.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(clean, ensure_ascii=False) + "\n")
    events = credit(parse_trace(clean))
    with open(os.path.join(root, "events.jsonl"), "a", encoding="utf-8") as f:
        for eid, kind, value in events:
            f.write(json.dumps({"card_id": eid, "kind": kind, "t": clean["t"], "value": value},
                               ensure_ascii=False) + "\n")
    with open(seen_path, "a", encoding="utf-8") as f:
        f.write(fp + "\n")
    return {"status": "accepted", "id": fp, "events": len(events)}


def refresh(root: str | None = None, min_support: int = 3) -> list[dict]:
    """全部轨迹 -> 工作流草稿（写 data/workflows_drafts/），返回摘要列表。"""
    from .registry import Registry
    root = root or hub_dir()
    traces = load_traces(os.path.join(root, "traces.jsonl"))
    res = induce(traces, existing=Registry.load(use_profile=False).workflows, min_support=min_support)
    out_dir = drafts_dir()
    os.makedirs(out_dir, exist_ok=True)
    summary = []
    for r in res:
        d = workflow_to_dict(r.workflow)
        d["_induction"] = {"support": r.support, "success_rate": round(r.success_rate, 3),
                           "reinforces": r.reinforces, "tool_share": r.tool_share}
        with open(os.path.join(out_dir, f"{r.workflow.workflow_id}.json"), "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
        summary.append({"workflow_id": r.workflow.workflow_id, "name": r.workflow.name,
                        "support": r.support, "success_rate": r.success_rate,
                        "reinforces": r.reinforces})
    return summary


def stats(root: str | None = None) -> dict:
    root = root or hub_dir()
    traces = load_traces(os.path.join(root, "traces.jsonl"))
    return {"traces": len(traces), "contributors": len({t.contributor for t in traces}),
            "accepted": sum(t.accepted for t in traces)}


def serve(host: str = "127.0.0.1", port: int = 8765) -> None:
    """hub 与路由共用一个 HTTP 服务（见 http_api）。"""
    from .http_api import serve as _serve
    _serve(host, port)
