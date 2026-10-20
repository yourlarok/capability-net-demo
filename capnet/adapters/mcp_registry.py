"""MCP 官方 registry 适配器：拉取 server 列表，映射为 v2 草稿卡。

API: GET https://registry.modelcontextprotocol.io/v0/servers?limit=N[&cursor=...]
返回 {servers: [{server: {...}, _meta: {...}}], metadata: {nextCursor, count}}
同一 server 的多版本按 name 去重，保留 isLatest 或最高 version。
产出为草稿卡（review_status=draft），须经评审才入主注册表。
"""
from __future__ import annotations

import argparse
import json
import os
import urllib.parse

from .http_util import fetch_json

API = "https://registry.modelcontextprotocol.io/v0/servers"

DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "image.gen": ["image", "flux", "diffusion", "dall", "photo", "picture"],
    "video.gen": ["video", "veo", "runway", "kling", "sora"],
    "audio.tts": ["tts", "text to speech", "speech", "voice"],
    "audio.music": ["music", "song", "suno"],
    "3d.mesh": ["3d", "mesh", "blender"],
    "devtools.git": ["github", "gitlab", "git "],
    "devtools.ci": ["ci", "deploy", "build", "vercel", "netlify"],
    "data.database": ["postgres", "sqlite", "mysql", "mongo", "redis", "database", "sql"],
    "data.search": ["search", "brave", "tavily", "exa", "serp"],
    "browser.automation": ["playwright", "puppeteer", "browser", "crawl"],
    "office.docs": ["notion", "slack", "linear", "jira", "trello", "asana", "docs"],
    "cloud.ops": ["aws", "azure", "gcp", "cloudflare", "kubernetes", "docker"],
    "ai.llm": ["llm", "openai", "anthropic", "claude", "gpt", "model"],
}

COST_DEFAULT = 0.0
LATENCY_DEFAULT = "medium"


def classify_domain(text: str) -> str:
    t = text.lower()
    best, best_hits = "misc", 0
    for domain, keywords in DOMAIN_KEYWORDS.items():
        hits = sum(1 for kw in keywords if kw in t)
        if hits > best_hits:
            best, best_hits = domain, hits
    return best


def server_to_app(entry: dict) -> dict:
    """纯函数：单条 registry 记录 -> v2 草稿卡。"""
    srv = entry.get("server", entry)
    name = srv.get("name", "")
    title = srv.get("title") or name.split("/")[-1]
    desc = srv.get("description", "")
    domain = classify_domain(f"{name} {title} {desc}")
    remotes = srv.get("remotes", [])
    return {
        "app_id": f"mcp.{name}",
        "name": title,
        "vendor": name.split("/")[0] if "/" in name else "",
        "description": desc,
        "platforms": ["mcp"],
        "pricing": {"model": "unknown"},
        "popularity": {"source": "mcp-official", "users": None, "reviews": None, "normalized": None},
        "value_evidence": "MCP 官方 registry 收录；领域初分为关键词启发式，待评审确认",
        "review_status": "draft",
        "healthy": True,
        "capabilities": [{
            "domain": domain,
            "intents": [title] + [w for w in desc.replace("/", " ").split()[:6] if len(w) > 2],
            "inputs": ["text"],
            "outputs": ["json"],
            "accessibility": "mcp",
            "cost_per_call": COST_DEFAULT,
            "latency": LATENCY_DEFAULT,
            "mcp_remotes": [r.get("url", "") for r in remotes if r.get("url")],
        }],
    }


def dedupe_latest(entries: list[dict]) -> list[dict]:
    """按 server.name 去重，优先 isLatest，否则保留最高 version。"""
    best: dict[str, dict] = {}
    for e in entries:
        srv = e.get("server", e)
        meta = srv.get("_meta", e.get("_meta", {}))
        official = meta.get("io.modelcontextprotocol.registry/official", {})
        key = srv.get("name", "")
        is_latest = official.get("isLatest", False)
        version = srv.get("version", "0")
        cur = best.get(key)
        if cur is None or (is_latest and not cur[1]) or (not cur[1] and version > cur[0]):
            best[key] = (version, is_latest, e)
    return [v[2] for v in best.values()]


def iter_pages(limit: int = 100, start_cursor: str | None = None, max_pages: int = 5):
    """逐页产出 (entries, next_cursor)。cursor 需 url 编码。"""
    cursor = start_cursor
    for _ in range(max_pages):
        url = f"{API}?limit={limit}"
        if cursor:
            url += "&cursor=" + urllib.parse.quote(cursor, safe="")
        data = fetch_json(url)
        yield data.get("servers", []), data.get("metadata", {}).get("nextCursor")
        cursor = data.get("metadata", {}).get("nextCursor")
        if not cursor:
            break


def iter_servers(limit: int = 100, max_pages: int = 5):
    for page, _next in iter_pages(limit=limit, max_pages=max_pages):
        yield from page


def _cursor_path(db: str) -> str:
    return db + ".mcp_cursor"


def main() -> None:
    p = argparse.ArgumentParser(description="拉取 MCP registry 生成 v2 草稿卡")
    p.add_argument("--pages", type=int, default=2)
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--out", default="data/registry_drafts/mcp.jsonl")
    p.add_argument("--db", default=None, help="直写 SQLite 注册表（草稿状态）")
    args = p.parse_args()

    if args.db:
        from ..cards import parse_app
        from ..store import Store
        store = Store(args.db)
        # 断点续传：游标侧车文件存在则自动续拉，全部完成后删除
        cursor = None
        if os.path.exists(_cursor_path(args.db)):
            with open(_cursor_path(args.db), encoding="utf-8") as f:
                cursor = f.read().strip() or None
            if cursor:
                print(f"从断点续拉：cursor={cursor[:40]}...", flush=True)
        total = 0
        try:
            for page, next_cursor in iter_pages(limit=args.limit,
                                                start_cursor=cursor, max_pages=args.pages):
                for e in dedupe_latest(page):
                    store.upsert_draft(parse_app(server_to_app(e)))
                total += len(page)
                print(f"  已拉 {total} 条", flush=True)
                if next_cursor:
                    with open(_cursor_path(args.db), "w", encoding="utf-8") as f:
                        f.write(next_cursor)
                if not next_cursor:
                    break
        finally:
            store.rebuild_idf()
            store.close()
        if os.path.exists(_cursor_path(args.db)):
            os.remove(_cursor_path(args.db))
        print(f"拉取 {total} 条记录 -> {args.db}（draft）")
        return

    entries = list(iter_servers(limit=args.limit, max_pages=args.pages))
    unique = dedupe_latest(entries)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        for e in unique:
            f.write(json.dumps(server_to_app(e), ensure_ascii=False) + "\n")
    print(f"拉取 {len(entries)} 条记录，去重后 {len(unique)} 张草稿卡 -> {args.out}")


if __name__ == "__main__":
    main()
