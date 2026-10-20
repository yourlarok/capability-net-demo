import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from capnet.cards import load_cards
from capnet.scoring import ScoreContext
from capnet.search import search, tokenize
from capnet.telemetry import Telemetry

BASE = os.path.join(os.path.dirname(__file__), "..")


class TestTokenize(unittest.TestCase):
    def test_cjk_bigrams(self):
        tokens = tokenize("给游戏画像素风精灵图")
        self.assertIn("像素", tokens)
        self.assertIn("精灵", tokens)

    def test_latin(self):
        self.assertIn("sprite", tokenize("Sprite 生成"))


class TestSearch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cards = load_cards(os.path.join(BASE, "data", "cards.json"))
        tel = Telemetry.load(seed_path=os.path.join(BASE, "data", "telemetry_seed.json"))
        cls.stats = tel.stats(now=0.0, half_life=30.0)
        cls.ctx = ScoreContext(total_selections=tel.total_selections(now=0.0, half_life=30.0))

    def test_unhealthy_filtered(self):
        trace = search(self.cards, self.stats, None, self.ctx, domain="video.text2video")
        ids = [rc.card.id for rc in trace.ranked]
        self.assertNotIn("vid.unstable", ids)

    def test_pixel_query_ranks_sprite_cards(self):
        trace = search(self.cards, self.stats, "给游戏画像素风精灵图", self.ctx, k=5)
        self.assertTrue(trace.ranked)
        top_domains = {rc.card.domain for rc in trace.ranked[:3]}
        self.assertIn("image.sprite", top_domains)
        top_ids = [rc.card.id for rc in trace.ranked[:3]]
        self.assertIn("img.pixelforge", top_ids)

    def test_quality_beats_exposure_in_rank(self):
        trace = search(self.cards, self.stats, None, self.ctx, domain="image.sprite", k=5)
        ids = [rc.card.id for rc in trace.ranked]
        self.assertLess(ids.index("img.pixelforge"), ids.index("img.megaimage"))

    def test_cold_start_card_appears(self):
        trace = search(self.cards, self.stats, None, self.ctx, domain="image.sprite", k=5)
        ids = [rc.card.id for rc in trace.ranked]
        self.assertIn("img.spritewave", ids)

    def test_cost_constraint(self):
        ctx = ScoreContext(total_selections=self.ctx.total_selections, max_cost=0.005)
        trace = search(self.cards, self.stats, "给游戏画像素风精灵图", ctx, k=5)
        for rc in trace.ranked:
            self.assertLess(rc.card.cost_per_call, 0.01)

    def test_domain_filter(self):
        trace = search(self.cards, self.stats, None, self.ctx, domain="audio.tts")
        self.assertTrue(all(rc.card.domain == "audio.tts" for rc in trace.ranked))

    def test_tts_quality_leader_first(self):
        trace = search(self.cards, self.stats, None, self.ctx, domain="audio.tts", k=3)
        self.assertEqual(trace.ranked[0].card.id, "tts.eleven")

    def test_relevance_normalized(self):
        trace = search(self.cards, self.stats, "配一段游戏主题曲", self.ctx, k=5)
        for rc in trace.ranked:
            self.assertLessEqual(rc.relevance, 1.0)
            self.assertGreater(rc.relevance, 0.0)

    def test_music_query_stays_in_music_domain(self):
        trace = search(self.cards, self.stats, "像素风游戏的循环背景音乐", self.ctx, k=3)
        self.assertTrue(trace.ranked)
        self.assertEqual(trace.ranked[0].card.domain, "audio.music")

    def test_sfx_query_stays_in_sfx_domain(self):
        trace = search(self.cards, self.stats, "跳跃金币受伤的游戏音效", self.ctx, k=3)
        self.assertTrue(trace.ranked)
        self.assertEqual(trace.ranked[0].card.domain, "audio.sfx")


class TestOptions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cards = load_cards(os.path.join(BASE, "data", "cards.json"))
        tel = Telemetry.load(seed_path=os.path.join(BASE, "data", "telemetry_seed.json"))
        cls.stats = tel.stats(now=0.0, half_life=30.0)
        cls.ctx = ScoreContext(total_selections=tel.total_selections(now=0.0, half_life=30.0))

    def test_options_shape(self):
        from capnet.search import as_options
        trace = search(self.cards, self.stats, None, self.ctx, domain="image.sprite", k=5)
        options = as_options(trace)
        self.assertEqual(len(options), 3)
        for o in options:
            self.assertIn("label", o)
            self.assertIn("intro", o)
            self.assertIn("value", o)
        self.assertIn("推荐", options[0]["label"])
        self.assertNotIn("推荐", options[1]["label"])

    def test_new_card_tagged(self):
        from capnet.search import as_options
        trace = search(self.cards, self.stats, None, self.ctx, domain="image.sprite", k=5)
        options = as_options(trace)
        by_value = {o["value"]: o for o in options}
        self.assertIn("img.spritewave", by_value)
        self.assertIn("新卡·探索性试用", by_value["img.spritewave"]["intro"])

    def test_intro_contains_cost_and_data(self):
        from capnet.search import as_options
        trace = search(self.cards, self.stats, None, self.ctx, domain="audio.tts", k=3)
        options = as_options(trace)
        first = options[0]
        self.assertIn("单次成本", first["intro"])
        self.assertIn("次调用", first["intro"])


class TestPopularityPrior(unittest.TestCase):
    """源外部流行度作为冷启动认可度先验：有数据的卡起步更高，无数据的走默认。"""

    @staticmethod
    def _card(cid, popularity):
        from capnet.cards import Card
        return Card(id=cid, name=cid, domain="image.gen", description="像素 精灵 生成",
                    intents=["像素", "精灵", "生成"], outputs=["image"],
                    cost_per_call=0.0, latency="medium", popularity=popularity)

    def test_popular_card_wins_cold_start(self):
        cards = [self._card("a.unknown", {"source": "mcp-official", "users": None,
                                          "normalized": None}),
                 self._card("b.popular", {"source": "hf", "users": 50000, "normalized": 1.0})]
        ctx = ScoreContext(total_selections=100.0)
        trace = search(cards, {}, "像素 精灵", ctx, k=2)
        self.assertEqual(trace.ranked[0].card.id, "b.popular")
        self.assertGreater(trace.ranked[0].breakdown.recognition,
                           trace.ranked[1].breakdown.recognition)

    def test_unknown_popularity_uses_default_prior(self):
        c = self._card("a.unknown", {"source": "mcp-official", "users": None,
                                     "normalized": None})
        ctx = ScoreContext(total_selections=100.0)
        trace = search([c], {}, "像素", ctx, k=1)
        self.assertAlmostEqual(trace.ranked[0].breakdown.recognition, 0.5)

    def test_zero_normalized_is_a_signal_not_missing(self):
        c = self._card("a.bottom", {"source": "hf", "users": 1200, "normalized": 0.0})
        ctx = ScoreContext(total_selections=100.0)
        trace = search([c], {}, "像素", ctx, k=1)
        self.assertAlmostEqual(trace.ranked[0].breakdown.recognition, 0.2)


class TestGapDetection(unittest.TestCase):
    """缺口判定：只命中通用词（合成/服务）不算答上来，特征词必须被覆盖。"""

    @classmethod
    def setUpClass(cls):
        cls.cards = load_cards(os.path.join(BASE, "data", "cards.json"))
        tel = Telemetry.load(seed_path=os.path.join(BASE, "data", "telemetry_seed.json"))
        cls.stats = tel.stats(now=0.0, half_life=30.0)
        cls.ctx = ScoreContext(total_selections=tel.total_selections(now=0.0, half_life=30.0))

    def test_real_query_not_gap(self):
        for q in ("给游戏画像素风精灵图", "tts 配音", "背景音乐 配乐生成"):
            trace = search(self.cards, self.stats, q, self.ctx, k=3)
            self.assertFalse(trace.gap, f"{q} 不应判为缺口")

    def test_nonsense_query_is_gap(self):
        for q in ("量子计算云服务", "气味合成器", "全息投影实时渲染引擎", "视频剪辑软件"):
            trace = search(self.cards, self.stats, q, self.ctx, k=3)
            self.assertTrue(trace.gap, f"{q} 应判为缺口（top={trace.ranked[0].card.id if trace.ranked else None}）")

    def test_distinctive_tokens_prefers_rare(self):
        from capnet.search import distinctive_tokens
        idf = {"合成": 1.0, "tts": 2.0}
        dt = distinctive_tokens("气味合成器", idf, unseen_idf=3.0)
        self.assertIn("气味", dt)      # 未见过的稀有词是特征词
        self.assertNotIn("合成", dt)   # 通用语素不是
        self.assertNotIn("计", dt)     # 单字不参与判定


if __name__ == "__main__":
    unittest.main()
