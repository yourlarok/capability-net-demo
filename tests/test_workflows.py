import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from capnet.workflows import capability_gaps, load_workflows, parse_workflow

BASE = os.path.join(os.path.dirname(__file__), "..")


class TestWorkflows(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.wfs = load_workflows(os.path.join(BASE, "data", "workflows"))

    def test_three_seed_workflows_load(self):
        ids = {w.workflow_id for w in self.wfs}
        self.assertEqual(ids, {"game-dev", "short-drama-episode", "video-recap"})

    def test_sources_are_practice_summaries(self):
        for w in self.wfs:
            self.assertEqual(w.source["kind"], "practice-summary")
            self.assertTrue(w.source["derived_from"])

    def test_slots_have_domain_and_constraints(self):
        for w in self.wfs:
            for stage in w.stages:
                for slot in stage.slots:
                    self.assertTrue(slot.domain)
                    self.assertIsInstance(slot.constraints, dict)

    def test_game_dev_gaps(self):
        wf = next(w for w in self.wfs if w.workflow_id == "game-dev")
        gaps = capability_gaps(wf)
        domains = {sl.domain for _, sl in gaps}
        self.assertEqual(domains, {"image.sprite", "audio.music", "audio.sfx"})
        stages = {st for st, _ in gaps}
        self.assertEqual(stages, {"assets"})

    def test_short_drama_chain_constraints(self):
        wf = next(w for w in self.wfs if w.workflow_id == "short-drama-episode")
        keyframe = next(sl for st in wf.stages if st.id == "keyframe" for sl in st.slots)
        self.assertIn("style_consistency_with", keyframe.constraints)
        motion = next(sl for st in wf.stages if st.id == "motion" for sl in st.slots)
        self.assertEqual(motion.constraints.get("input_from"), "keyframe")

    def test_invalid_source_kind_raises(self):
        with self.assertRaises(ValueError):
            parse_workflow({"workflow_id": "x", "name": "x", "source": {"kind": "guessed"}, "stages": []})

    def test_completion_signals_present(self):
        for w in self.wfs:
            self.assertTrue(w.completion_signals)


if __name__ == "__main__":
    unittest.main()
