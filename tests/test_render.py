import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from capnet.cards import load_cards
from capnet.render import render_html, render_terminal
from capnet.scoring import ScoreContext
from capnet.search import as_options, search
from capnet.telemetry import Telemetry

BASE = os.path.join(os.path.dirname(__file__), "..")


class TestRender(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cards = load_cards(os.path.join(BASE, "data", "cards.json"))
        tel = Telemetry.load(seed_path=os.path.join(BASE, "data", "telemetry_seed.json"))
        cls.stats = tel.stats(now=0.0, half_life=30.0)
        cls.ctx = ScoreContext(total_selections=tel.total_selections(now=0.0, half_life=30.0))
        trace = search(cls.cards, cls.stats, None, cls.ctx, domain="image.sprite", k=3)
        cls.options = as_options(trace)
        cls.boards = [{"query": "给游戏画像素风精灵图", "trace": trace, "options": cls.options}]

    def test_terminal_render(self):
        text = render_terminal(self.options, query="给游戏画像素风精灵图")
        self.assertIn("PixelForge Local", text)
        self.assertIn("能力缺口", text)
        self.assertIn("\033[", text)
        self.assertIn("新卡·探索性试用", text)

    def test_markdown_render(self):
        from capnet.render import render_markdown
        md = render_markdown(self.options, query="给游戏画像素风精灵图")
        self.assertIn("**能力缺口：", md)
        self.assertIn("**PixelForge Local（推荐）**", md)
        self.assertIn("Q 0.78", md)
        self.assertIn("`[新卡·探索性试用]`", md)
        self.assertNotIn("\033[", md)

    def test_html_render(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "board.html")
            render_html(self.boards, path, registry_size=len(self.cards))
            doc = open(path, encoding="utf-8").read()
        self.assertIn("PixelForge Local", doc)
        self.assertIn("给游戏画像素风精灵图", doc)
        self.assertIn("<style>", doc)
        self.assertIn("badge rec", doc)
        self.assertIn("badge new", doc)
        self.assertIn("漏斗", doc)

    def test_html_escapes_injection(self):
        evil = dict(self.options[0])
        evil["name"] = "<script>alert(1)</script>"
        evil["description"] = "<img onerror=x>"
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "b.html")
            render_html([{"query": "<b>q</b>", "trace": None, "options": [evil]}], path)
            doc = open(path, encoding="utf-8").read()
        self.assertNotIn("<script>alert(1)</script>", doc)
        self.assertNotIn("<img onerror=x>", doc)
        self.assertIn("&lt;script&gt;", doc)
        self.assertIn("&lt;b&gt;q&lt;/b&gt;", doc)


if __name__ == "__main__":
    unittest.main()
