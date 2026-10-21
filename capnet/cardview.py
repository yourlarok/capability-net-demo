"""能力卡的对话窗口渲染：纯 GitHub 风格 Markdown，Claude Code / Cursor / Claude.ai / ChatGPT 都能显示。

两种形态：
  render_capabilities  单个需求的推荐：总览表 + 每张卡的详情（调用方式、擅长、成本速度、评分、数据量、推荐理由）
  slot_table           工作流计划里每个工具槽的紧凑对比表
只用 Markdown 原语（标题、表格、列表、引用、行内代码）和 emoji，完全不依赖 HTML，终端里也不会乱。
"""
from __future__ import annotations

from .intent import FAMILIES, card_families
from .tokens import tokenize

ACCESS = {
    "api": ("🤖", "API 直连", True), "cli": ("🤖", "命令行", True), "mcp": ("🤖", "MCP", True),
    "gui": ("👤", "人工·软件界面", False), "manual": ("👤", "人工服务", False),
}
LATENCY = {"fast": "⚡ 快", "medium": "⏱ 中", "slow": "🐢 慢"}
MARKET = {"cn": "🇨🇳 国内", "global": "🌍 海外"}
RANK = "①②③④⑤⑥⑦⑧⑨⑩"


def bar(x: float, n: int = 10) -> str:
    x = max(0.0, min(1.0, x))
    full = round(x * n)
    return "▰" * full + "▱" * (n - full)


def access(o: dict) -> str:
    icon, label, _ = ACCESS.get(o.get("accessibility", ""), ("🤖", o.get("accessibility", "?"), True))
    return f"{icon} {label}"


def cost(o: dict) -> str:
    c = o.get("cost") or 0.0
    if c == 0:
        return "按产品定价" if o.get("manual") else "免费"
    return f"${c:g}/次"


def users(n) -> str:
    if not n:
        return ""
    if n >= 1e8:
        return f"约 {n / 1e8:g} 亿用户"
    if n >= 1e4:
        return f"约 {n / 1e4:g} 万用户"
    return f"约 {n:,} 用户"


def data_line(o: dict) -> str:
    if o.get("is_new"):
        u = users(o.get("users"))
        return "🆕 新卡，真实调用数据还不足，属于探索性推荐" + (f"（外部口碑：{u}）" if u else "")
    sr = o.get("success_rate")
    return f"{o['calls']:,} 次真实调用" + (f" · 成功率 {sr:.0%}" if sr is not None else "")


def family_label(domain: str) -> str:
    fams = sorted(card_families(domain))
    return " ".join(FAMILIES[f].split(" ")[0] for f in fams if f in FAMILIES) or "🧩"


def matched_terms(query: str, o: dict, limit: int = 4) -> list[str]:
    q = {t for t in tokenize(query) if len(t) >= 2}
    hits = [i for i in o.get("intents", []) if q & set(tokenize(i))]
    return hits[:limit]


def _markets(o: dict) -> str:
    ms = o.get("markets") or []
    return " · ".join(MARKET.get(m, m) for m in ms) if ms else "不限"


def overview_table(opts: list[dict]) -> list[str]:
    out = ["| # | 工具 | 类型 | 调用方式 | 成本 | 速度 | 综合 |", "|:-:|---|:-:|---|---|:-:|---|"]
    for i, o in enumerate(opts):
        star = " ⭐" if i == 0 else ""
        out.append(f"| {RANK[i]}{star} | **{o['name']}** | {family_label(o['domain'])} | {access(o)} | {cost(o)} "
                   f"| {LATENCY.get(o['latency'], o['latency'])} | `{bar(o['final'], 8)}` {o['final']:.2f} |")
    return out


def card_detail(i: int, o: dict, query: str) -> list[str]:
    _, _, auto = ACCESS.get(o.get("accessibility", ""), ("", "", True))
    head = f"### {RANK[i]} {o['name']}" + ("　⭐ 首选" if i == 0 else "")
    meta = f"`{o['value']}` · {family_label(o['domain'])} {o['domain']}" + (f" · {o['vendor']}" if o.get("vendor") else "")
    out = [head, meta, f"> {o['description']}", ""]
    how = "agent 可以直接调用" if auto else "agent 无法替你操作，我会给出具体操作步骤"
    out.append(f"- **调用方式**　{access(o)} — {how}")
    if o.get("intents"):
        out.append(f"- **擅长**　{' · '.join(o['intents'][:6])}")
    if o.get("inputs") or o.get("outputs"):
        out.append(f"- **输入 → 产出**　{' / '.join(o.get('inputs') or ['—'])} → {' / '.join(o.get('outputs') or ['—'])}")
    out.append(f"- **成本 / 速度 / 市场**　{cost(o)} · {LATENCY.get(o['latency'], o['latency'])} · {_markets(o)}")
    out.append(f"- **评分**　综合 `{bar(o['final'])}` {o['final']:.2f}　"
               f"匹配 {o.get('relevance', 0):.2f} · 质量 {o['quality']:.2f} · 口碑 {o['recognition']:.2f} · 选用率 {o['ctr']:.2f}")
    out.append(f"- **数据**　{data_line(o)}")
    why = []
    terms = matched_terms(query, o)
    if terms:
        why.append("命中「" + "」「".join(terms) + "」")
    if o.get("tier") == 2:
        why.append("属于需求的主要能力类型")
    elif o.get("tier") == 1:
        why.append("属于需求的次要能力类型")
    if i == 0:
        why.append("同类中综合分最高")
    if why:
        out.append(f"- **为什么推荐**　{'；'.join(why)}")
    if not auto:
        out.append("- **⚠ 注意**　人工工具：只能推荐给你操作，agent 不会假装调用它")
    return out + [""]


def render_capabilities(query: str, opts: list[dict], understanding: str, source: str,
                        market: str | None, excluded: int = 0, gap: bool = False) -> str:
    out = [f"## 🔎 能力推荐：{query}", ""]
    meta = [f"**需求理解** {understanding}（{source}）"]
    if market:
        meta.append(f"**市场** {MARKET.get(market, market)}")
    if excluded:
        meta.append(f"已排除 {excluded} 个类型不符的工具")
    out.append("> " + " · ".join(meta))
    out.append("")
    if not opts:
        out.append("**注册表里没有可靠匹配的工具。** 这个需求已记为能力缺口；请不要编造工具，按常识完成或询问用户。")
        return "\n".join(out)
    if gap:
        out.append("⚠ 以下候选与需求的核心词匹配较弱，仅供参考；缺口已记录。\n")
    out += overview_table(opts)
    out.append("")
    for i, o in enumerate(opts):
        out += card_detail(i, o, query)
    out.append("*综合分 = 匹配度 ×（质量 · 口碑 · 选用率 · 探索加成）× 成本/速度/市场适配；"
               "随真实使用数据回流持续更新。*")
    return "\n".join(out)


def slot_table(opts: list[dict]) -> list[str]:
    """工作流计划中单个工具槽的紧凑对比表。"""
    out = ["| | 工具 | 调用方式 | 成本 | 速度 | 数据 | 说明 |", "|:-:|---|---|---|:-:|---|---|"]
    for i, o in enumerate(opts):
        star = "⭐" if i == 0 else RANK[i]
        d = "🆕 探索期" if o.get("is_new") else f"{o['calls']:,} 次" + (
            f" · {o['success_rate']:.0%}" if o.get("success_rate") is not None else "")
        desc = o["description"]
        desc = desc if len(desc) <= 36 else desc[:35] + "…"
        out.append(f"| {star} | **{o['name']}** `{o['value']}` | {access(o)} | {cost(o)} "
                   f"| {LATENCY.get(o['latency'], o['latency'])} | {d} | {desc} |")
    return out
