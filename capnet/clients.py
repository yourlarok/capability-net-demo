"""接入适配：为各家 agent 客户端生成 MCP 配置，为 function calling 框架导出工具定义。

capnet 本身与客户端无关：核心是 Server（mcp_server.py），stdio MCP / HTTP MCP / REST 只是外壳。
这里只负责"把它告诉各个客户端"——配置文件格式各家不同。
"""
from __future__ import annotations

import json
import os
import shutil
import sys

from .mcp_server import TOOLS

DEMO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "demo.py")


def _home(*p: str) -> str:
    return os.path.join(os.path.expanduser("~"), *p)


def _appdata(*p: str) -> str:
    if sys.platform == "win32":
        return os.path.join(os.environ.get("APPDATA", _home("AppData", "Roaming")), *p)
    if sys.platform == "darwin":
        return _home("Library", "Application Support", *p)
    return _home(".config", *p)


# 客户端 -> (配置文件, 顶层键, stdio 条目, 远程条目 | None, 规范放哪)
def _stdio(env: dict | None) -> dict:
    e = {"command": sys.executable, "args": [DEMO, "mcp"]}
    if env:
        e["env"] = env
    return e


CLIENTS = {
    "claude-desktop": dict(path=lambda: _appdata("Claude", "claude_desktop_config.json"), key="mcpServers",
                           remote=None, rules="在 Claude Desktop 里通过 MCP prompt「capnet_rules」加载，或放进项目说明"),
    "cursor": dict(path=lambda: _home(".cursor", "mcp.json"), key="mcpServers",
                   remote=lambda u: {"url": u}, rules="复制到项目 .cursor/rules/capnet.mdc"),
    "windsurf": dict(path=lambda: _home(".codeium", "windsurf", "mcp_config.json"), key="mcpServers",
                     remote=lambda u: {"serverUrl": u}, rules="复制到项目 .windsurf/rules/capnet.md"),
    "gemini-cli": dict(path=lambda: _home(".gemini", "settings.json"), key="mcpServers",
                       remote=lambda u: {"httpUrl": u}, rules="追加到 ~/.gemini/GEMINI.md 或项目 GEMINI.md"),
    "vscode": dict(path=lambda: os.path.join(os.getcwd(), ".vscode", "mcp.json"), key="servers",
                   remote=lambda u: {"type": "http", "url": u}, rules="追加到项目 .github/copilot-instructions.md"),
    "cline": dict(path=None, key="mcpServers", remote=lambda u: {"url": u, "type": "streamableHttp"},
                  rules="追加到项目 .clinerules"),
    "codex": dict(path=lambda: _home(".codex", "config.toml"), key=None, remote=None,
                  rules="追加到 ~/.codex/AGENTS.md 或项目 AGENTS.md"),
    "claude-code": dict(path=None, key=None, remote=None, rules="复制 .claude/skills/capnet 到 ~/.claude/skills/"),
}


def _toml_entry(env: dict | None) -> str:
    lines = ["[mcp_servers.capnet]", f"command = {json.dumps(sys.executable)}",
             f"args = [{json.dumps(DEMO)}, \"mcp\"]"]
    if env:
        lines.append("env = { " + ", ".join(f"{k} = {json.dumps(v)}" for k, v in env.items()) + " }")
    return "\n".join(lines) + "\n"


def config(client: str, url: str | None = None, env: dict | None = None) -> dict:
    """返回 {path, snippet, entry, rules, note}；不写文件。"""
    c = CLIENTS[client]
    path = c["path"]() if c["path"] else None
    if client == "claude-code":
        envs = "".join(f" -e {k}={v}" for k, v in (env or {}).items())
        cmd = (f"claude mcp add --transport http capnet {url}" if url
               else f"claude mcp add capnet --scope user{envs} -- \"{sys.executable}\" \"{DEMO}\" mcp")
        return {"path": None, "snippet": cmd, "entry": None, "rules": c["rules"], "note": "在终端执行上面的命令"}
    if client == "codex":
        if url:
            return {"path": path, "snippet": "", "entry": None, "rules": c["rules"],
                    "note": "codex 请用本地 stdio 模式（去掉 --url）"}
        return {"path": path, "snippet": _toml_entry(env), "entry": None, "rules": c["rules"], "note": ""}
    if url and c["remote"] is None:
        return {"path": path, "snippet": "", "entry": None, "rules": c["rules"],
                "note": f"{client} 的配置文件不支持远程 URL，请在其界面里添加连接器，或去掉 --url 用本地模式"}
    entry = c["remote"](url) if url else _stdio(env)
    snippet = json.dumps({c["key"]: {"capnet": entry}}, ensure_ascii=False, indent=2)
    note = "" if path else "把 snippet 合并进该客户端的 MCP 设置（在其 MCP 面板里打开配置文件）"
    return {"path": path, "snippet": snippet, "entry": entry, "rules": c["rules"], "note": note}


def write_config(client: str, url: str | None = None, env: dict | None = None) -> str:
    """把 capnet 合并进客户端配置文件（先备份为 .bak），只改 capnet 这一项。"""
    cfg = config(client, url, env)
    path = cfg["path"]
    if not path or not cfg["snippet"]:
        raise ValueError(cfg["note"] or f"{client} 不支持自动写入")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        shutil.copyfile(path, path + ".bak")
    if client == "codex":
        text = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
        if "[mcp_servers.capnet]" in text:
            raise ValueError(f"{path} 已有 [mcp_servers.capnet]，请手动修改")
        with open(path, "a", encoding="utf-8") as f:
            f.write(("\n" if text and not text.endswith("\n") else "") + "\n" + cfg["snippet"])
        return path
    data = {}
    if os.path.exists(path) and os.path.getsize(path):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)  # 解析失败就抛出，绝不覆盖用户配置
    key = CLIENTS[client]["key"]
    data.setdefault(key, {})["capnet"] = cfg["entry"]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def _strip(obj, key: str):
    if isinstance(obj, dict):
        return {k: _strip(v, key) for k, v in obj.items() if k != key}
    if isinstance(obj, list):
        return [_strip(v, key) for v in obj]
    return obj


def export_tools(fmt: str = "openai") -> list[dict]:
    """function calling 工具定义：openai / anthropic / gemini / mcp。"""
    if fmt == "openai":
        return [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                  "parameters": t["inputSchema"]}} for t in TOOLS]
    if fmt == "anthropic":
        return [{"name": t["name"], "description": t["description"], "input_schema": t["inputSchema"]}
                for t in TOOLS]
    if fmt == "gemini":  # Gemini 的 schema 子集不接受 default
        return [{"function_declarations": [{"name": t["name"], "description": t["description"],
                                            "parameters": _strip(t["inputSchema"], "default")} for t in TOOLS]}]
    if fmt == "mcp":
        return TOOLS
    raise ValueError(f"未知格式 {fmt}")
