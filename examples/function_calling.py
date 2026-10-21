"""把 capnet 接进任意 function calling 循环（OpenAI / Anthropic / Gemini / LangChain / 自研 agent）。

两种调用方式，二选一：
  本地库：Server().call(name, args)            —— 同进程，零网络
  远程：  POST {CAPNET_URL}/v1/tools/{name}    —— 先在某台机器上 `python demo.py serve`

运行示例：python examples/function_calling.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from capnet.clients import export_tools  # noqa: E402
from capnet.mcp_server import Server, agent_rules  # noqa: E402

CAPNET_URL = os.environ.get("CAPNET_URL")  # 如 http://127.0.0.1:8765；不设则用本地库
_local = Server()


def run_tool(name: str, args: dict) -> str:
    """模型返回 tool call 后，把 name/args 交给这里，返回值作为 tool result 回给模型。"""
    if CAPNET_URL:
        req = urllib.request.Request(f"{CAPNET_URL}/v1/tools/{name}",
                                     data=json.dumps(args, ensure_ascii=False).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)["text"]
    return _local.call(name, args)[0]


# 接入任意模型时需要的三样东西：
SYSTEM_PROMPT = agent_rules()            # 1. 使用规范放进系统提示
TOOLS_OPENAI = export_tools("openai")    # 2. 工具定义（也有 "anthropic" / "gemini"）
# 3. 在你的循环里：if 模型要调用工具: result = run_tool(call.name, call.arguments)

# OpenAI 风格的循环大致如下（需要 pip install openai，这里只示意）：
#   msgs = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": question}]
#   while True:
#       r = client.chat.completions.create(model=..., messages=msgs, tools=TOOLS_OPENAI)
#       m = r.choices[0].message; msgs.append(m)
#       if not m.tool_calls: break
#       for c in m.tool_calls:
#           msgs.append({"role": "tool", "tool_call_id": c.id,
#                        "content": run_tool(c.function.name, json.loads(c.function.arguments))})

if __name__ == "__main__":
    print(f"{len(TOOLS_OPENAI)} 个工具：", [t["function"]["name"] for t in TOOLS_OPENAI])
    print(run_tool("plan_task", {"query": "新茶饮品牌秋季上新，做一波全网传播", "market": "cn"})[:600])
