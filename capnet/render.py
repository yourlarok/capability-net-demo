"""选项卡渲染：终端 ANSI 卡片 + 自包含 HTML 能力选择板。"""
from __future__ import annotations

import html
import shutil
import textwrap
import time

_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_CYAN = "\033[36m"


def _bar(value: float, width: int = 18) -> str:
    filled = int(round(max(0.0, min(1.0, value)) * width))
    return "█" * filled + "░" * (width - filled)


def render_terminal(options: list[dict], query: str | None = None) -> str:
    """左边框式卡片：规避 CJK 宽字符对齐问题，任何终端都能正确渲染。"""
    width = min(shutil.get_terminal_size((88, 20)).columns, 88)
    out: list[str] = []
    if query:
        out.append(f"{_BOLD}能力缺口: {query}{_RESET}")
    for i, o in enumerate(options, 1):
        label = o["label"]
        label_txt = f"{_GREEN}{_BOLD}{label}{_RESET}" if o.get("recommended") else f"{_BOLD}{label}{_RESET}"
        out.append(f"{_CYAN}╭─{label_txt}{_CYAN} · {o['domain']}{_RESET}")
        for line in textwrap.wrap(o["description"], width=width - 8) or [""]:
            out.append(f"{_CYAN}│{_RESET} {line}")
        out.append(f"{_CYAN}│{_RESET} final  {_GREEN}{_bar(o['final'])}{_RESET}  {o['final']:.3f}")
        out.append(
            f"{_CYAN}│{_RESET} {_DIM}Q {o['quality']:.2f}   R {o['recognition']:.2f}   "
            f"C {o['ctr']:.2f}   U {o['ucb']:.2f}{_RESET}"
        )
        chips = f"{o['cost']}/次 · {o['latency']} · {o['calls']} 次调用 · {o['vendor']}"
        out.append(f"{_CYAN}│{_RESET} {chips}")
        if o.get("is_new"):
            out.append(f"{_CYAN}│{_RESET} {_YELLOW}[新卡·探索性试用]{_RESET}")
        out.append(f"{_CYAN}╰{'─' * 40}{_RESET}")
        if i < len(options):
            out.append("")
    return "\n".join(out)


def render_markdown(options: list[dict], query: str | None = None) -> str:
    """输出可直接粘贴进 agent 回复正文的 Markdown 卡片（对话窗口内渲染）。"""
    lines: list[str] = []
    if query:
        lines.append(f"**能力缺口：{query}**")
        lines.append("")
    for o in options:
        head = f"**{o['label']}** · `{o['domain']}`"
        if o.get("is_new"):
            head += " · `[新卡·探索性试用]`"
        lines.append(head)
        lines.append(f"> {o['description']}")
        lines.append(
            f"> final `{_bar(o['final'], width=12)}` **{o['final']:.3f}**"
            f" — Q {o['quality']:.2f} · R {o['recognition']:.2f} · C {o['ctr']:.2f} · U {o['ucb']:.2f}"
        )
        lines.append(f"> 成本 {o['cost']}/次 · {o['latency']} · {o['calls']} 次调用 · {o['vendor']}")
        lines.append("")
    return "\n".join(lines)


_CSS = """
:root { color-scheme: dark; }
* { box-sizing: border-box; margin: 0; }
body { background: #0d1117; color: #e6edf3; font: 15px/1.6 -apple-system, "PingFang SC", sans-serif; }
main { max-width: 1180px; margin: 0 auto; padding: 40px 24px 80px; }
h1 { font-size: 26px; letter-spacing: 1px; }
.sub { color: #8b949e; margin: 6px 0 36px; }
.board { margin-bottom: 48px; }
.board > h2 { font-size: 18px; margin-bottom: 4px; }
.board > h2::before { content: "能力缺口 · "; color: #8b949e; font-weight: 400; }
.funnel { color: #8b949e; font-size: 13px; margin-bottom: 16px; }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 16px; }
.card {
  --hue: 210;
  background: #161b22; border: 1px solid #2d333b; border-radius: 14px;
  padding: 18px 18px 14px; display: flex; flex-direction: column; gap: 10px;
  transition: transform .15s ease, box-shadow .15s ease;
}
.card:hover { transform: translateY(-3px); box-shadow: 0 8px 28px rgba(0,0,0,.45); }
.card.rec { border-color: hsl(var(--hue) 70% 55%); box-shadow: 0 0 0 1px hsl(var(--hue) 70% 55% / .5), 0 6px 24px hsl(var(--hue) 70% 45% / .15); }
.card header { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.card h3 { font-size: 17px; }
.badge { font-size: 11px; padding: 2px 9px; border-radius: 999px; white-space: nowrap; }
.badge.rec { background: hsl(var(--hue) 70% 45% / .2); color: hsl(var(--hue) 90% 65%); border: 1px solid hsl(var(--hue) 70% 55% / .6); }
.badge.new { background: #d2992230; color: #e3b341; border: 1px solid #d2992266; }
.domain { font-size: 12px; color: hsl(var(--hue) 60% 65%); font-family: ui-monospace, monospace; }
.desc { color: #b1bac4; font-size: 13.5px; flex: 1; }
.score { display: flex; align-items: center; gap: 10px; }
.score b { font-size: 15px; min-width: 44px; text-align: right; }
.bar { flex: 1; height: 8px; background: #2d333b; border-radius: 999px; overflow: hidden; }
.bar i { display: block; height: 100%; border-radius: 999px;
  background: linear-gradient(90deg, hsl(var(--hue) 80% 45%), hsl(var(--hue) 90% 62%)); }
.comps { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; }
.comp { font-size: 11px; color: #8b949e; }
.comp .bar { height: 5px; margin-top: 3px; }
.comp b { color: #c9d1d9; font-weight: 600; }
footer { display: flex; flex-wrap: wrap; gap: 6px; border-top: 1px solid #2d333b; padding-top: 10px; }
.chip { font-size: 11.5px; color: #9da7b3; background: #21262d; border-radius: 6px; padding: 2px 8px; }
footer .chip:first-child { color: #f0f6fc; }
"""


def _domain_hue(domain: str) -> int:
    return abs(hash(domain)) % 360


def _card_html(o: dict) -> str:
    hue = _domain_hue(o["domain"])
    badges = ""
    if o.get("recommended"):
        badges += '<span class="badge rec">推荐</span>'
    if o.get("is_new"):
        badges += '<span class="badge new">新卡·探索性试用</span>'
    comps = "".join(
        f'<div class="comp">{label} <b>{disp:.2f}</b><div class="bar"><i style="width:{min(1.0, bar) * 100:.0f}%"></i></div></div>'
        for label, disp, bar in [("Q 质量", o["quality"], o["quality"]),
                                  ("R 认可", o["recognition"], o["recognition"]),
                                  ("C 效率", o["ctr"], o["ctr"]),
                                  ("U 探索", o["ucb"], o["ucb"] / 2.0)]
    )
    return f"""
  <article class="card{' rec' if o.get('recommended') else ''}" style="--hue:{hue}">
    <header><h3>{html.escape(o['name'])}</h3><span>{badges}</span></header>
    <div class="domain">{html.escape(o['domain'])} · {html.escape(o['vendor'] or 'unknown')}</div>
    <p class="desc">{html.escape(o['description'])}</p>
    <div class="score"><div class="bar"><i style="width:{o['final'] * 100:.1f}%"></i></div><b>{o['final']:.3f}</b></div>
    <div class="comps">{comps}</div>
    <footer>
      <span class="chip">{o['cost']}/次</span>
      <span class="chip">{html.escape(o['latency'])}</span>
      <span class="chip">{o['calls']} 次调用</span>
    </footer>
  </article>"""


def render_html(boards: list[dict], path: str, registry_size: int = 0) -> None:
    """boards: [{"query": str, "options": [...], "trace": FunnelTrace}]，写自包含 HTML。"""
    sections = []
    for b in boards:
        t = b.get("trace")
        funnel = ""
        if t is not None:
            funnel = (f'<p class="funnel">漏斗：注册表 {t.total} → 过滤 {t.after_filter} '
                      f'→ 召回 {t.after_recall} → 呈现 {len(b["options"])}</p>')
        cards = "".join(_card_html(o) for o in b["options"]) or '<p class="funnel">注册表无匹配能力</p>'
        sections.append(
            f'<section class="board"><h2>{html.escape(b["query"])}</h2>{funnel}'
            f'<div class="cards">{cards}</div></section>'
        )
    doc = f"""<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>capnet 能力选择板</title><style>{_CSS}</style></head>
<body><main>
<h1>capnet 能力选择板</h1>
<p class="sub">{len(boards)} 个能力缺口 · 注册表 {registry_size} 张卡 · 生成于 {time.strftime('%Y-%m-%d %H:%M:%S')}</p>
{''.join(sections)}
</main></body></html>"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(doc)
