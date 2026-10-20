import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from capnet.cards import ACCESSIBILITY, load_apps, load_cards
from capnet.scoring import ScoreContext
from capnet.search import as_options, search

PRO = os.path.join(os.path.dirname(__file__), "..", "data", "pro_tools.json")


class TestProToolsCatalog(unittest.TestCase):
    """策展专业工具目录：补齐 MCP/HF 采集层天然不可见的 gui/web 能力。"""

    @classmethod
    def setUpClass(cls):
        cls.apps = load_apps(PRO)
        cls.cards = load_cards(PRO)
        cls.ctx = ScoreContext(total_selections=1.0)

    def test_schema_and_scale(self):
        self.assertGreaterEqual(len(self.apps), 30)
        for app in self.apps:
            self.assertTrue(app.value_evidence, f"{app.app_id} 缺 value_evidence")
            pop = app.popularity
            self.assertIsNotNone(pop.get("users"), f"{app.app_id} 流行度缺 users")
            self.assertGreaterEqual(pop["normalized"], 0.0)
            self.assertLessEqual(pop["normalized"], 1.0)
            for cap in app.capabilities:
                self.assertIn(cap.accessibility, ACCESSIBILITY)
                self.assertGreaterEqual(len(cap.intents), 3, f"{app.app_id} intents 太少")

    def test_covers_user_gap_domains(self):
        domains = {c.domain for c in self.cards}
        for d in ("image.gen", "image.edit", "video.gen", "video.edit",
                  "audio.music", "audio.tts", "3d.mesh", "game.engine"):
            self.assertIn(d, domains, f"缺口领域未覆盖: {d}")

    def test_all_human_operated_flagged(self):
        for c in self.cards:
            if c.accessibility == "gui":
                self.assertFalse(c.auto_callable, f"{c.id} 不应被 agent 自动调用")

    def test_music_query_recalls_suno(self):
        trace = search(self.cards, {}, "给短剧做主题曲 配乐 写歌", self.ctx, k=3)
        top = {rc.card.id for rc in trace.ranked}
        self.assertTrue(any(i.startswith("pro.suno") for i in top),
                        f"配乐查询应召回 Suno，实际: {top}")

    def test_gui_option_intro_marks_human_use(self):
        trace = search(self.cards, {}, "精修封面 修图 抠图", self.ctx, k=3)
        options = as_options(trace)
        self.assertTrue(options)
        gui_opts = [o for o in options if o["value"].startswith("pro.")]
        self.assertTrue(gui_opts)
        for o in gui_opts:
            self.assertIn("人工使用", o["intro"])

    def test_popularity_prior_orders_cold_start(self):
        """同查询下，头部平台（高 normalized）应压过低流行度同类。"""
        trace = search(self.cards, {}, "像素画 精灵图", self.ctx, k=5)
        order = [rc.card.id for rc in trace.ranked]
        self.assertLess(order.index("pro.aseprite"), order.index("pro.piskel"))


if __name__ == "__main__":
    unittest.main()
