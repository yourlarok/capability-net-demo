import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from capnet.cards import load_apps, load_cards, parse_app
from capnet.domains import UserProfile
from capnet.scoring import ScoreContext, Stats, Weights
from capnet.search import search, search_store
from capnet.store import SCHEMA, Store
from capnet.telemetry import Telemetry

BASE = os.path.join(os.path.dirname(__file__), "..")
CARDS = os.path.join(BASE, "data", "cards.json")
SEED = os.path.join(BASE, "data", "telemetry_seed.json")


class TestStore(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.db = os.path.join(cls.tmp, "registry.db")
        store = Store(cls.db)
        store.upsert_apps(load_apps(CARDS))
        import json
        with open(SEED, encoding="utf-8") as f:
            for entry_id, counts in json.load(f).items():
                store.add_seed(entry_id, Stats(**counts))
        store.rebuild_idf()
        store.close()

    def _store(self):
        return Store(self.db)

    def test_counts(self):
        c = self._store().counts()
        self.assertEqual(c["entries"], 17)
        self.assertEqual(c["drafts"], 0)

    def test_store_search_matches_memory_search(self):
        cards = load_cards(CARDS)
        tel = Telemetry.load(seed_path=SEED)
        stats = tel.stats(now=0.0, half_life=30.0)
        ctx_mem = ScoreContext(total_selections=sum(s.selections for s in stats.values()))
        mem = search(cards, stats, "给游戏画像素风精灵图", ctx_mem, k=5)

        store = self._store()
        ctx_st = ScoreContext(total_selections=store.total_selections())
        st = search_store(store, "给游戏画像素风精灵图", ctx_st, k=5)
        store.close()

        self.assertEqual([r.card.id for r in mem.ranked], [r.card.id for r in st.ranked])

    def test_draft_excluded_until_approved(self):
        import tempfile as _tf
        db = os.path.join(_tf.mkdtemp(), "iso.db")
        store = Store(db)
        store.upsert_apps(load_apps(CARDS))
        draft = parse_app({
            "app_id": "vendor.drafty", "name": "Drafty Sprite King",
            "description": "像素 sprite 精灵图 画图 神器",
            "review_status": "draft",
            "capabilities": [{"domain": "image.sprite", "intents": ["像素画", "精灵图", "sprite"],
                              "outputs": ["png"], "accessibility": "mcp"}],
        })
        store.upsert_draft(draft)
        store.rebuild_idf()
        ctx = ScoreContext(total_selections=store.total_selections())
        ids = [r.card.id for r in search_store(store, "像素精灵图", ctx, k=8).ranked]
        self.assertNotIn("vendor.drafty", ids)
        self.assertGreater(store.approve("vendor.drafty"), 0)
        ids = [r.card.id for r in search_store(store, "像素精灵图", ctx, k=8).ranked]
        self.assertIn("vendor.drafty", ids)
        store.close()

    def test_domain_filter_and_unhealthy(self):
        store = self._store()
        ctx = ScoreContext(total_selections=store.total_selections())
        trace = search_store(store, None, ctx, domain="audio.tts", k=8)
        self.assertTrue(all(r.card.domain == "audio.tts" for r in trace.ranked))
        trace = search_store(store, None, ctx, domain="video.text2video", k=8)
        self.assertNotIn("vid.unstable", [r.card.id for r in trace.ranked])
        store.close()

    def test_event_decay(self):
        store = self._store()
        store.record("img.pixelforge", "selection", t=0.0)
        s0 = store.stats_for(["img.pixelforge"], now=0.0, half_life=30.0)["img.pixelforge"]
        s1 = store.stats_for(["img.pixelforge"], now=30.0, half_life=30.0)["img.pixelforge"]
        self.assertAlmostEqual(s0.selections, 91.0, places=3)
        self.assertAlmostEqual(s1.selections, 90.5, places=3)
        store.close()

    def test_future_events_not_amplified(self):
        store = self._store()
        store.record("img.sdcomfy", "selection", t=100.0)
        s = store.stats_for(["img.sdcomfy"], now=0.0, half_life=30.0)["img.sdcomfy"]
        # 未来事件钳到权重 1.0（记为最新），不得放大（>1）
        self.assertAlmostEqual(s.selections, 281.0, places=3)
        s_now = store.stats_for(["img.sdcomfy"], now=100.0, half_life=30.0)["img.sdcomfy"]
        self.assertAlmostEqual(s_now.selections, 281.0, places=3)
        store.close()

    def test_profile_affinity_bounded_and_default_neutral(self):
        p = UserProfile(domain_weights={"image.sprite": 1.0}, boost_max=0.15)
        self.assertAlmostEqual(p.affinity("image.sprite"), 1.15)
        self.assertEqual(p.affinity("audio.tts"), 1.0)
        p2 = UserProfile(domain_weights={"image.*": 0.5})
        self.assertAlmostEqual(p2.affinity("image.gen"), 1.075)


class TestPerf(unittest.TestCase):
    """扩容效率压测：2 万条目下建索引与查询延迟。"""

    N = 20000

    @classmethod
    def setUpClass(cls):
        import random
        import time as _time
        rng = random.Random(42)
        domains = ["image.sprite", "image.gen", "video.text2video", "audio.tts",
                   "audio.music", "devtools.git", "data.search", "office.docs", "misc"]
        zh = ["像素", "精灵图", "配音", "配乐", "视频", "生成", "检索", "部署", "文档", "游戏素材"]
        en = ["sprite", "image", "video", "tts", "music", "search", "deploy", "docs", "git", "llm"]
        apps = []
        for i in range(cls.N):
            d = domains[i % len(domains)]
            apps.append({
                "app_id": f"perf.app{i}", "name": f"PerfApp{i}",
                "description": " ".join(rng.sample(en, 4)) + " " + " ".join(rng.sample(zh, 3)),
                "capabilities": [{
                    "domain": d, "intents": rng.sample(en, 3) + rng.sample(zh, 2),
                    "outputs": ["png"], "accessibility": "api",
                    "cost_per_call": round(rng.random() * 0.1, 3),
                }],
            })
        cls.tmp = tempfile.mkdtemp()
        cls.db = os.path.join(cls.tmp, "perf.db")
        store = Store(cls.db)
        t0 = _time.time()
        store.upsert_apps([parse_app(a) for a in apps])
        store.rebuild_idf()
        cls.build_seconds = _time.time() - t0
        cls._store = store

    @classmethod
    def tearDownClass(cls):
        cls._store.close()

    def test_build_index_fast_enough(self):
        self.assertLess(self.build_seconds, 60.0)

    def test_query_latency(self):
        import random
        import time as _time
        rng = random.Random(7)
        queries = ["像素 精灵图 生成", "sprite image", "tts 配音", "video 生成",
                   "search 检索", "deploy 部署", "游戏素材", "llm docs"]
        ctx = ScoreContext(total_selections=1e6)
        t0 = _time.time()
        n = 200
        for _ in range(n):
            search_store(self._store, rng.choice(queries), ctx, k=5)
        avg = (_time.time() - t0) / n
        self.assertLess(avg, 0.1, f"平均查询 {avg*1000:.1f}ms 超标")

    def test_planted_token_found(self):
        store = self._store  # 类级共享连接，tearDownClass 统一关闭
        store.upsert_apps([parse_app({
            "app_id": "perf.unicorn", "name": "UnicornSprite",
            "description": "独一无二的 zqxyz 标记",
            "capabilities": [{"domain": "image.sprite", "intents": ["zqxyz", "精灵图"],
                              "outputs": ["png"], "accessibility": "api"}],
        })])
        store.rebuild_idf()
        ctx = ScoreContext(total_selections=1e6)
        trace = search_store(store, "zqxyz 精灵图", ctx, k=3)
        self.assertTrue(trace.ranked)
        self.assertEqual(trace.ranked[0].card.id, "perf.unicorn")


class TestExtrasPopularityRoundtrip(unittest.TestCase):
    """extras / popularity 必须完整落库并读回（独立临时库，不碰共享类库）。"""

    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            store = Store(os.path.join(d, "r.db"))
            store.upsert_apps([parse_app({
                "app_id": "hf.x/y", "name": "y", "vendor": "x",
                "popularity": {"source": "hf", "users": 5000, "reviews": 10,
                               "normalized": 0.9},
                "capabilities": [{"domain": "image.gen", "intents": ["像素"],
                                  "outputs": ["image"], "accessibility": "api",
                                  "hf_pipeline": "text-to-image"}],
            })])
            store.rebuild_idf()
            card = store.get_entries(["hf.x/y"])["hf.x/y"]
            self.assertEqual(card.popularity["normalized"], 0.9)
            self.assertEqual(card.popularity["users"], 5000)
            self.assertEqual(card.extras["hf_pipeline"], "text-to-image")
            store.close()

    def test_old_db_migrated(self):
        """没有 popularity 列的旧库应被 ALTER 迁移且数据不丢。"""
        import sqlite3
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "old.db")
            conn = sqlite3.connect(path)
            conn.executescript(SCHEMA.replace(", popularity TEXT", ""))
            conn.execute("INSERT INTO entries (id, app_id, name, vendor, domain, description,"
                         " cost, latency, accessibility, healthy, review_status, true_quality,"
                         " inputs, outputs, intents, extras) VALUES"
                         " ('e1','a','n','v','misc','d',0,'medium','api',1,'approved',0.5,"
                         " '[]','[]','[]','{}')")
            conn.commit()
            conn.close()
            store = Store(path)  # 触发迁移
            self.assertEqual(store.counts()["entries"], 1)
            self.assertEqual(store.get_entries(["e1"])["e1"].popularity, {})
            store.close()


if __name__ == "__main__":
    unittest.main()
