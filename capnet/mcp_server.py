"""capnet MCP server（stdio，零依赖）。

任何支持 MCP 的 agent 接入后即获得完整路由——与是否上传轨迹无关。
工具：
  plan_task        复杂任务 -> 成熟工作流 + 逐阶段方法 + 工具推荐
  find_capability  单个能力缺口 -> 工具候选
  list_workflows / get_workflow  浏览工作流库
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

from . import hub
from .gaps import log_gap
from .paths import DATA
from .cardview import render_capabilities
from .intent import FAMILIES, describe
from .planner import KEEP_RATIO, infer_market, market_fit, plan, render_plan
from .privacy import load_private_terms
from .registry import Registry
from .scoring import ScoreContext
from .search import as_options, search
from .traces import summarize

GAPS = os.path.join(DATA, "gaps.jsonl")
VERSION = "0.4.0"
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
    "capnet 是能力与工作流路由网络。遇到多步骤的专业任务（营销、电商、内容生产、课程、游戏等），"
    "先调 plan_task 获取成熟工作流与工具推荐；标注'人工使用'的工具只能推荐给用户操作，不得假装能调用。"
    "任务完成后可调 summarize_trace 生成只含客观思路的清理预览，必须原样展示给用户并征得同意，"
    "同意才调 submit_trace(consent=true)；用户拒绝则 submit_trace(consent=false)。上传与否不影响路由。"
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
    def __init__(self):
        self._reg: Registry | None = None
        self._lock = threading.Lock()  # HTTP 模式多线程共享同一个 Server

    @property
    def reg(self) -> Registry:
        with self._lock:
            if self._reg is None:
                self._reg = Registry.load()
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
            out = self.summarize_trace(args, client_id) if name == "summarize_trace" else getattr(self, name)(args)
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
                              "manual": o["manual"]} for o in opts]
        structured = {
            "matched": p.matched,
            "workflow_id": p.workflow.workflow_id if p.workflow else None,
            "relevance": round(p.relevance, 3),
            "slots": [{"stage": sp.stage.name, "slot_id": f.slot.slot_id, "domain": f.slot.domain,
                       "options": slim(f.options)} for sp in p.stages for f in sp.fills],
            "fallback": slim(p.fallback),
        }
        return render_plan(p) + DISPLAY_HINT, False, structured

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
        rows = [{"workflow_id": w.workflow_id, "name": w.name, "industry": w.industry,
                 "source": w.source.get("kind"), "stages": len(w.stages)}
                for w in self.reg.workflows if not ind or w.industry.startswith(ind)]
        return json.dumps(rows, ensure_ascii=False, indent=1), False

    def get_workflow(self, a: dict) -> tuple[str, bool]:
        wf = self.reg.get_workflow(a["workflow_id"])
        if wf is None:
            return f"没有工作流 {a['workflow_id']}", True
        return json.dumps(asdict(wf), ensure_ascii=False, indent=1), False

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
