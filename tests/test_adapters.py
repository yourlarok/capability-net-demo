import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from capnet.adapters.huggingface import _intent_tokens, model_to_app, percentile_ranks
from capnet.adapters.mcp_registry import classify_domain, dedupe_latest, server_to_app

FIXTURE_ENTRY = {
    "server": {
        "name": "ac.inference.sh/mcp",
        "title": "inference.sh",
        "description": "Run 150+ AI apps — image, video, audio, LLMs, 3D and more.",
        "version": "1.0.1",
        "remotes": [{"type": "streamable-http", "url": "https://api.inference.sh/mcp"}],
    },
    "_meta": {"io.modelcontextprotocol.registry/official": {"status": "active", "isLatest": True}},
}


class TestMcpAdapter(unittest.TestCase):
    def test_server_to_app_mapping(self):
        app = server_to_app(FIXTURE_ENTRY)
        self.assertEqual(app["app_id"], "mcp.ac.inference.sh/mcp")
        self.assertEqual(app["name"], "inference.sh")
        self.assertEqual(app["review_status"], "draft")
        cap = app["capabilities"][0]
        self.assertEqual(cap["accessibility"], "mcp")
        self.assertEqual(cap["mcp_remotes"], ["https://api.inference.sh/mcp"])
        self.assertEqual(app["popularity"]["source"], "mcp-official")

    def test_classify_domain(self):
        self.assertEqual(classify_domain("github repo manager"), "devtools.git")
        self.assertEqual(classify_domain("text to speech voice clone"), "audio.tts")
        self.assertEqual(classify_domain("postgres database server"), "data.database")
        self.assertEqual(classify_domain("completely unrelated thing"), "misc")

    def test_dedupe_keeps_latest(self):
        v1 = {"server": {"name": "a/b", "version": "1.0.0", "description": "x"},
              "_meta": {"io.modelcontextprotocol.registry/official": {"isLatest": False}}}
        v2 = {"server": {"name": "a/b", "version": "1.0.1", "description": "x"},
              "_meta": {"io.modelcontextprotocol.registry/official": {"isLatest": True}}}
        out = dedupe_latest([v1, v2])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["server"]["version"], "1.0.1")

    def test_dedupe_without_latest_flag_picks_higher_version(self):
        v1 = {"server": {"name": "a/b", "version": "1.0.0", "description": "x"}}
        v2 = {"server": {"name": "a/b", "version": "1.2.0", "description": "x"}}
        out = dedupe_latest([v2, v1])
        self.assertEqual(out[0]["server"]["version"], "1.2.0")

    def test_draft_card_passes_schema_loader(self):
        import json
        import tempfile
        from capnet.cards import load_apps
        app = server_to_app(FIXTURE_ENTRY)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "c.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump([app], f)
            apps = load_apps(path)
        self.assertEqual(apps[0].review_status, "draft")
        self.assertEqual(apps[0].capabilities[0].extras["mcp_remotes"], ["https://api.inference.sh/mcp"])


HF_MODEL = {
    "id": "stabilityai/stable-diffusion-xl-base-1.0",
    "author": "stabilityai",
    "downloads": 4288373,
    "likes": 8362,
    "pipeline_tag": "text-to-image",
    "library_name": "diffusers",
    "gated": False,
    "tags": ["diffusers", "safetensors", "text-to-image", "stable-diffusion",
             "arxiv:2307.01952", "license:openrail++", "endpoints_compatible",
             "deploy:sagemaker", "region:us"],
}


class TestHfAdapter(unittest.TestCase):
    def test_model_to_app_mapping(self):
        app = model_to_app(HF_MODEL, normalized=1.0)
        self.assertEqual(app["app_id"], "hf.stabilityai/stable-diffusion-xl-base-1.0")
        self.assertEqual(app["vendor"], "stabilityai")
        self.assertEqual(app["review_status"], "approved")
        self.assertEqual(app["popularity"]["users"], 4288373)
        self.assertEqual(app["popularity"]["normalized"], 1.0)
        cap = app["capabilities"][0]
        self.assertEqual(cap["domain"], "image.gen")
        self.assertEqual(cap["accessibility"], "api")
        self.assertEqual(cap["outputs"], ["image"])
        self.assertEqual(cap["hf_pipeline"], "text-to-image")

    def test_intent_tokens_filter_metadata_noise(self):
        intents = _intent_tokens(HF_MODEL)
        self.assertIn("stable", intents)
        self.assertIn("diffusion", intents)
        self.assertIn("image", intents)
        for noise in ("openrail", "sagemaker", "arxiv", "safetensors", "endpoints"):
            self.assertNotIn(noise, intents)

    def test_unmapped_tag_is_draft_misc(self):
        m = dict(HF_MODEL, pipeline_tag="other")
        app = model_to_app(m, 0.5)
        self.assertEqual(app["review_status"], "draft")
        self.assertEqual(app["capabilities"][0]["domain"], "misc")

    def test_gated_noted_in_description(self):
        m = dict(HF_MODEL, gated=True)
        self.assertIn("gated", model_to_app(m, 0.5)["description"])

    def test_percentile_ranks(self):
        models = [{"id": "a", "downloads": 10}, {"id": "b", "downloads": 100},
                  {"id": "c", "downloads": 1000}]
        pct = percentile_ranks(models)
        self.assertEqual(pct["c"], 1.0)
        self.assertEqual(pct["a"], 0.0)
        self.assertAlmostEqual(pct["b"], 0.5)
        self.assertEqual(percentile_ranks([{"id": "solo", "downloads": 1}])["solo"], 1.0)

    def test_hf_card_passes_schema_loader(self):
        import json
        import tempfile
        from capnet.cards import load_apps
        app = model_to_app(HF_MODEL, 0.9)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "c.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump([app], f)
            apps = load_apps(path)
        self.assertEqual(apps[0].capabilities[0].extras["hf_pipeline"], "text-to-image")
        self.assertEqual(apps[0].popularity["users"], 4288373)


if __name__ == "__main__":
    unittest.main()
