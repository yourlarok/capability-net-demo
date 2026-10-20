"""能力缺口日志：注册表答不出来的查询，逐条落盘并聚合。

用途：缺口 = 策展与适配器扩展的输入。search/board 查无结果时记录
{query, domain, reason}，`demo.py gaps` 按频次聚合，回答"园子还缺什么"。
"""
from __future__ import annotations

import json
import os
import time


def log_gap(path: str, query: str | None, domain: str | None, reason: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"t": time.time(), "query": query, "domain": domain,
                            "reason": reason}, ensure_ascii=False) + "\n")


def summarize(path: str) -> list[dict]:
    """按 (query, domain) 聚合计数，高频优先，同频最近优先。"""
    counts: dict[tuple, dict] = {}
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            e = json.loads(line)
            key = (e.get("query"), e.get("domain"))
            slot = counts.setdefault(key, {"query": key[0], "domain": key[1],
                                           "reason": e.get("reason", ""), "count": 0, "last": 0.0})
            slot["count"] += 1
            slot["last"] = max(slot["last"], e.get("t", 0.0))
    return sorted(counts.values(), key=lambda x: (-x["count"], -x["last"]))
