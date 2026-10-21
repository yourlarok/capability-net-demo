import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from capnet import hub
from capnet.privacy import scrub_text
from capnet.traces import fingerprint, summarize

BASE = os.path.join(os.path.dirname(__file__), "..")
KNOWN = {"biz.douyin-creator", "pro.capcut", "biz.claude-api"}

DRAFT = {
    "problem": "张总公司《小熊保温杯》的宣传视频播放量低",
    "industry": "marketing/video", "workflow_id": "marketing-video-improve", "accepted": True,
    "task": "给 ACME 做视频改进，联系人 13812345678 zhang@acme.com",
    "steps": [
        {"stage": "目标与基线", "domain": "data.analytics", "entry_id": "biz.douyin-creator", "ok": True,
         "approach": r"导出 D:\work\acme\data.xlsx 对比均值，发现 3 秒流失高"},
        {"stage": "改版", "domain": "video.edit", "entry_id": "acme.internal-editor", "ok": True,
         "retries": 2, "cost": 5000, "approach": "首帧前置结果画面，预算 ¥5000"},
        {"stage": "x", "domain": "Bad Domain!", "ok": False},
    ],
    "lessons": ["卖点要在 5 秒内出现，见 https://acme.com/wiki?id=1"],
}


class TestScrub(unittest.TestCase):
    def test_rules(self):
        cases = {
            "联系 13812345678": "[电话]", "发到 a.b@x.com": "[邮箱]", "key sk-abcdefghijklmnop123": "[密钥]",
            r"C:\Users\me\a.txt": "[路径]", "~/proj/x": "[路径]", "https://x.com/a": "[链接]",
            "预算 50万元": "[金额]", "《某剧》": "《作品》", "李经理说": "[某人]", "110101199001011234": "[证件号]",
            "@某博主": "[账号]", "192.168.1.10": "[IP]",
        }
        for raw, token in cases.items():
            self.assertIn(token, scrub_text(raw), raw)

    def test_methodology_survives(self):
        s = "用留存曲线定位 3 秒跌点，CTR 0.8% 时先改封面，压到原时长 60–70%"
        self.assertEqual(scrub_text(s), s)
        self.assertEqual(scrub_text("把数据汇总后总结"), "把数据汇总后总结")

    def test_private_terms(self):
        self.assertEqual(scrub_text("ACME 的新品", ["ACME"]), "[私有] 的新品")


class TestSummarize(unittest.TestCase):
    def setUp(self):
        self.s = summarize(DRAFT, KNOWN, ["ACME"], contributor_raw="install-1")
        self.blob = json.dumps(self.s.item, ensure_ascii=False)

    def test_nothing_private_leaks(self):
        for bad in ("13812345678", "zhang@acme.com", "acme.internal-editor", "小熊保温杯", "张总",
                    "5000", "acme.com", r"D:\work", "ACME", "install-1"):
            self.assertNotIn(bad, self.blob, bad)

    def test_objective_structure_kept(self):
        st = self.s.item["steps"]
        self.assertEqual(st[0]["entry_id"], "biz.douyin-creator")
        self.assertIn("3 秒流失高", st[0]["approach"])
        self.assertEqual(st[1]["retries"], 2)
        self.assertEqual(st[1]["cost"], 0.0)
        self.assertEqual(st[2]["domain"], "misc")
        self.assertEqual(self.s.item["task"], "")
        self.assertEqual(self.s.item["t"] % 86400, 0)

    def test_fingerprint_matches_item(self):
        self.assertEqual(self.s.preview_id, fingerprint(self.s.item))

    def test_render_shows_what_was_removed(self):
        md = self.s.render()
        self.assertIn("任务原文", md)
        self.assertIn(self.s.preview_id, md)


class TestHubFlow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.env = {k: os.environ.get(k) for k in ("CAPNET_HOME", "CAPNET_HUB_DIR", "CAPNET_DRAFTS_DIR", "CAPNET_HUB_URL")}
        os.environ["CAPNET_HOME"] = os.path.join(self.tmp, "home")
        os.environ["CAPNET_HUB_DIR"] = os.path.join(self.tmp, "hub")
        os.environ["CAPNET_DRAFTS_DIR"] = os.path.join(self.tmp, "drafts")
        os.environ.pop("CAPNET_HUB_URL", None)

    def tearDown(self):
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _pending(self, who="a"):
        s = summarize(DRAFT, KNOWN, contributor_raw=who)
        hub.save_pending(s)
        return s.preview_id

    def test_no_consent_no_upload(self):
        pid = self._pending()
        self.assertEqual(hub.submit(pid, consent=False, known_entries=KNOWN)["status"], "declined")
        self.assertFalse(os.path.exists(os.path.join(os.environ["CAPNET_HUB_DIR"], "traces.jsonl")))
        self.assertEqual(hub.submit(pid, consent=True, known_entries=KNOWN)["status"], "error")  # 预览已删

    def test_consent_must_be_exactly_true(self):
        pid = self._pending()
        self.assertEqual(hub.submit(pid, consent="yes", known_entries=KNOWN)["status"], "declined")

    def test_tampered_preview_rejected(self):
        pid = self._pending()
        path = os.path.join(os.environ["CAPNET_HOME"], "pending", f"{pid}.json")
        item = json.load(open(path, encoding="utf-8"))
        item["problem"] = "被偷偷改过"
        json.dump(item, open(path, "w", encoding="utf-8"), ensure_ascii=False)
        self.assertEqual(hub.submit(pid, consent=True, known_entries=KNOWN)["status"], "error")

    def test_accept_dedupe_and_events(self):
        pid = self._pending()
        self.assertEqual(hub.submit(pid, consent=True, known_entries=KNOWN)["status"], "accepted")
        s = summarize(DRAFT, KNOWN, contributor_raw="a")
        self.assertEqual(hub.ingest(s.item, KNOWN)["status"], "duplicate")
        ev = open(os.path.join(os.environ["CAPNET_HUB_DIR"], "events.jsonl"), encoding="utf-8").read()
        self.assertIn("biz.douyin-creator", ev)

    def test_server_side_rescrub(self):
        s = summarize(DRAFT, KNOWN, contributor_raw="b")
        dirty = dict(s.item, problem="联系 13812345678")  # 绕过客户端直接投递
        self.assertEqual(hub.ingest(dirty, KNOWN)["status"], "accepted")
        stored = open(os.path.join(os.environ["CAPNET_HUB_DIR"], "traces.jsonl"), encoding="utf-8").read()
        self.assertNotIn("13812345678", stored)

    def test_bad_contributor_rejected(self):
        s = summarize(DRAFT, KNOWN, contributor_raw="c")
        self.assertEqual(hub.ingest(dict(s.item, contributor="real-name"), KNOWN)["status"], "error")

    def test_refresh_induces_from_three_contributors(self):
        for who in ("u1", "u2", "u3"):
            draft = dict(DRAFT, steps=DRAFT["steps"][:2])
            hub.ingest(summarize(draft, KNOWN, contributor_raw=who).item, KNOWN)
        rows = hub.refresh()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["support"], 3)
        d = json.load(open(os.path.join(os.environ["CAPNET_DRAFTS_DIR"], rows[0]["workflow_id"] + ".json"),
                           encoding="utf-8"))
        self.assertTrue(d["stages"][0]["method"])  # 阶段方法来自贡献者的 approach


    def test_uploaded_traces_feed_routing(self):
        from capnet.planner import fill_slot
        from capnet.registry import Registry
        from capnet.scoring import ScoreContext
        from capnet.workflows import Slot

        def top_cover():
            reg = Registry.load()
            ctx = ScoreContext(total_selections=reg.total_selections)
            return fill_slot(reg, Slot(slot_id="cover", domain="image.design"), ctx, market="cn").options[0]["value"]

        before = top_cover()
        challenger = "biz.gaoding" if before != "biz.gaoding" else "biz.canva"
        known = {challenger}
        for i in range(40):  # 40 位独立贡献者都用它成功完成并被接受
            d = {"problem": "封面点击率低", "accepted": True,
                 "steps": [{"stage": "封面", "domain": "image.design", "entry_id": challenger, "ok": True}]}
            hub.ingest(summarize(d, known, contributor_raw=f"u{i}").item, known)
        self.assertEqual(top_cover(), challenger)


class TestMcpServer(unittest.TestCase):
    def test_protocol_roundtrip(self):
        tmp = tempfile.mkdtemp()
        env = dict(os.environ, CAPNET_HOME=os.path.join(tmp, "h"), CAPNET_HUB_DIR=os.path.join(tmp, "hub"))
        p = subprocess.Popen([sys.executable, os.path.join(BASE, "demo.py"), "mcp"], env=env,
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE)

        def call(i, method, params=None):
            p.stdin.write((json.dumps({"jsonrpc": "2.0", "id": i, "method": method,
                                       "params": params or {}}, ensure_ascii=False) + "\n").encode("utf-8"))
            p.stdin.flush()
            return json.loads(p.stdout.readline().decode("utf-8"))

        try:
            self.assertEqual(call(1, "initialize", {"protocolVersion": "2025-06-18"})["result"]["serverInfo"]["name"], "capnet")
            p.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
            names = {t["name"] for t in call(2, "tools/list")["result"]["tools"]}
            self.assertEqual(names, {"plan_task", "find_capability", "list_workflows", "get_workflow",
                                     "summarize_trace", "submit_trace"})
            r = call(3, "tools/call", {"name": "plan_task", "arguments": {"query": "抖音视频完播率低，怎么改"}})
            self.assertIn("marketing-video-improve", r["result"]["content"][0]["text"])
            r = call(4, "tools/call", {"name": "plan_task", "arguments": {"query": "播客耳机推荐", "task_type": "qa"}})
            self.assertIn("不需要工作流", r["result"]["content"][0]["text"])
            r = call(5, "tools/call", {"name": "summarize_trace", "arguments": DRAFT})
            text = r["result"]["content"][0]["text"]
            self.assertNotIn("13812345678", text)
            pid = text.split("preview_id='")[1].split("'")[0]
            r = call(6, "tools/call", {"name": "submit_trace", "arguments": {"preview_id": pid, "consent": False}})
            self.assertIn("declined", r["result"]["content"][0]["text"])
            self.assertEqual(call(7, "nope")["error"]["code"], -32601)
        finally:
            p.stdin.close()
            p.wait(timeout=10)
            p.stdout.close()
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
