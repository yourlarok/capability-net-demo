"""`capnet setup`：一条命令把 capnet 接进本机所有已安装的 agent 客户端。

设计目标是"一句话安装"：用户对自己的 agent 说「按 <INSTALL.md 链接> 安装 capnet」，agent 读说明、
跑两条命令即可，不需要用户克隆仓库或手改配置。本模块负责第二条命令：
  1. 探测本机装了哪些客户端（Claude Code / Codex / Gemini CLI / Cursor / Windsurf / Claude Desktop）
  2. 给每个客户端写入 MCP 配置（只改 capnet 这一项，原文件先备份为 .bak；重复执行会更新而不是重复添加）
  3. 安装使用规范：Claude Code 写 skill，Codex / Gemini 在全局说明文件里写一段带标记的区块
     （其余客户端靠 MCP 握手说明 + prompt「capnet_rules」+ 工具描述）
  4. 输出 Markdown 报告，agent 原样转告用户
`--remove` 反向撤销以上全部改动。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field

from . import clients
from .mcp_server import agent_rules

BEGIN, END = "<!-- capnet:begin -->", "<!-- capnet:end -->"
SKILL_FRONTMATTER = """---
name: capnet
description: 能力与工作流路由网络。遇到多步骤的专业任务（营销视频改进、品牌传播、账号增长、电商素材、直播复盘、课程/播客制作、游戏、短剧、解说等）时，先用 capnet 找成熟工作流与合适工具；任务完成后可汇总"客观解决思路"，经用户同意并清理私有信息后上传，帮助网络归纳新工作流。
---
"""
JSON_CLIENTS = ("cursor", "windsurf", "gemini-cli", "claude-desktop")


def _home(*p: str) -> str:
    return os.path.join(os.path.expanduser("~"), *p)


def skill_text() -> str:
    return SKILL_FRONTMATTER + "\n" + agent_rules() + "\n"


@dataclass
class Report:
    done: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def render(self, remove: bool, dry_run: bool) -> str:
        title = "capnet 卸载" if remove else "capnet 安装"
        out = [f"## {'🧹' if remove else '✅'} {title}{'（预演，未改动任何文件）' if dry_run else ''}", ""]
        if self.done:
            out.append("**将要处理**" if dry_run else "**已处理**")
            out += [f"- {x}" for x in self.done]
        if self.skipped:
            out.append("\n**未检测到 / 跳过**")
            out += [f"- {x}" for x in self.skipped]
        if self.errors:
            out.append("\n**⚠ 失败**")
            out += [f"- {x}" for x in self.errors]
        if not remove and not dry_run and self.done:
            out.append("\n**下一步**：重启上面这些客户端（或开一个新会话），然后直接提需求，例如"
                       "「帮我画一张中秋海报」「抖音视频完播率低，帮我出改进计划」。")
            out.append("想撤销：运行 `capnet setup --remove`。")
        return "\n".join(out)


# ---------- 探测 ----------

def detect() -> dict[str, bool]:
    return {
        "claude-code": bool(shutil.which("claude")),
        "codex": bool(shutil.which("codex")) or os.path.isdir(_home(".codex")),
        "gemini-cli": bool(shutil.which("gemini")) or os.path.isdir(_home(".gemini")),
        "cursor": os.path.isdir(_home(".cursor")),
        "windsurf": os.path.isdir(_home(".codeium", "windsurf")),
        "claude-desktop": os.path.isdir(os.path.dirname(clients.CLIENTS["claude-desktop"]["path"]())),
    }


# ---------- 文件工具 ----------

def _backup(path: str) -> None:
    if os.path.exists(path):
        shutil.copyfile(path, path + ".bak")


def _read(path: str) -> str:
    if not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8") as f:
        return f.read()


def _write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def upsert_block(text: str, body: str | None) -> str:
    """在 Markdown 文本里写入/替换/删除 capnet 区块（body=None 为删除）。"""
    pat = re.compile(re.escape(BEGIN) + r".*?" + re.escape(END) + r"\n?", re.S)
    text = pat.sub("", text).rstrip("\n")
    if body is None:
        return text + ("\n" if text else "")
    return (text + "\n\n" if text else "") + f"{BEGIN}\n{body}\n{END}\n"


def upsert_toml(text: str, section: str | None) -> str:
    """删除所有 [mcp_servers.capnet*] 段，再追加新段（section=None 为只删除）。"""
    out, skip = [], False
    for line in text.splitlines():
        if line.strip().startswith("["):
            skip = line.strip().startswith("[mcp_servers.capnet")
        if not skip:
            out.append(line)
    text = "\n".join(out).rstrip("\n")
    if section is None:
        return text + ("\n" if text else "")
    return (text + "\n\n" if text else "") + section


# ---------- 各客户端 ----------

def _claude_code(rep: Report, env: dict, remove: bool, dry: bool) -> None:
    exe = shutil.which("claude")
    skill = _home(".claude", "skills", "capnet", "SKILL.md")
    cmd = clients.launcher()
    if dry:
        rep.done.append(f"Claude Code：{'移除' if remove else '注册'} MCP（user 级）+ "
                        f"{'删除' if remove else '写入'} skill `{skill}`")
        return
    subprocess.run([exe, "mcp", "remove", "--scope", "user", "capnet"], capture_output=True)
    if not remove:
        envs = [x for k, v in env.items() for x in ("-e", f"{k}={v}")]
        r = subprocess.run([exe, "mcp", "add", "--scope", "user", *envs, "capnet", "--", *cmd],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            rep.errors.append(f"Claude Code：`claude mcp add` 失败：{(r.stderr or r.stdout).strip()[:200]}")
            return
        _write(skill, skill_text())
        rep.done.append(f"Claude Code：已注册 MCP（user 级，所有项目可用）+ skill `{skill}`")
    else:
        if os.path.exists(skill):
            os.remove(skill)
            os.rmdir(os.path.dirname(skill)) if not os.listdir(os.path.dirname(skill)) else None
        rep.done.append("Claude Code：已移除 MCP 与 skill")


def _json_client(name: str, rep: Report, env: dict, remove: bool, dry: bool) -> None:
    path = clients.CLIENTS[name]["path"]()
    key = clients.CLIENTS[name]["key"]
    if dry:
        rep.done.append(f"{name}：{'移除' if remove else '写入'} `{path}` 中的 capnet 项")
        return
    try:
        data = json.loads(_read(path) or "{}")
    except ValueError:
        rep.errors.append(f"{name}：`{path}` 不是合法 JSON，未改动（请手动检查）")
        return
    servers = data.setdefault(key, {})
    if remove:
        if servers.pop("capnet", None) is None:
            rep.skipped.append(f"{name}：没有 capnet 配置")
            return
    else:
        servers["capnet"] = clients.config(name, None, env or None)["entry"]
    _backup(path)
    _write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    rep.done.append(f"{name}：{'已移除' if remove else '已写入'} `{path}`（原文件备份为 .bak）")


def _rules_file(name: str, path: str, rep: Report, remove: bool, dry: bool) -> None:
    if dry:
        rep.done.append(f"{name}：{'删除' if remove else '写入'} `{path}` 中的 capnet 使用规范区块")
        return
    old = _read(path)
    if remove and BEGIN not in old:
        return
    _backup(path)
    _write(path, upsert_block(old, None if remove else agent_rules()))
    rep.done.append(f"{name}：{'已删除' if remove else '已写入'}使用规范区块 `{path}`")


def _codex(rep: Report, env: dict, remove: bool, dry: bool) -> None:
    path = _home(".codex", "config.toml")
    if dry:
        rep.done.append(f"codex：{'移除' if remove else '写入'} `{path}` 的 [mcp_servers.capnet]")
    else:
        _backup(path)
        _write(path, upsert_toml(_read(path), None if remove else clients._toml_entry(env or None)))
        rep.done.append(f"codex：{'已移除' if remove else '已写入'} `{path}` 的 [mcp_servers.capnet]（原文件备份为 .bak）")
    _rules_file("codex", _home(".codex", "AGENTS.md"), rep, remove, dry)


def run(only: list[str] | None = None, env: dict | None = None, remove: bool = False,
        dry_run: bool = False) -> Report:
    rep = Report()
    env = env or {}
    found = detect()
    for name, present in found.items():
        if only and name not in only:
            continue
        if not present and not only:
            rep.skipped.append(name)
            continue
        try:
            if name == "claude-code":
                if not shutil.which("claude"):
                    rep.skipped.append("claude-code（找不到 claude 命令）")
                    continue
                _claude_code(rep, env, remove, dry_run)
            elif name == "codex":
                _codex(rep, env, remove, dry_run)
            elif name in JSON_CLIENTS:
                _json_client(name, rep, env, remove, dry_run)
                if name == "gemini-cli":
                    _rules_file(name, _home(".gemini", "GEMINI.md"), rep, remove, dry_run)
        except OSError as e:
            rep.errors.append(f"{name}：{e}")
    if not remove:
        rep.done.append("启动命令：`" + " ".join(clients.launcher()) + "`")
    return rep


def main(args) -> None:
    env = dict(kv.split("=", 1) for kv in args.env)
    rep = run(args.client or None, env, remove=args.remove, dry_run=args.dry_run)
    sys.stdout.buffer.write((rep.render(args.remove, args.dry_run) + "\n").encode("utf-8"))
    if rep.errors:
        sys.exit(1)
