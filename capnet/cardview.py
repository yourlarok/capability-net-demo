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
AUTH = {
    "bearer": "请求头 `Authorization: Bearer <Key>`", "token": "请求头 `Authorization: Token <Key>`",
    "oauth2": "OAuth 2.0 授权（先创建开发者应用，再由账号所有者授权）",
    "ak_sk_jwt": "AccessKey + SecretKey 生成 JWT，放进 `Authorization: Bearer`",
    "ak_sk_sign": "AccessKey + SecretKey 对请求签名", "password": "连接密码", "none": "无需鉴权（本地服务）",
}


def bar(x: float, n: int = 10) -> str:
    x = max(0.0, min(1.0, x))
    full = round(x * n)
    return "▰" * full + "▱" * (n - full)


def access(o: dict) -> str:
    icon, label, _ = ACCESS.get(o.get("accessibility", ""), ("🤖", o.get("accessibility", "?"), True))
    api = (o.get("access") or {}).get("api") or {}
    if o.get("accessibility") == "api" and api:
        label += " · 免鉴权" if api.get("auth") == "none" else " · 需授权" if api.get("auth") == "oauth2" else " · 需 Key"
    return f"{icon} {label}"


def auth_text(auth: str) -> str:
    if auth.startswith("header:"):
        return f"请求头 `{auth[7:]}: <Key>`"
    return AUTH.get(auth, auth)


def link(label: str, url: str) -> str:
    return f"[{label}]({url})"


def access_lines(o: dict) -> list[str]:
    """接入渠道：鉴权方式与凭证申请入口、文档端点、安装命令、下载/网页版、找人渠道。"""
    a = o.get("access") or {}
    if a.get("demo"):
        return ["- **接入渠道**　🧪 示例卡（模拟数据，用于演示路由打分），没有真实接入渠道"
                + (f"；{a['note']}" if a.get("note") else "")]
    if not a:
        return ["- **接入渠道**　⚠ 卡片未登记接入渠道（已知缺陷，请按工具名搜索官网）"]
    out = ["- **接入渠道**"]
    api = a.get("api")
    if api:
        key = []
        if api.get("env"):
            key.append(f"环境变量 `{api['env']}`")
        if api.get("key_url"):
            key.append(link("申请凭证", api["key_url"]))
        if not api.get("self_serve", True):
            key.append("需申请开通 / 审核")
        out.append(f"  - 🔑 **鉴权**　{auth_text(api.get('auth', ''))}" + ("　·　" + " · ".join(key) if key else ""))
        doc = [link("API 文档", api["docs"])] if api.get("docs") else []
        if api.get("base_url"):
            doc.append(f"端点 `{api['base_url']}`")
        if doc:
            out.append("  - 📘 **文档**　" + " · ".join(doc))
        if api.get("note"):
            out.append(f"  - 💡 {api['note']}")
    cli = a.get("cli") or {}
    if cli.get("install"):
        out.append(f"  - 📦 **安装**　`{cli['install']}`")
    if cli.get("run"):
        out.append(f"  - ⌨️ **命令行**　`{cli['run']}`" + (f"　{link('文档', cli['docs'])}" if cli.get("docs") else ""))
    if cli.get("note"):
        out.append(f"  - 💡 {cli['note']}")
    for r in (a.get("mcp") or {}).get("remotes") or []:
        out.append(f"  - 🔌 **MCP**　`{r}`")
    if a.get("web"):
        out.append(f"  - 🌐 **网页版**　{a['web']}")
    if a.get("download"):
        out.append(f"  - ⬇️ **下载**　{a['download']}")
    if a.get("repo"):
        out.append(f"  - 🧩 **源码**　{a['repo']}")
    if a.get("hire"):
        out.append("  - 🧑‍💼 **找人渠道**　" + " · ".join(link(h["name"], h["url"]) for h in a["hire"]))
    if a.get("note"):
        out.append(f"  - ℹ️ {a['note']}")
    return out


def access_short(o: dict) -> str:
    """表格里的一格：最关键的接入入口。"""
    a = o.get("access") or {}
    if a.get("demo"):
        return "🧪 示例卡"
    api = a.get("api") or {}
    if o.get("accessibility") == "api" and api.get("key_url"):
        return link("🔑 申请 Key", api["key_url"]) + (f" `{api['env']}`" if api.get("env") else "")
    if o.get("accessibility") == "cli" and (a.get("cli") or {}).get("install"):
        return f"`{a['cli']['install']}`"
    for k, label in (("web", "🌐 网页版"), ("download", "⬇️ 下载"), ("repo", "🧩 源码")):
        if a.get(k):
            return link(label, a[k])
    if a.get("hire"):
        return link("🧑‍💼 " + a["hire"][0]["name"], a["hire"][0]["url"])
    return "—"


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
    out += access_lines(o)
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
        out.append("- **⚠ 注意**　人工工具：只能推荐给你操作，agent 不会假装调用它；按上面的接入渠道获取")
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
    out = ["| | 工具 | 调用方式 | 接入 | 成本 | 速度 | 数据 | 说明 |", "|:-:|---|---|---|---|:-:|---|---|"]
    for i, o in enumerate(opts):
        star = "⭐" if i == 0 else RANK[i]
        d = "🆕 探索期" if o.get("is_new") else f"{o['calls']:,} 次" + (
            f" · {o['success_rate']:.0%}" if o.get("success_rate") is not None else "")
        desc = o["description"]
        desc = desc if len(desc) <= 36 else desc[:35] + "…"
        out.append(f"| {star} | **{o['name']}** `{o['value']}` | {access(o)} | {access_short(o)} | {cost(o)} "
                   f"| {LATENCY.get(o['latency'], o['latency'])} | {d} | {desc} |")
    return out


def access_checklist(opts: list[dict]) -> list[str]:
    """计划用：把首选工具需要的账号 / Key / 安装汇总成一张准备清单。"""
    rows, seen = [], set()
    for o in opts:
        aid = o.get("app_id") or o["value"]
        if aid in seen:
            continue
        seen.add(aid)
        a = o.get("access") or {}
        api = a.get("api") or {}
        if a.get("demo"):
            need = "🧪 示例卡，无需准备"
        elif o.get("accessibility") == "api" and api:
            need = ("无需鉴权，本地启动即可" if api.get("auth") == "none"
                    else f"`{api['env']}`" if api.get("env") else auth_text(api.get("auth", "")))
        elif o.get("accessibility") == "cli":
            need = "本地安装"
        elif a.get("hire"):
            need = "找到合适的人"
        else:
            need = "账号 / 安装软件"
        rows.append(f"| **{o['name']}** | {access(o)} | {need} | {access_short(o)} |")
    if not rows:
        return []
    return ["| 工具 | 方式 | 需要准备 | 入口 |", "|---|---|---|---|"] + rows
