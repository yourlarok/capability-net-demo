import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from capnet.cards import load_apps, load_cards

BASE = os.path.join(os.path.dirname(__file__), "..")


class TestSchemaV2(unittest.TestCase):
    def _write(self, data):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "cards.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        return path

    def test_v1_cards_wrap_as_single_capability_apps(self):
        apps = load_apps(os.path.join(BASE, "data", "cards.json"))
        self.assertEqual(len(apps), 17)
        for app in apps:
            self.assertEqual(len(app.capabilities), 1)
            self.assertEqual(app.review_status, "approved")

    def test_v1_flatten_keeps_entry_ids_stable(self):
        cards = load_cards(os.path.join(BASE, "data", "cards.json"))
        ids = {c.id for c in cards}
        self.assertIn("img.pixelforge", ids)
        self.assertIn("tts.eleven", ids)
        pf = next(c for c in cards if c.id == "img.pixelforge")
        self.assertEqual(pf.app_id, "img.pixelforge")
        self.assertEqual(pf.accessibility, "cli")
        self.assertTrue(pf.auto_callable)

    def test_v2_multi_capability_flattens_with_qualified_ids(self):
        path = self._write([{
            "app_id": "vendor.superapp", "name": "SuperApp",
            "capabilities": [
                {"domain": "image.gen", "intents": ["画图"], "outputs": ["png"], "accessibility": "api"},
                {"domain": "audio.tts", "intents": ["配音"], "outputs": ["wav"], "accessibility": "gui"},
            ],
        }])
        cards = load_cards(path)
        self.assertEqual(len(cards), 2)
        ids = {c.id for c in cards}
        self.assertEqual(ids, {"vendor.superapp#image.gen", "vendor.superapp#audio.tts"})
        gui_card = next(c for c in cards if c.domain == "audio.tts")
        self.assertFalse(gui_card.auto_callable)

    def test_v2_single_capability_keeps_plain_app_id(self):
        path = self._write([{
            "app_id": "vendor.solo", "name": "Solo",
            "capabilities": [{"domain": "image.gen", "intents": ["画图"], "outputs": ["png"], "accessibility": "mcp"}],
        }])
        cards = load_cards(path)
        self.assertEqual(cards[0].id, "vendor.solo")

    def test_v2_missing_cap_field_raises(self):
        path = self._write([{
            "app_id": "bad.app", "name": "Bad",
            "capabilities": [{"domain": "image.gen", "intents": ["x"], "accessibility": "api"}],
        }])
        with self.assertRaises(ValueError):
            load_apps(path)

    def test_v2_illegal_accessibility_raises(self):
        path = self._write([{
            "app_id": "bad.app", "name": "Bad",
            "capabilities": [{"domain": "image.gen", "intents": ["x"], "outputs": ["png"], "accessibility": "telepathy"}],
        }])
        with self.assertRaises(ValueError):
            load_apps(path)

    def test_v2_extras_passthrough(self):
        path = self._write([{
            "app_id": "vendor.x", "name": "X",
            "capabilities": [{"domain": "devtools.git", "intents": ["git"], "outputs": ["json"],
                              "accessibility": "mcp", "mcp_remotes": ["https://example.com/mcp"]}],
        }])
        apps = load_apps(path)
        self.assertEqual(apps[0].capabilities[0].extras["mcp_remotes"], ["https://example.com/mcp"])


if __name__ == "__main__":
    unittest.main()
