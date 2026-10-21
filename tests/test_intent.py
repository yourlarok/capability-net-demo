import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from capnet.intent import card_families, query_families, tier
from capnet.intent_eval import run
from capnet.registry import Registry
from capnet.scoring import ScoreContext
from capnet.search import search

EVAL = os.path.join(os.path.dirname(__file__), "..", "data", "eval")


def primary(q):
    f = query_families(q)
    top = max(f.values())
    return {k for k, v in f.items() if v >= top * 0.99}


class TestQueryFamilies(unittest.TestCase):
    def test_disambiguation(self):
        self.assertEqual(primary("画画"), {"image"})
        self.assertIn("video", primary("视频画面太糊了"))      # 画面 ≠ 画画
        self.assertEqual(primary("画外音怎么做"), {"speech"})
        self.assertEqual(primary("用户画像分析"), {"data"})
        self.assertEqual(primary("给视频配音"), {"speech"})     # 视频只是上下文
        self.assertEqual(primary("像素风游戏的循环背景音乐"), {"music"})  # 中心词在后

    def test_unknown_means_no_gate(self):
        self.assertEqual(query_families("帮我想想办法"), {})
        self.assertEqual(tier("audio.tts", {}), 2)

    def test_card_families(self):
        self.assertEqual(card_families("audio.tts"), {"speech"})
        self.assertEqual(card_families("audio.music"), {"music"})
        self.assertIn("image", card_families("video.storyboard"))


class TestGate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reg = Registry.load(load_hub=False)
        cls.ctx = ScoreContext(total_selections=cls.reg.total_selections)

    def top_domains(self, q, **kw):
        tr = search(self.reg.cards, self.reg.stats, q, self.ctx, k=5, **kw)
        return [r.card.domain for r in tr.ranked]

    def test_drawing_never_returns_voice(self):
        for q in ("画画", "画一个角色立绘", "水彩画", "画个头像"):
            doms = self.top_domains(q)
            self.assertTrue(doms, q)
            self.assertFalse([d for d in doms if d.startswith("audio")], (q, doms))

    def test_declared_type_overrides_keywords(self):
        doms = self.top_domains("给动画角色配个萌妹声音", families=["speech"])
        self.assertTrue(doms and all(d in ("audio.tts", "audio.asr") for d in doms), doms)

    def test_eval_floors(self):
        self.assertGreaterEqual(run(os.path.join(EVAL, "capability_intent.json"))["top1"], 0.95)
        self.assertGreaterEqual(run(os.path.join(EVAL, "capability_intent_blind.json"))["top1"], 0.90)
        r = run(os.path.join(EVAL, "capability_intent_blind2.json"), declared=True)
        self.assertEqual(r["cross_per_query"], 0.0)  # agent 声明类型后不串族


if __name__ == "__main__":
    unittest.main()
