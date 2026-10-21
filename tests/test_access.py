"""接入渠道：每个工具都要能找到入口，API 工具必须写清鉴权与凭证申请处。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from capnet.cards import access_problems, load_apps  # noqa: E402
from capnet.mcp_server import Server  # noqa: E402
from capnet.registry import catalog_paths  # noqa: E402


class TestAccess(unittest.TestCase):
    def test_every_app_has_channel_and_auth(self):
        bad = {}
        for p in catalog_paths():
            for app in load_apps(p):
                probs = access_problems(app)
                if probs:
                    bad[app.app_id] = probs
        self.assertEqual(bad, {})

    def test_api_without_auth_is_rejected(self):
        from capnet.cards import parse_app
        app = parse_app({"app_id": "x", "name": "X", "access": {"web": "https://x.example"},
                         "capabilities": [{"domain": "image.gen", "intents": ["画图"], "outputs": ["png"],
                                           "accessibility": "api"}]})
        self.assertTrue(any("鉴权" in p for p in access_problems(app)))

    def test_card_renders_auth_channel(self):
        text, _ = Server().call("find_capability", {"query": "给动画角色配一个萌妹声音", "capability_type": ["speech"]})
        self.assertIn("接入渠道", text)
        self.assertIn("ELEVENLABS_API_KEY", text)
        self.assertIn("https://elevenlabs.io/app/settings/api-keys", text)

    def test_server_hides_demo_cards(self):
        text, _ = Server().call("find_capability", {"query": "给动画角色配一个萌妹声音", "capability_type": ["speech"]})
        self.assertNotIn("tts.mimo", text)
        self.assertNotIn("示例卡", text)

    def test_plan_has_access_checklist(self):
        text, _ = Server().call("plan_task", {"query": "我们的抖音视频完播率很低，帮我出一个完整的改进计划",
                                              "capability_type": ["marketing"]})
        self.assertIn("接入准备", text)


if __name__ == "__main__":
    unittest.main()
