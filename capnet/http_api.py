"""capnet HTTP 服务：一个端口同时提供远程 MCP、REST/OpenAPI 与轨迹汇总（hub）。

让不支持 stdio MCP、或不想本地安装的 agent 也能接入：
  POST /mcp                  MCP Streamable HTTP（JSON 响应）。Claude.ai 连接器、Dify、Coze、Cursor 等填 URL 即可
  GET  /v1/tools             工具清单（name/description/parameters）
  POST /v1/tools/{name}      直接调用工具，body 为参数 JSON → {"text", "is_error"}
  GET  /openapi.json         OpenAPI 3.1，可导入 GPTs Actions / Coze·Dify 插件 / 任何 function calling 框架
  GET  /v1/rules             agent 使用规范（Markdown），可放进不支持 MCP prompt 的系统提示
  POST /v1/traces            hub：接收已确认的轨迹（hub.submit 远程投递的目标）
  GET  /v1/health, /v1/stats

隐私：远程模式下执行记录会先发到本服务再清理（只在内存处理，原文不落盘、不记日志）；
要求"清理只在本机"的用户请用本地 stdio 模式。
贡献者区分：请求头 X-Capnet-Client（客户端自选的随机 id）> MCP 会话 id > 单次随机；都会被哈希。
鉴权：设了 CAPNET_API_KEY 则除 /v1/health 外都要求 Authorization: Bearer <key>。
"""
from __future__ import annotations

import json
import os
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import hub
from .mcp_server import TOOLS, VERSION, Server, agent_rules

MAX_BODY = 256 * 1024


def openapi_spec(base_url: str = "") -> dict:
    paths = {}
    for t in TOOLS:
        paths[f"/v1/tools/{t['name']}"] = {"post": {
            "operationId": t["name"],
            "summary": t["description"][:120],
            "description": t["description"],
            "requestBody": {"required": True, "content": {"application/json": {"schema": t["inputSchema"]}}},
            "responses": {"200": {"description": "工具输出（Markdown/JSON 文本）", "content": {"application/json": {
                "schema": {"type": "object", "properties": {"text": {"type": "string"},
                                                            "is_error": {"type": "boolean"}}}}}}},
        }}
    spec = {
        "openapi": "3.1.0",
        "info": {"title": "capnet", "version": VERSION,
                 "description": "能力与工作流路由网络。使用规范：\n\n" + agent_rules()},
        "paths": paths,
    }
    if base_url:
        spec["servers"] = [{"url": base_url}]
    if os.environ.get("CAPNET_API_KEY"):
        spec["components"] = {"securitySchemes": {"bearer": {"type": "http", "scheme": "bearer"}}}
        spec["security"] = [{"bearer": []}]
    return spec


def make_handler(server: Server, known: set[str]):
    api_key = os.environ.get("CAPNET_API_KEY")

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _send(self, code: int, body, ctype: str = "application/json; charset=utf-8",
                  headers: dict | None = None) -> None:
            data = body if isinstance(body, bytes) else (
                body.encode("utf-8") if isinstance(body, str) else json.dumps(body, ensure_ascii=False).encode("utf-8"))
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Access-Control-Allow-Origin", "*")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def _authorized(self) -> bool:
            if not api_key or self.path == "/v1/health":
                return True
            return self.headers.get("Authorization", "") == f"Bearer {api_key}"

        def _body(self, limit: int = MAX_BODY):
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0 or n > limit:
                # 先读掉（有上限的）请求体再回 413：不读就回，Windows 上客户端常收到连接重置而不是 413
                left = min(n, 8 * 1024 * 1024)
                while left > 0:
                    chunk = self.rfile.read(min(65536, left))
                    if not chunk:
                        break
                    left -= len(chunk)
                self.close_connection = True
                self._send(413,{"status": "error", "message": f"body 为空或超过 {limit // 1024}KB"})
                return None
            try:
                return json.loads(self.rfile.read(n).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                self._send(400, {"status": "error", "message": "非法 JSON"})
                return None

        def _client_id(self) -> str:
            return (self.headers.get("X-Capnet-Client") or self.headers.get("Mcp-Session-Id")
                    or "anon-" + uuid.uuid4().hex)

        def _base_url(self) -> str:
            return os.environ.get("CAPNET_PUBLIC_URL") or f"http://{self.headers.get('Host', 'localhost')}"

        def do_OPTIONS(self):  # noqa: N802 —— 浏览器类客户端的 CORS 预检
            self._send(204, b"", headers={
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, Authorization, Mcp-Session-Id, "
                                                "Mcp-Protocol-Version, X-Capnet-Client"})

        def do_GET(self):  # noqa: N802
            if not self._authorized():
                return self._send(401, {"error": "unauthorized"})
            path = self.path.split("?")[0]
            if path == "/v1/health":
                return self._send(200, {"ok": True, "version": VERSION})
            if path == "/v1/stats":
                return self._send(200, hub.stats())
            if path == "/v1/tools":
                return self._send(200, [{"name": t["name"], "description": t["description"],
                                         "parameters": t["inputSchema"]} for t in TOOLS])
            if path == "/openapi.json":
                return self._send(200, openapi_spec(self._base_url()))
            if path == "/v1/rules":
                return self._send(200, agent_rules(), "text/markdown; charset=utf-8")
            if path == "/mcp":  # 不提供服务端推送流
                return self._send(405, {"error": "use POST"}, headers={"Allow": "POST"})
            self._send(404, {"error": "not found"})

        def do_POST(self):  # noqa: N802
            if not self._authorized():
                return self._send(401, {"error": "unauthorized"})
            path = self.path.split("?")[0]
            if path == "/mcp":
                return self._mcp()
            if path.startswith("/v1/tools/"):
                body = self._body()
                if body is None:
                    return
                try:
                    text, is_err, data = server.call_full(path[len("/v1/tools/"):], body, self._client_id())
                except LookupError as e:
                    return self._send(404, {"text": str(e), "is_error": True})
                return self._send(200, {"text": text, "is_error": is_err, "data": data})
            if path == "/v1/traces":
                body = self._body(hub.MAX_BODY)
                if body is None:
                    return
                result = hub.ingest(body.get("trace"), known)
                return self._send(200 if result["status"] != "error" else 400, result)
            self._send(404, {"error": "not found"})

        def _mcp(self):
            msg = self._body()
            if msg is None:
                return
            session = self.headers.get("Mcp-Session-Id")
            headers = {}
            msgs = msg if isinstance(msg, list) else [msg]
            if any(isinstance(m, dict) and m.get("method") == "initialize" for m in msgs) and not session:
                session = uuid.uuid4().hex
                headers["Mcp-Session-Id"] = session
            cid = self.headers.get("X-Capnet-Client") or session
            out = [r for m in msgs if isinstance(m, dict) for r in [server.handle(m, cid)] if r is not None]
            if not out:  # 只有通知/响应
                return self._send(202, b"", headers=headers)
            self._send(200, out if isinstance(msg, list) else out[0], headers=headers)

        def do_DELETE(self):  # noqa: N802 —— 会话无服务端状态，结束会话无需处理
            self._send(405, {"error": "method not allowed"}, headers={"Allow": "GET, POST, OPTIONS"})

        def log_message(self, fmt, *args):  # 不记录请求体/来源 IP
            return

    return Handler


def serve(host: str = "127.0.0.1", port: int = 8765) -> None:
    server = Server()
    known = {c.id for c in server.reg.cards}
    httpd = ThreadingHTTPServer((host, port), make_handler(server, known))
    print(f"capnet 服务 http://{host}:{port}\n"
          f"  远程 MCP   POST /mcp\n  REST       POST /v1/tools/<name>   OpenAPI  GET /openapi.json\n"
          f"  使用规范   GET /v1/rules            汇总端   POST /v1/traces")
    httpd.serve_forever()
