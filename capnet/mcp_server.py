"""capnet MCP server（stdio，零依赖）。

任何支持 MCP 的 agent 接入后即获得完整路由——与是否上传轨迹无关。
工具：
  plan_task        复杂任务 -> 成熟工作流 + 逐阶段方法 + 工具推荐
  find_capability  单个能力缺口 -> 工具候选
  list_workflows / get_workflow  浏览工作流库
  finish_task      任务结束时自动记录隐式反馈（结果/纠正/跳过/所用工具/方法），按用户事先选定的上报方式处理
  set_share_mode   记录用户选定的上报方式 auto / ask / off（只在用户明确选择后调用）
  summarize_trace  把一次执行整理成"只含客观思路"的清理后预览（本地，不上传）
  submit_trace     用户明确同意后上传该预览；consent=false 则删除预览
同一个 Server 被三种接入方式复用：stdio MCP（本文件 main）、HTTP MCP / REST / OpenAPI（http_api.py）、
Python 库（Server().call）。使用规范只有一份：agent_rules.md（skill、MCP prompt、/v1/rules 都读它）。
运行：python -m capnet.mcp_server   （或 demo.py mcp）
"""
from __future__ import annotations

import json
import os
import sys
import threading
from dataclasses import asdict

from . import community, hub
from .gaps import log_gap
from .paths import gaps_path
from .cardview import render_capabilities
from .intent import FAMILIES, describe
from .planner import KEEP_RATIO, infer_market, market_fit, plan, render_plan
from .privacy import load_private_terms
from .registry import Registry
from .scoring import ScoreContext
from .search import as_options, search
from .traces import summarize

GAPS = gaps_path()
VERSION = "0.6.0"
RULES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent_rules.md")


def agent_rules() -> str:
    with open(RULES_PATH, encoding="utf-8") as f:
        return f.read().strip()


# 能力族：由调用方（LLM）判断并声明，比词表推断可靠得多（见 intent.py）
_TYPE_SCHEMA = {
    "type": "array", "items": {"type": "string", "enum": list(FAMILIES)},
    "description": "你对需求所需能力类型的判断（强烈建议填写，可多选）："
                   + "；".join(f"{k}={v.split(' ', 1)[1]}" for k, v in FAMILIES.items())
                   + "。例：'给角色配个声音'→[speech]，'画角色立绘'→[image]，'给视频配乐'→[music]。",
}
DISPLAY_HINT = ("\n\n---\n*（给 agent：请把上面的 Markdown 原样展示给用户，"
                "不要改写成纯文字摘要；需要选择时让用户回复编号。）*")


def _types(a: dict) -> list[str] | None:
    t = a.get("capability_type")
    if isinstance(t, str):
        t = [t]
    return [x for x in t or [] if x in FAMILIES] or None


PROMPTS = [{"name": "capnet_rules", "description": "capnet 使用规范：何时路由、人工工具不得假装调用、上传前必须征得同意"}]

INSTRUCTIONS = (
    "capnet 是能力与工作流路由网络：系统维护的基石 flow + 用户在使用中长出来的社区 flow。"
    "遇到多步骤的专业任务（营销、电商、内容生产、课程、游戏等），先调 plan_task 获取别人走通过的成熟做法与工具推荐；"
    "标注'人工使用'的工具只能推荐给用户操作，不得假装能调用。"
    "任务结束时调 finish_task，按对话中观察到的行为如实填写结果、用户纠正次数、跳过的阶段——不要为此打扰用户。"
    "上报方式由用户事先选定（auto/ask/off）；ask 模式下必须把预览原样给用户看、同意后才 submit_trace。"
    "上报与否不影响任何路由结果。"
)

_STEP_SCHEMA = {
    "type": "object",
    "properties": {
        "stage": {"type": "string", "description": "阶段名，如'逐秒拉片诊断'"},
        "domain": {"type": "string", "description": "能力领域，如 video.understand"},
        "entry_id": {"type": "string", "description": "所用工具的注册表 id（plan_task 返回的 value）；未收录工具留空"},
        "ok": {"type": "boolean"},
        "retries": {"type": "integer", "description": "返工/重试次数"},
        "approach": {"type": "string", "description": "这一步的方法，一句话，不含具体内容/名称/数字"},
    },
    "required": ["stage", "domain", "ok"],
}

_FINISH_PROPS = {
    "problem": {"type": "string", "description": "泛化的问题类型，如'营销视频完播率低'，不写公司/产品名"},
    "industry": {"type": "string", "description": "如 marketing/video"},
    "workflow_id": {"type": "string", "description": "依据的 flow（plan_task 返回的 workflow_id）；没有就留空"},
    "outcome": {"type": "string", "enum": ["accepted", "partial", "abandoned"],
                "description": "按用户表现判断：接受/采用了结果=accepted，只用了一部分=partial，中途放弃或转去别处=abandoned"},
    "corrections": {"type": "integer",
                    "description": "用户纠正、表示不满、要求重来的次数（如'不对''不是这个意思''重来'），没有就是 0"},
    "skipped": {"type": "array", "items": {"type": "string"}, "description": "计划里没有做的阶段名"},
    "steps": {"type": "array", "items": _STEP_SCHEMA, "description": "实际走过的步骤；entry_id 填用户最终选用的工具"},
    "lessons": {"type": "array", "items": {"type": "string"}, "description": "可迁移的经验，最多 5 条"},
    "private_terms": {"type": "array", "items": {"type": "string"},
                      "description": "用户不想外传的词（公司/产品/人名），替换为 [私有]；只用于本次清理"},
    "share_mode": {"type": "string", "enum": ["auto", "ask", "off"],
                   "description": "仅连接远程 capnet 服务时需要：用户开工前选定的上报方式。本机安装时忽略（读本机配置）"},
}

TOOLS = [
    {"name": "plan_task",
     "description": "复杂任务 -> 成熟工作流（阶段/方法/交付物/验收）+ 每个工具槽的推荐工具。未命中时返回能力直搜结果并记缺口。"
                    "标注'人工使用'的工具只能推荐给用户操作，不得假装调用。",
     "inputSchema": {"type": "object", "properties": {
         "query": {"type": "string", "description": "用户的任务描述"},
         "task_type": {"type": "string", "enum": ["produce", "improve", "grow", "launch", "review", "qa"],
                       "description": "agent 对任务类型的判断；qa=问答/排障/购买咨询，不走工作流"},
         "market": {"type": "string", "enum": ["cn", "global"], "description": "目标市场；缺省按语言推断"},
         "capability_type": _TYPE_SCHEMA,
         "max_cost": {"type": "number"}},
         "required": ["query"]}},
    {"name": "find_capability",
     "description": "单个能力需求的工具推荐：返回排版好的能力卡（总览对比表 + 每张卡的调用方式、擅长、成本速度、"
                    "评分、数据量、推荐理由）。请同时传 capability_type 声明能力类型，避免'画画'推荐出配音工具。",
     "inputSchema": {"type": "object", "properties": {
         "query": {"type": "string"}, "capability_type": _TYPE_SCHEMA, "domain": {"type": "string"},
         "k": {"type": "integer", "default": 5}, "market": {"type": "string", "enum": ["cn", "global"]}},
         "required": ["query", "capability_type"]}},
    {"name": "list_workflows", "description": "列出工作流库（id、名称、行业、来源）。",
     "inputSchema": {"type": "object", "properties": {"industry": {"type": "string"}}}},
    {"name": "get_workflow", "description": "取单个工作流的完整定义。",
     "inputSchema": {"type": "object", "properties": {"workflow_id": {"type": "string"}},
                     "required": ["workflow_id"]}},
    {"name": "finish_task",
     "description": "任务结束（用户接受、部分采用或放弃）时调用，记录隐式反馈：结果、用户纠正次数、跳过的阶段、实际步骤与所用工具。"
                    "信息来自你对对话的观察，不要为此询问用户。它会更新工具与 flow 的评分；没有现成 flow 的新做法会成为社区 flow。"
                    "之后按用户事先选定的上报方式处理：auto 自动上报清理后的思路，ask 返回预览（须给用户看并征得同意），off 只在本机生效。",
     "inputSchema": {"type": "object", "properties": _FINISH_PROPS, "required": ["problem", "steps", "outcome"]}},
    {"name": "set_share_mode",
     "description": "记录用户选定的上报方式。只在用户明确做出选择后调用：auto=自动上报清理后的思路，ask=每次先确认，off=不上报。",
     "inputSchema": {"type": "object", "properties": {"mode": {"type": "string", "enum": list(community.SHARE_MODES)}},
                     "required": ["mode"]}},
    {"name": "summarize_trace",
     "description": "把刚完成的任务整理成只含客观解决思路的轨迹，本地清理私有信息并生成预览与 preview_id。"
                    "不上传。返回的预览必须原样展示给用户并询问是否同意上传。",
     "inputSchema": {"type": "object", "properties": {
         "problem": {"type": "string", "description": "泛化的问题类型，如'营销视频播放量低'，不写公司/产品名"},
         "industry": {"type": "string", "description": "如 marketing/video"},
         "workflow_id": {"type": "string"},
         "accepted": {"type": "boolean", "description": "用户是否接受了最终结果"},
         "steps": {"type": "array", "items": _STEP_SCHEMA},
         "lessons": {"type": "array", "items": {"type": "string"}, "description": "可迁移的经验，最多 5 条"},
         "private_terms": {"type": "array", "items": {"type": "string"},
                           "description": "用户不想外传的词（公司/产品/人名），会被替换为 [私有]；只用于本次清理，不保存"}},
         "required": ["problem", "steps", "accepted"]}},
    {"name": "submit_trace",
     "description": "上传 summarize_trace 生成的预览。仅在用户看过预览并明确同意后以 consent=true 调用；"
                    "用户拒绝时以 consent=false 调用以删除预览。",
     "inputSchema": {"type": "object", "properties": {
         "preview_id": {"type": "string"}, "consent": {"type": "boolean"}},
         "required": ["preview_id", "consent"]}},
]


class Server:
    def __init__(self, remote: bool = False):
        # remote=True：作为多人共用的远程服务运行，本服务的 hub 就是社区 hub，
        # 收到的反馈即是上报，所以上报方式必须由每个用户自己选（finish_task.share_mode），不读服务端配置
        self.remote = remote
        self._reg: Registry | None = None
        self._lock = threading.Lock()  # HTTP 模式多线程共享同一个 Server

    @property
    def reg(self) -> Registry:
        with self._lock:
            if self._reg is None:
                community.sync()  # 配置了社区 hub 时拉取社区 flow 与评分（有缓存期，失败沿用旧缓存）
                # 对外推荐只给有真实接入渠道的工具；CAPNET_DEMO_CARDS=1 时保留示例卡（演示路由打分用）
                self._reg = Registry.load(include_demo=os.environ.get("CAPNET_DEMO_CARDS") == "1")
            return self._reg

    def call(self, name: str, args: dict | None = None, client_id: str | None = None) -> tuple[str, bool]:
        """按名调用工具，返回 (文本, 是否出错)。client_id 只用于远程模式区分贡献者（会被哈希）。"""
        text, is_err, _ = self.call_full(name, args, client_id)
        return text, is_err

    def call_full(self, name: str, args: dict | None = None,
                  client_id: str | None = None) -> tuple[str, bool, dict | None]:
        """同 call，额外返回结构化数据（MCP structuredContent / REST data）。"""
        if name not in {t["name"] for t in TOOLS}:
            raise LookupError(f"未知工具 {name}")
        args = dict(args or {})
        try:
            out = (getattr(self, name)(args, client_id) if name in ("summarize_trace", "finish_task")
                   else getattr(self, name)(args))
        except (KeyError, ValueError, TypeError) as e:
            return f"参数错误：{e}", True, None
        return out if len(out) == 3 else (out[0], out[1], None)

    # ---------- 工具实现 ----------

    def plan_task(self, a: dict) -> tuple[str, bool]:
        q = a["query"]
        if a.get("task_type") == "qa":
            return ("这是问答/排障/咨询类请求，不需要工作流，直接回答即可。", False)
        p = plan(q, self.reg, max_cost=a.get("max_cost"), task_type=a.get("task_type"),
                 market=a.get("market"), families=_types(a))
        if not p.matched:
            log_gap(GAPS, q, None, "no_workflow")
        for sl in p.gaps:
            log_gap(GAPS, q, sl.domain, "slot_unfilled")
        slim = lambda opts: [{"id": o["value"], "name": o["name"], "accessibility": o["accessibility"],  # noqa: E731
                              "manual": o["manual"], "access": o.get("access", {})} for o in opts]
        structured = {
            "matched": p.matched,
            "workflow_id": p.workflow.workflow_id if p.workflow else None,
            "relevance": round(p.relevance, 3),
            "slots": [{"stage": sp.stage.name, "slot_id": f.slot.slot_id, "domain": f.slot.domain,
                       "options": slim(f.options)} for sp in p.stages for f in sp.fills],
            "fallback": slim(p.fallback),
        }
        structured["flow"] = community.stats_dict(p.workflow, p.flow_stats) if p.workflow else None
        hint = DISPLAY_HINT
        if community.get_share() is None:
            hint += ("\n*（给 agent：用户还没选上报方式。开工前用一句话问一次：任务结束后匿名思路要「自动上报 / 每次先确认 / 不上报」？"
                     "按回答调 set_share_mode。无论选什么，任务结束时都调 finish_task。）*")
        return render_plan(p) + hint, False, structured

    def find_capability(self, a: dict) -> tuple[str, bool, dict]:
        q, dom, k = a.get("query"), a.get("domain"), int(a.get("k") or 5)
        market = a.get("market") or (infer_market(q) if q else None)
        declared = _types(a)
        reg = self.reg
        ctx = ScoreContext(total_selections=reg.total_selections)
        trace = search(reg.cards, reg.stats, q, ctx, domain=dom, k=k * 2, profile=reg.profile,
                       families=declared, keep_ratio=KEEP_RATIO)
        for rc in trace.ranked:
            rc.final *= market_fit(rc.card, market)
        trace.ranked.sort(key=lambda x: (-x.tier, -x.final))
        trace.ranked = trace.ranked[:k]
        if q and trace.gap:
            log_gap(GAPS, q, dom, "no_distinctive_match")
        opts = as_options(trace, max_options=k)
        if q and trace.gap and not trace.families:
            opts = []  # 既没有类型线索、核心词也没命中：宁缺毋滥
        source = "agent 声明" if declared else ("关键词推断" if trace.families else "未识别")
        text = render_capabilities(q or dom or "", opts, describe(trace.families), source, market,
                                   trace.off_intent, gap=bool(q and trace.gap))
        return text + DISPLAY_HINT, False, {"options": opts, "families": trace.families, "gap": trace.gap}

    def list_workflows(self, a: dict) -> tuple[str, bool]:
        ind = a.get("industry")
        reg = self.reg
        rows = [{"workflow_id": w.workflow_id, "name": w.name, "industry": w.industry,
                 "source": w.source.get("kind"), "stages": len(w.stages),
                 **community.stats_dict(w, reg.flow_stats.get(w.workflow_id))}
                for w in reg.workflows if not ind or w.industry.startswith(ind)]
        return json.dumps(rows, ensure_ascii=False, indent=1), False

    def get_workflow(self, a: dict) -> tuple[str, bool]:
        wf = self.reg.get_workflow(a["workflow_id"])
        if wf is None:
            return f"没有工作流 {a['workflow_id']}", True
        return json.dumps(asdict(wf), ensure_ascii=False, indent=1), False

    def finish_task(self, a: dict, client_id: str | None = None) -> tuple[str, bool]:
        """隐式收集：清理后先入本机 hub（工具 / flow 评分、本机社区 flow），再按事先选定的方式上报。"""
        known = {c.id for c in self.reg.cards}
        extra = [t for t in a.pop("private_terms", None) or [] if isinstance(t, str)]
        chosen = a.pop("share_mode", None)
        a["accepted"] = a.get("outcome") == "accepted"
        s = summarize(a, known, load_private_terms() + extra, contributor_raw=client_id)
        if self.remote:
            return self._finish_remote(s, chosen, known)
        remote = os.environ.get("CAPNET_HUB_URL")
        local = hub.ingest(s.item, known)
        self._reg = None  # 新反馈进入打分，下次调用重新加载
        mode = community.get_share()
        note = f"已记录本次反馈（本机）：{local.get('status')}。"
        if mode == "auto" and remote:
            hub.save_pending(s)
            res = hub.submit(s.preview_id, True, known_entries=known)
            note += f"已按「{community.SHARE_LABEL['auto']}」上报：{res.get('status')}。"
        elif mode == "ask" and remote:
            hub.save_pending(s)
            return (note + "\n\n" + s.render() + "\n\n下一步：用户选的是「每次先确认」。把以上内容原样给用户看，问是否同意上报。"
                    f"同意 → submit_trace(preview_id='{s.preview_id}', consent=true)；不同意 → consent=false。", False)
        elif mode is None:
            note += "用户还没选上报方式，本次只在本机生效；下次开工前问一次用户并调 set_share_mode。"
        elif not remote and mode != "off":
            note += "未配置社区 hub（CAPNET_HUB_URL），本次只在本机生效。"
        else:
            note += "用户选择不上报，只在本机生效。"
        return note + "（不需要告诉用户细节，一句话带过即可。）", False

    def _finish_remote(self, s, mode: str | None, known: set[str]) -> tuple[str, bool]:
        if mode == "auto":
            res = hub.ingest(s.item, known)
            self._reg = None
            return f"已按「{community.SHARE_LABEL['auto']}」上报清理后的思路：{res.get('status')}。", False
        if mode == "ask":
            hub.save_pending(s)
            return (s.render() + "\n\n下一步：把以上内容原样给用户看，问是否同意上报。"
                    f"同意 → submit_trace(preview_id='{s.preview_id}', consent=true)；不同意 → consent=false。", False)
        return ("未上报（用户选择不上报，或还没选上报方式）。远程服务不保存任何内容；"
                "如需记录，请先问用户并在 finish_task 里传 share_mode。"), False

    def set_share_mode(self, a: dict) -> tuple[str, bool]:
        if self.remote:
            return "远程服务不保存个人设置：请记住用户的选择，之后每次 finish_task 传 share_mode。", False
        community.set_share(a["mode"])
        return f"已记录上报方式：{community.SHARE_LABEL[a['mode']]}。随时可以让我改，或运行 `capnet share <auto|ask|off>`。", False

    def summarize_trace(self, a: dict, client_id: str | None = None) -> tuple[str, bool]:
        known = {c.id for c in self.reg.cards}
        extra = [t for t in a.pop("private_terms", None) or [] if isinstance(t, str)]
        s = summarize(a, known, load_private_terms() + extra, contributor_raw=client_id)
        hub.save_pending(s)
        return (s.render() + f"\n\n下一步：把以上内容原样给用户看，问是否同意上传。"
                f"同意 → submit_trace(preview_id='{s.preview_id}', consent=true)；"
                f"不同意 → consent=false。", False)

    def submit_trace(self, a: dict) -> tuple[str, bool]:
        res = hub.submit(a["preview_id"], a.get("consent") is True,
                         known_entries={c.id for c in self.reg.cards})
        if res.get("status") == "accepted":
            self._reg = None  # 新事件进入打分，下次调用重新加载
        return json.dumps(res, ensure_ascii=False), res.get("status") == "error"

    # ---------- JSON-RPC ----------

    def handle(self, msg: dict, client_id: str | None = None) -> dict | None:
        mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
        if mid is None:  # 通知
            return None
        try:
            if method == "initialize":
                result = {"protocolVersion": params.get("protocolVersion", "2025-06-18"),
                          "capabilities": {"tools": {}, "prompts": {}},
                          "serverInfo": {"name": "capnet", "version": VERSION},
                          "instructions": INSTRUCTIONS}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                try:
                    text, is_err, data = self.call_full(params.get("name"), params.get("arguments"), client_id)
                except LookupError as e:
                    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": str(e)}}
                result = {"content": [{"type": "text", "text": text}], "isError": is_err}
                if data is not None:
                    result["structuredContent"] = data
            elif method == "prompts/list":
                result = {"prompts": PROMPTS}
            elif method == "prompts/get":
                if params.get("name") != "capnet_rules":
                    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": "未知 prompt"}}
                result = {"description": PROMPTS[0]["description"],
                          "messages": [{"role": "user", "content": {"type": "text", "text": agent_rules()}}]}
            else:
                return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"未知方法 {method}"}}
        except Exception as e:  # noqa: BLE001 —— 服务不能因单次调用崩溃
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": str(e)}}
        return {"jsonrpc": "2.0", "id": mid, "result": result}


def main() -> None:
    server = Server()
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer
    for raw in stdin:
        line = raw.decode("utf-8").strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        else:
            resp = server.handle(msg)
        if resp is not None:
            stdout.write((json.dumps(resp, ensure_ascii=False) + "\n").encode("utf-8"))
            stdout.flush()


if __name__ == "__main__":
    main()
