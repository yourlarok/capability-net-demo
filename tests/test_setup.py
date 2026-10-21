import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from capnet import setup_agent
from capnet.mcp_server import agent_rules

BASE = os.path.join(os.path.dirname(__file__), "..")


class TestBlocks(unittest.TestCase):
    def test_block_upsert_idempotent_and_removable(self):
        t = setup_agent.upsert_block("# 我的规则\n", "A")
        t2 = setup_agent.upsert_block(t, "B")
        self.assertEqual(t2.count(setup_agent.BEGIN), 1)
        self.assertIn("B", t2)
        self.assertNotIn("\nA\n", t2)
        self.assertEqual(setup_agent.upsert_block(t2, None), "# 我的规则\n")

    def test_toml_upsert(self):
        t = '[model]\nname = "x"\n\n[mcp_servers.capnet]\ncommand = "old"\n\n[mcp_servers.capnet.env]\nA = "1"\n\n[other]\nk = 1\n'
        out = setup_agent.upsert_toml(t, '[mcp_servers.capnet]\ncommand = "new"\n')
        self.assertEqual(out.count("[mcp_servers.capnet]"), 1)
        self.assertIn('command = "new"', out)
        self.assertNotIn("old", out)
        self.assertIn("[other]", out)
        self.assertIn("[model]", out)

    def test_skill_matches_repo(self):
        repo = open(os.path.join(BASE, ".claude", "skills", "capnet", "SKILL.md"), encoding="utf-8").read()
        self.assertEqual(setup_agent.skill_text().strip(), repo.strip())


class TestRun(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.saved = {k: os.environ.get(k) for k in ("HOME", "USERPROFILE", "APPDATA")}
        os.environ["HOME"] = os.environ["USERPROFILE"] = self.tmp
        os.environ["APPDATA"] = os.path.join(self.tmp, "AppData")

    def tearDown(self):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_install_twice_then_remove(self):
        cur = os.path.join(self.tmp, ".cursor", "mcp.json")
        os.makedirs(os.path.dirname(cur))
        json.dump({"mcpServers": {"other": {"command": "x"}}}, open(cur, "w"))
        agents = os.path.join(self.tmp, ".codex", "AGENTS.md")
        os.makedirs(os.path.dirname(agents))
        open(agents, "w", encoding="utf-8").write("# 我自己的规则\n")
        only = ["cursor", "codex", "gemini-cli"]
        for _ in range(2):
            rep = setup_agent.run(only)
            self.assertFalse(rep.errors, rep.errors)
        data = json.load(open(cur))
        self.assertEqual(set(data["mcpServers"]), {"other", "capnet"})
        toml = open(os.path.join(self.tmp, ".codex", "config.toml"), encoding="utf-8").read()
        self.assertEqual(toml.count("[mcp_servers.capnet]"), 1)
        md = open(agents, encoding="utf-8").read()
        self.assertEqual(md.count(setup_agent.BEGIN), 1)
        self.assertIn(agent_rules()[:20], md)
        self.assertIn("capnet", json.load(open(os.path.join(self.tmp, ".gemini", "settings.json")))["mcpServers"])

        setup_agent.run(only, remove=True)
        self.assertEqual(set(json.load(open(cur))["mcpServers"]), {"other"})
        self.assertNotIn("capnet", open(os.path.join(self.tmp, ".codex", "config.toml"), encoding="utf-8").read())
        self.assertEqual(open(agents, encoding="utf-8").read(), "# 我自己的规则\n")

    def test_dry_run_touches_nothing(self):
        setup_agent.run(["cursor", "codex"], dry_run=True)
        self.assertEqual(os.listdir(self.tmp), [])

    def test_broken_json_reported_not_overwritten(self):
        cur = os.path.join(self.tmp, ".cursor", "mcp.json")
        os.makedirs(os.path.dirname(cur))
        open(cur, "w").write("{oops")
        rep = setup_agent.run(["cursor"])
        self.assertTrue(rep.errors)
        self.assertEqual(open(cur).read(), "{oops")


if __name__ == "__main__":
    unittest.main()
