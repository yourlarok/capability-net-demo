import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from capnet import clients
from capnet.http_api import make_handler, openapi_spec
from capnet.mcp_server import TOOLS, Server, agent_rules

BASE = os.path.join(os.path.dirname(__file__), "..")
ENV_KEYS = ("CAPNET_HOME", "CAPNET_HUB_DIR", "CAPNET_DRAFTS_DIR", "CAPNET_HUB_URL", "CAPNET_API_KEY",
            "HOME", "USERPROFILE")


class EnvCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.saved = {k: os.environ.get(k) for k in ENV_KEYS}
        os.environ["CAPNET_HOME"] = os.path.join(self.tmp, "home")
        os.environ["CAPNET_HUB_DIR"] = os.path.join(self.tmp, "hub")
        os.environ["CAPNET_DRAFTS_DIR"] = os.path.join(self.tmp, "drafts")
        os.environ.pop("CAPNET_HUB_URL", None)
        os.environ.pop("CAPNET_API_KEY", None)

    def tearDown(self):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestRules(unittest.TestCase):
    def test_single_source(self):
        skill = open(os.path.join(BASE, ".claude", "skills", "capnet", "SKILL.md"), encoding="utf-8").read()
        body = skill.split("---", 2)[2].strip()
        self.assertEqual(body, agent_rules())  # skill 与通用规范必须一致

    def test_mcp_prompt(self):
        s = Server()
        names = [p["name"] for p in s.handle({"jsonrpc": "2.0", "id": 1, "method": "prompts/list"})["result"]["prompts"]]
        self.assertEqual(names, ["capnet_rules"])
        msg = s.handle({"jsonrpc": "2.0", "id": 2, "method": "prompts/get", "params": {"name": "capnet_rules"}})
        self.assertIn("同意", msg["result"]["messages"][0]["content"]["text"])


class TestExport(unittest.TestCase):
    def test_formats(self):
        oa = clients.export_tools("openai")
        self.assertEqual({t["function"]["name"] for t in oa}, {t["name"] for t in TOOLS})
        self.assertIn("input_schema", clients.export_tools("anthropic")[0])
        self.assertNotIn('"default"', json.dumps(clients.export_tools("gemini")))

    def test_openapi(self):
        spec = openapi_spec("http://x")
        self.assertEqual(len(spec["paths"]), len(TOOLS))
        self.assertEqual(spec["paths"]["/v1/tools/plan_task"]["post"]["operationId"], "plan_task")


class TestConfigWrite(EnvCase):
    def setUp(self):
        super().setUp()
        os.environ["HOME"] = os.environ["USERPROFILE"] = self.tmp

    def test_merge_keeps_other_servers(self):
        path = os.path.join(self.tmp, ".cursor", "mcp.json")
        os.makedirs(os.path.dirname(path))
        json.dump({"mcpServers": {"other": {"command": "x"}}, "keep": 1}, open(path, "w"))
        clients.write_config("cursor")
        data = json.load(open(path))
        self.assertEqual(data["mcpServers"]["other"], {"command": "x"})
        self.assertEqual(data["keep"], 1)
        self.assertEqual(data["mcpServers"]["capnet"]["args"][-1], "mcp")
        self.assertTrue(os.path.exists(path + ".bak"))

    def test_broken_json_not_overwritten(self):
        path = os.path.join(self.tmp, ".cursor", "mcp.json")
        os.makedirs(os.path.dirname(path))
        with open(path, "w") as f:
            f.write("{ not json")
        with self.assertRaises(ValueError):
            clients.write_config("cursor")
        self.assertEqual(open(path).read(), "{ not json")

    def test_codex_toml_once(self):
        path = clients.write_config("codex")
        self.assertIn("[mcp_servers.capnet]", open(path, encoding="utf-8").read())
        with self.assertRaises(ValueError):
            clients.write_config("codex")

    def test_remote_urls(self):
        self.assertEqual(clients.config("gemini-cli", "http://h/mcp")["entry"], {"httpUrl": "http://h/mcp"})
        self.assertIn("--transport http", clients.config("claude-code", "http://h/mcp")["snippet"])


class TestHttp(EnvCase):
    def _start(self):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(Server(), None))
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        super().tearDown()

    def req(self, path, body=None, headers=None):
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        r = urllib.request.Request(self.base + path, data=data,
                                   headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(r, timeout=20) as resp:
                raw = resp.read()
                is_json = raw and "json" in resp.headers.get("Content-Type", "")
                return resp.status, dict(resp.headers), (json.loads(raw) if is_json else raw)
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def test_rest_and_openapi(self):
        self._start()
        self.assertEqual(self.req("/v1/health")[0], 200)
        self.assertEqual(len(self.req("/v1/tools")[2]), len(TOOLS))
        code, _, out = self.req("/v1/tools/plan_task", {"query": "抖音视频完播率低，怎么改"})
        self.assertEqual(code, 200)
        self.assertIn("marketing-video-improve", out["text"])
        self.assertEqual(self.req("/v1/tools/nope", {"a": 1})[0], 404)
        self.assertIn("/v1/tools/submit_trace", self.req("/openapi.json")[2]["paths"])
        self.assertIn("同意".encode("utf-8"), self.req("/v1/rules")[2])

    def test_remote_mcp_session_and_upload(self):
        self._start()

        def rpc(i, method, params=None, sid=None):
            h = {"Mcp-Session-Id": sid} if sid else {}
            return self.req("/mcp", {"jsonrpc": "2.0", "id": i, "method": method, "params": params or {}}, h)

        for who in ("a", "b"):
            _, hdr, _ = rpc(1, "initialize", {"protocolVersion": "2025-06-18"})
            sid = hdr.get("Mcp-Session-Id")
            self.assertTrue(sid)
            self.assertEqual(self.req("/mcp", {"jsonrpc": "2.0", "method": "notifications/initialized"},
                                      {"Mcp-Session-Id": sid})[0], 202)
            draft = {"problem": f"ACME 视频播放量低 {who}", "accepted": True, "private_terms": ["ACME"],
                     "steps": [{"stage": "诊断", "domain": "data.analytics", "ok": True,
                                "approach": "联系 13812345678 看留存曲线"}]}
            out = rpc(2, "tools/call", {"name": "summarize_trace", "arguments": draft}, sid)[2]
            text = out["result"]["content"][0]["text"]
            self.assertNotIn("ACME", text)
            self.assertNotIn("13812345678", text)
            pid = text.split("preview_id='")[1].split("'")[0]
            res = rpc(3, "tools/call", {"name": "submit_trace", "arguments": {"preview_id": pid, "consent": True}}, sid)
            self.assertIn("accepted", res[2]["result"]["content"][0]["text"])
        with open(os.path.join(os.environ["CAPNET_HUB_DIR"], "traces.jsonl"), encoding="utf-8") as f:
            rows = [json.loads(x) for x in f]
        self.assertEqual(len({r["contributor"] for r in rows}), 2)  # 不同会话 = 不同贡献者

    def test_api_key(self):
        os.environ["CAPNET_API_KEY"] = "s3cret"
        self._start()
        self.assertEqual(self.req("/v1/health")[0], 200)
        self.assertEqual(self.req("/v1/tools")[0], 401)
        self.assertEqual(self.req("/v1/tools", headers={"Authorization": "Bearer s3cret"})[0], 200)

    def test_trace_body_limit(self):
        self._start()
        self.assertEqual(self.req("/v1/traces", {"trace": {"x": "y" * 70000}})[0], 413)


if __name__ == "__main__":
    unittest.main()
