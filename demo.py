#!/usr/bin/env python3
"""capnet demo CLI。

  python3 demo.py import --cards data/cards.json --seeds data/telemetry_seed.json --db data/registry.db
  python3 demo.py search "给游戏画像素风精灵图" --db data/registry.db --markdown
  python3 demo.py rank --domain image.sprite --explain
  python3 demo.py board "画精灵图" "背景音乐" --open
  python3 demo.py simulate --domain image.sprite --rounds 600
  python3 demo.py stats --db data/registry.db
  python3 demo.py approve image.gen --db data/registry.db
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from capnet.cards import load_apps, load_cards, parse_app
from capnet.domains import load_profile
from capnet.gaps import log_gap, summarize
from capnet.render import render_html, render_markdown, render_terminal
from capnet.scoring import ScoreContext, Weights
from capnet.search import as_options, search, search_store
from capnet.simulate import simulate
from capnet.store import Store
from capnet.telemetry import Telemetry

BASE = os.path.dirname(os.path.abspath(__file__))
CARDS = os.path.join(BASE, "data", "cards.json")
SEED = os.path.join(BASE, "data", "telemetry_seed.json")
PROFILE = os.path.join(BASE, "data", "user_profile.json")
DB = os.path.join(BASE, "data", "registry.db")
GAPS = os.path.join(BASE, "data", "gaps.jsonl")


def run_search(args, query, k=None):
    """双后端：--db 存在走 store 快速通道，否则走内存 JSON。"""
    profile = load_profile(getattr(args, "profile", PROFILE))
    max_cost = getattr(args, "max_cost", None)
    latency = getattr(args, "latency", None)
    half_life = getattr(args, "half_life", 30.0)
    db = getattr(args, "db", None)
    if db and os.path.exists(db):
        store = Store(db)
        try:
            ctx = ScoreContext(
                total_selections=store.total_selections(),
                weights=Weights(half_life=half_life),
                max_cost=max_cost, required_latency=latency,
            )
            return search_store(store, query, ctx, domain=args.domain,
                                k=k or args.k, profile=profile)
        finally:
            store.close()
    cards = load_cards(CARDS)
    tel = Telemetry.load(seed_path=SEED)
    stats_by_id = tel.stats(now=0.0, half_life=half_life)
    ctx = ScoreContext(
        total_selections=sum(s.selections for s in stats_by_id.values()),
        weights=Weights(half_life=half_life),
        max_cost=max_cost, required_latency=latency,
    )
    return search(cards, stats_by_id, query, ctx, domain=args.domain,
                  k=k or args.k, profile=profile)


def print_ranked(trace, explain: bool, options_mode: bool = False, markdown_mode: bool = False) -> None:
    print(f"漏斗: 注册表 {trace.total} -> 过滤 {trace.after_filter} -> 召回 {trace.after_recall} -> 精排 {len(trace.ranked)}")
    if markdown_mode:
        print(render_markdown(as_options(trace)))
        return
    if options_mode:
        print(json.dumps(as_options(trace), ensure_ascii=False, indent=2))
        return
    if explain:
        header = f"{'#':>2} {'card':<28} {'rel':>5} {'Q':>6} {'R':>6} {'C':>6} {'U':>6} {'costF':>5} {'latF':>5} {'Score':>7} {'final':>7}"
        print(header)
        print("-" * len(header))
        for i, rc in enumerate(trace.ranked, 1):
            b = rc.breakdown
            print(f"{i:>2} {rc.card.id:<28} {rc.relevance:>5.2f} {b.quality:>6.3f} {b.recognition:>6.3f} "
                  f"{b.ctr:>6.3f} {b.ucb:>6.3f} {b.cost_fit:>5.2f} {b.latency_fit:>5.2f} {b.score:>7.4f} {rc.final:>7.4f}")
    else:
        for i, rc in enumerate(trace.ranked, 1):
            print(f"{i:>2} {rc.card.id:<28} final={rc.final:.4f} ({rc.card.name})")


def cmd_rank(args) -> None:
    print_ranked(run_search(args, None), args.explain, args.options, args.markdown)


def cmd_search(args) -> None:
    trace = run_search(args, args.query)
    print_ranked(trace, args.explain, args.options, args.markdown)
    if trace.gap:
        log_gap(GAPS, args.query, args.domain, "no_distinctive_match")
        print("注册表答不了这个查询（特征词无命中）：应上报/询问，而不是写代码模拟。缺口已记入 data/gaps.jsonl。")


def cmd_board(args) -> None:
    boards = []
    for q in args.queries:
        trace = run_search(args, q, k=3)
        if trace.gap:
            log_gap(GAPS, q, getattr(args, "domain", None), "board_no_distinctive_match")
        boards.append({"query": q, "trace": trace, "options": as_options(trace)})
        print(render_terminal(boards[-1]["options"], query=q))
        print()
    registry_size = boards[0]["trace"].total if boards else 0
    render_html(boards, args.html, registry_size=registry_size)
    print(f"HTML 选择板已写入 {args.html}")
    if args.open:
        subprocess.run(["open", args.html], check=False)


def cmd_simulate(args) -> None:
    cards = load_cards(CARDS)
    tel = Telemetry.load(seed_path=SEED)
    result = simulate(cards, tel, domain=args.domain, rounds=args.rounds, seed=args.seed,
                      temperature=args.temperature, snapshot_every=args.snapshot,
                      half_life=args.half_life)
    for r, trace in result.snapshots:
        print(f"\n=== 第 {r} 轮  ranking ({args.domain}) ===")
        print_ranked(trace, explain=False)
    print(f"\n=== 终态 第 {args.rounds} 轮 ===")
    print_ranked(result.final, explain=args.explain)
    print("\n被选中次数:")
    for card_id, n in sorted(result.picks.items(), key=lambda x: -x[1]):
        print(f"  {card_id:<28} {n}")


def cmd_import(args) -> None:
    store = Store(args.db)
    try:
        n = 0
        if args.cards:
            n += store.upsert_apps(load_apps(args.cards))
        if args.drafts:
            with open(args.drafts, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        store.upsert_draft(parse_app(json.loads(line)))
                        n += 1
        if args.seeds:
            from capnet.scoring import Stats
            with open(args.seeds, encoding="utf-8") as f:
                for entry_id, counts in json.load(f).items():
                    store.add_seed(entry_id, Stats(**counts))
        store.rebuild_idf()
        c = store.counts()
        print(f"导入 {n} 个条目；库内共 {c['entries']}（approved {c['approved']} / draft {c['drafts']}）")
    finally:
        store.close()


def cmd_stats(args) -> None:
    store = Store(args.db)
    try:
        c = store.counts()
        print(f"条目 {c['entries']}（approved {c['approved']} / draft {c['drafts']}）")
        for domain, n in list(c["by_domain"].items())[:20]:
            print(f"  {domain:<24} {n}")
    finally:
        store.close()


def cmd_approve(args) -> None:
    store = Store(args.db)
    try:
        n = store.approve(args.prefix)
        print(f"批准 {n} 个条目（前缀/领域: {args.prefix}）")
    finally:
        store.close()


def cmd_gaps(args) -> None:
    rows = summarize(args.file)
    if not rows:
        print("暂无缺口记录。")
        return
    print(f"缺口 {sum(r['count'] for r in rows)} 次 / {len(rows)} 类（高频优先）：")
    for r in rows[:30]:
        dom = f"[{r['domain']}] " if r.get("domain") else ""
        print(f"  {r['count']:>4}×  {dom}{r['query']}  ({r['reason']})")


def cmd_plan(args) -> None:
    from capnet.planner import plan, render_plan
    from capnet.registry import Registry
    reg = Registry.load()
    p = plan(args.query, reg, k=args.k, max_cost=args.max_cost)
    if not p.matched:
        log_gap(GAPS, args.query, None, "no_workflow")
    for sl in p.gaps:
        log_gap(GAPS, args.query, sl.domain, "slot_unfilled")
    text = render_plan(p)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        print(f"计划已写入 {args.out}")
    else:
        print(text)
    if args.trace:
        from dataclasses import asdict
        print("\n--- 执行后补全 ok/retries/accepted 再上报的轨迹模板 ---")
        print(json.dumps(asdict(p.trace_template("me")), ensure_ascii=False, indent=2))


def cmd_eval(args) -> None:
    from capnet.evaluate import render_report, run_eval
    from capnet.registry import Registry
    rows, summary = run_eval(Registry.load(), args.file)
    text = render_report(rows, summary)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(text + "\n")
    print(text.split("\n## 逐条")[0])
    print(f"完整报告：{args.out}")


def cmd_induce(args) -> None:
    from capnet.registry import Registry
    from capnet.traces import induce, load_traces
    res = induce(load_traces(args.traces), existing=Registry.load().workflows,
                 min_support=args.min_support)
    if not res:
        print("没有达到支持度门槛的模式。")
    for r in res:
        tag = f"强化已有 {r.reinforces}" if r.reinforces else "新工作流草稿"
        print(f"[{tag}] {r.workflow.name}  支持 {r.support} 人 · 接受率 {r.success_rate:.0%}")


def main() -> None:
    p = argparse.ArgumentParser(description="capnet: 能力卡注册表与打分路由")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--k", type=int, default=5)
        sp.add_argument("--half-life", type=float, default=30.0)
        sp.add_argument("--explain", action="store_true")
        sp.add_argument("--options", action="store_true", help="输出给用户选择的选项卡 JSON")
        sp.add_argument("--markdown", action="store_true", help="输出可粘贴进回复的 Markdown 卡片")
        sp.add_argument("--db", default=None, help="SQLite 注册表路径（存在则走快速通道）")
        sp.add_argument("--profile", default=PROFILE, help="用户领域画像 JSON")

    sp = sub.add_parser("rank", help="按领域打分排序")
    sp.add_argument("--domain", default=None)
    common(sp)
    sp.set_defaults(fn=cmd_rank)

    sp = sub.add_parser("search", help="语义检索 + 打分精排")
    sp.add_argument("query")
    sp.add_argument("--domain", default=None)
    sp.add_argument("--max-cost", type=float, default=None)
    sp.add_argument("--latency", choices=["fast", "medium", "slow"], default=None)
    common(sp)
    sp.set_defaults(fn=cmd_search)

    sp = sub.add_parser("board", help="多查询能力选择板：终端卡片 + 自包含 HTML")
    sp.add_argument("queries", nargs="+")
    sp.add_argument("--html", default=os.path.join(BASE, "board.html"))
    sp.add_argument("--open", action="store_true", help="生成后在浏览器打开 (macOS)")
    sp.add_argument("--max-cost", type=float, default=None)
    sp.add_argument("--latency", choices=["fast", "medium", "slow"], default=None)
    common(sp)
    sp.set_defaults(fn=cmd_board)

    sp = sub.add_parser("simulate", help="模拟 planner 行为，观察排名演化")
    sp.add_argument("--domain", default="image.sprite")
    sp.add_argument("--rounds", type=int, default=600)
    sp.add_argument("--seed", type=int, default=7)
    sp.add_argument("--temperature", type=float, default=0.08)
    sp.add_argument("--snapshot", type=int, default=100)
    sp.add_argument("--half-life", type=float, default=150.0)
    sp.add_argument("--k", type=int, default=5)
    sp.add_argument("--explain", action="store_true")
    sp.set_defaults(fn=cmd_simulate)

    sp = sub.add_parser("import", help="导入卡/草稿/遥测种子到 SQLite 注册表")
    sp.add_argument("--cards", default=None)
    sp.add_argument("--drafts", default=None, help="JSONL 草稿文件")
    sp.add_argument("--seeds", default=None)
    sp.add_argument("--db", default=DB)
    sp.set_defaults(fn=cmd_import)

    sp = sub.add_parser("stats", help="注册表统计")
    sp.add_argument("--db", default=DB)
    sp.set_defaults(fn=cmd_stats)

    sp = sub.add_parser("approve", help="按 app_id 或领域批准草稿卡")
    sp.add_argument("prefix")
    sp.add_argument("--db", default=DB)
    sp.set_defaults(fn=cmd_approve)

    sp = sub.add_parser("gaps", help="能力缺口聚合：注册表答不出来的查询，按频次排序")
    sp.add_argument("--file", default=GAPS)
    sp.set_defaults(fn=cmd_gaps)

    sp = sub.add_parser("plan", help="复杂任务 -> 成熟工作流 -> 逐阶段方法 + 工具填槽")
    sp.add_argument("query")
    sp.add_argument("--k", type=int, default=3)
    sp.add_argument("--max-cost", type=float, default=None)
    sp.add_argument("--out", default=None, help="把 Markdown 计划写入文件")
    sp.add_argument("--trace", action="store_true", help="附带待上报的轨迹模板")
    sp.set_defaults(fn=cmd_plan)

    sp = sub.add_parser("eval", help="批量领域问题评测：工作流命中、槽位覆盖、缺口识别")
    sp.add_argument("--file", default=os.path.join(BASE, "data", "eval", "queries.json"))
    sp.add_argument("--out", default=os.path.join(BASE, "data", "eval", "report.md"))
    sp.set_defaults(fn=cmd_eval)

    sp = sub.add_parser("induce", help="从轨迹 JSONL 归纳工作流草稿")
    sp.add_argument("traces")
    sp.add_argument("--min-support", type=int, default=3)
    sp.set_defaults(fn=cmd_induce)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
