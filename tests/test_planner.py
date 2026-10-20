import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from capnet.evaluate import run_eval
from capnet.planner import plan, render_plan
from capnet.registry import Registry
from capnet.workflows import is_workflow_task

BASE = os.path.join(os.path.dirname(__file__), "..")
EVAL = os.path.join(BASE, "data", "eval")


class TestRegistry(unittest.TestCase):
    def test_loads_all_catalogs(self):
        reg = Registry.load()
        apps = {c.app_id for c in reg.cards}
        self.assertIn("pro.capcut", apps)          # pro_tools.json
        self.assertIn("biz.juliang-suanshu", apps)  # catalog/business.json
        self.assertIn("img.pixelforge", apps)       # cards.json


class TestPlanner(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reg = Registry.load()
        cls.p = plan("我们公司的产品宣传视频播放量很低，帮我出一个完善的视频改进计划", cls.reg)

    def test_marketing_matches_and_fully_covered(self):
        self.assertEqual(self.p.workflow.workflow_id, "marketing-video-improve")
        self.assertEqual(self.p.coverage, 1.0)

    def test_primary_domain_beats_alt_domain(self):
        copy = next(f for f in self.p.fills if f.slot.slot_id == "copywriting")
        self.assertEqual(copy.options[0]["domain"], "text.copywriting")

    def test_no_duplicate_app_in_slot(self):
        for f in self.p.fills:
            names = [o["name"] for o in f.options]
            self.assertEqual(len(names), len(set(names)), f.slot.slot_id)

    def test_manual_tools_are_labeled(self):
        md = render_plan(self.p)
        self.assertIn("人工使用·gui", md)
        self.assertIn("agent 可调用·api", md)

    def test_out_of_scope_rejected(self):
        self.assertFalse(plan("设计一个三居室的装修方案", self.reg).matched)
        self.assertFalse(plan("我的电脑播放视频卡顿怎么办", self.reg).matched)

    def test_explicit_task_type_overrides_cues(self):
        self.assertFalse(is_workflow_task("没什么反响"))
        self.assertTrue(is_workflow_task("没什么反响", task_type="improve"))

    def test_trace_template_links_plan_to_flywheel(self):
        tr = self.p.trace_template("me")
        self.assertEqual(len(tr.steps), len(self.p.fills))
        self.assertFalse(tr.accepted)


class TestEvalRegression(unittest.TestCase):
    """三套评测集的回归下限。blind 集写完后未针对其调参，数字是真实泛化水平。"""

    def _check(self, name, hit, reject):
        _, s = run_eval(Registry.load(), os.path.join(EVAL, name))
        self.assertGreaterEqual(s["hit@1"], hit, name)
        self.assertGreaterEqual(s["reject"], reject, name)

    def test_dev_set(self):
        self._check("queries.json", 0.9, 1.0)

    def test_holdout_set(self):
        self._check("holdout.json", 0.9, 1.0)

    def test_blind_set(self):
        self._check("blind.json", 0.85, 1.0)


if __name__ == "__main__":
    unittest.main()
