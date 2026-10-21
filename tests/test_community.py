"""能力社区：隐式反馈 → 工具 / flow 评分；没有现成 flow 的新做法 → 社区 flow；上报方式事先选定。"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from capnet import community  # noqa: E402
from capnet.mcp_server import Server  # noqa: E402

PODCAST = {  # 一条现有 flow 库里没有的路：古籍 → 有声书
    "problem": "古籍整理成有声书",
    "industry": "media",
    "outcome": "accepted",
    "corrections": 0,
    "steps": [
        {"stage": "断句与白话注释", "domain": "text.copywriting", "entry_id": "biz.claude-api", "ok": True,
         "approach": "先按章节断句，生僻字加拼音注释，再出白话导读"},
        {"stage": "分角色配音", "domain": "audio.tts", "ok": True, "approach": "旁白与人物对白用不同音色，语速放慢一档"},
        {"stage": "配乐与降噪", "domain": "audio.edit", "ok": True, "approach": "古琴类配乐压低到人声下 18dB"},
    ],
}
QUERY = "帮我把古籍整理成有声书"


class CommunityCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.env = {k: os.environ.get(k) for k in ("CAPNET_HOME", "CAPNET_HUB_DIR", "CAPNET_HUB_URL")}
        os.environ["CAPNET_HOME"] = os.path.join(self.tmp, "home")
        os.environ["CAPNET_HUB_DIR"] = os.path.join(self.tmp, "hub")
        os.environ.pop("CAPNET_HUB_URL", None)

    def tearDown(self):
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def finish(self, srv, item, who):
        text, err = srv.call("finish_task", dict(item, steps=[dict(s) for s in item["steps"]]), client_id=who)
        self.assertFalse(err, text)
        return text


class TestCommunityFlows(CommunityCase):
    def test_new_path_becomes_community_flow_then_verified(self):
        srv = Server()
        text, _ = srv.call("plan_task", {"query": QUERY, "task_type": "produce"})
        self.assertIn("没有现成工作流", text)

        self.finish(srv, PODCAST, "user-a")
        text, _ = srv.call("plan_task", {"query": QUERY, "task_type": "produce"})
        self.assertIn("🌱 社区流程", text)
        self.assertIn("古籍整理成有声书", text)
        self.assertIn("先按章节断句", text)  # 阶段方法来自贡献者的思路

        self.finish(srv, PODCAST, "user-b")
        self.finish(srv, PODCAST, "user-c")
        text, _ = srv.call("plan_task", {"query": QUERY, "task_type": "produce"})
        self.assertIn("✅ 社区流程 · 已验证", text)

    def test_failing_path_is_not_listed(self):
        srv = Server()
        self.finish(srv, dict(PODCAST, outcome="abandoned"), "user-a")
        text, _ = srv.call("plan_task", {"query": QUERY, "task_type": "produce"})
        self.assertIn("没有现成工作流", text)

    def test_foundation_is_rated_not_replaced(self):
        srv = Server()
        q = "我们的抖音视频完播率很低，帮我出一个完整的改进计划"
        _, _, data = srv.call_full("plan_task", {"query": q, "capability_type": ["marketing"]})
        wid = data["workflow_id"]
        self.assertEqual(data["flow"]["tier"], "foundation")
        before = data["flow"]["trust"]
        wf = srv.reg.get_workflow(wid)
        steps = [{"stage": st.name, "domain": sl.domain, "ok": True} for st in wf.stages for sl in st.slots]
        skip = wf.stages[-1].name
        for who in ("a", "b", "c"):
            self.finish(srv, {"problem": "营销视频完播率低", "workflow_id": wid, "outcome": "partial",
                              "corrections": 3, "skipped": [skip], "steps": steps}, who)
        text, _, data = srv.call_full("plan_task", {"query": q, "capability_type": ["marketing"]})
        self.assertEqual(data["workflow_id"], wid)  # 基石不被替换
        self.assertEqual(data["flow"]["tier"], "foundation")
        self.assertLess(data["flow"]["trust"], before)
        self.assertIn("3 次使用", text)
        self.assertIn(f"100% 的使用者跳过了这一步", text)
        self.assertFalse(os.listdir(community.community_dir()))  # 同构轨迹只强化，不另建

    def test_better_rated_flow_wins_tie(self):
        from capnet.workflows import Stage, Workflow
        a = Workflow("a", "A", "", {"kind": "conversation-induction"}, [Stage("s1", "x")], community={"support": 1})
        good, bad = community.FlowStats(), community.FlowStats()
        for i in range(6):
            good.add({"accepted": True, "contributor": f"g{i}"})
            bad.add({"accepted": False, "corrections": 3, "contributor": f"b{i}"})
        self.assertGreater(community.trust(a, good), community.trust(a, None))
        self.assertLess(community.trust(a, bad), community.trust(a, None))
        self.assertEqual(community.tier(a, good), "verified")


class TestShareModes(CommunityCase):
    def test_unset_mode_asks_once_and_stays_local(self):
        srv = Server()
        text, _ = srv.call("plan_task", {"query": "我们的抖音视频完播率很低，帮我出一个完整的改进计划"})
        self.assertIn("set_share_mode", text)
        os.environ["CAPNET_HUB_URL"] = "http://127.0.0.1:9"  # 不可达：一旦尝试上报就会报错
        text = self.finish(srv, PODCAST, "u")
        self.assertIn("只在本机生效", text)

    def test_off_never_contacts_hub(self):
        community.set_share("off")
        os.environ["CAPNET_HUB_URL"] = "http://127.0.0.1:9"
        text = self.finish(Server(), PODCAST, "u")
        self.assertIn("不上报", text)

    def test_ask_returns_preview_for_consent(self):
        community.set_share("ask")
        os.environ["CAPNET_HUB_URL"] = "http://127.0.0.1:9"
        text = self.finish(Server(), PODCAST, "u")
        self.assertIn("预览指纹", text)
        self.assertIn("submit_trace", text)
        self.assertIn("隐式反馈", text)

    def test_set_share_mode_tool(self):
        srv = Server()
        srv.call("set_share_mode", {"mode": "auto"})
        self.assertEqual(community.get_share(), "auto")
        text, _ = srv.call("plan_task", {"query": "我们的抖音视频完播率很低，帮我出一个完整的改进计划"})
        self.assertNotIn("set_share_mode", text)

    def test_remote_server_needs_per_user_choice(self):
        srv = Server(remote=True)
        community.set_share("auto")  # 服务端配置不代表任何用户的选择
        text = self.finish(srv, PODCAST, "u")
        self.assertIn("未上报", text)
        self.assertFalse(os.path.exists(os.path.join(os.environ["CAPNET_HUB_DIR"], "traces.jsonl")))
        self.finish(srv, dict(PODCAST, share_mode="auto"), "u")
        self.assertTrue(os.path.exists(os.path.join(os.environ["CAPNET_HUB_DIR"], "traces.jsonl")))

    def test_private_terms_scrubbed_before_anything_is_stored(self):
        srv = Server()
        item = dict(PODCAST, problem="给喵喵书局把古籍整理成有声书", private_terms=["喵喵书局"])
        self.finish(srv, item, "u")
        with open(os.path.join(os.environ["CAPNET_HUB_DIR"], "traces.jsonl"), encoding="utf-8") as f:
            self.assertNotIn("喵喵书局", f.read())


if __name__ == "__main__":
    unittest.main()
