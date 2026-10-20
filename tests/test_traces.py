import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from capnet.cards import load_cards
from capnet.traces import Step, Trace, credit, induce, redact
from capnet.workflows import load_workflows

BASE = os.path.join(os.path.dirname(__file__), "..")


def _tr(who, accepted=True, tools=("img.pixelforge", "mus.suno"), retries=0):
    return Trace(contributor=who, industry="game", accepted=accepted, steps=[
        Step(stage="资产", domain="image.sprite", entry_id=tools[0], retries=retries),
        Step(stage="配乐", domain="audio.music", entry_id=tools[1]),
    ])


class TestCredit(unittest.TestCase):
    def test_retries_count_as_failures_and_acceptance_as_approval(self):
        ev = credit(_tr("a", retries=2))
        kinds = [k for e, k, _ in ev if e == "img.pixelforge"]
        self.assertEqual(kinds.count("failure"), 2)
        self.assertEqual(kinds.count("success"), 1)
        self.assertEqual(kinds.count("approval"), 1)

    def test_rejected_trace_gives_no_approval(self):
        ev = credit(_tr("a", accepted=False))
        self.assertNotIn("approval", [k for _, k, _ in ev])


class TestInduce(unittest.TestCase):
    def test_support_counts_distinct_contributors_not_volume(self):
        spam = [_tr("same-user") for _ in range(50)]
        self.assertEqual(induce(spam), [])

    def test_three_contributors_induce_draft(self):
        res = induce([_tr("a"), _tr("b"), _tr("c", tools=("img.spritewave", "mus.suno"))])
        self.assertEqual(len(res), 1)
        wf = res[0].workflow
        self.assertEqual(wf.source["kind"], "conversation-induction")
        self.assertEqual([st.slots[0].domain for st in wf.stages], ["image.sprite", "audio.music"])
        self.assertEqual(wf.stages[0].slots[0].original_tool, "img.pixelforge")
        self.assertEqual(res[0].support, 3)

    def test_low_success_rate_blocked(self):
        trs = [_tr(w) for w in "abc"] + [_tr(w, accepted=False) for w in "defgh"]
        self.assertEqual(induce(trs), [])

    def test_isomorphic_to_existing_marks_reinforces(self):
        wfs = load_workflows(os.path.join(BASE, "data", "workflows"))
        sd = next(w for w in wfs if w.workflow_id == "short-drama-episode")
        sig = [sl.domain for st in sd.stages for sl in st.slots]
        trs = [Trace(contributor=w, industry="short-drama", accepted=True,
                     steps=[Step(stage=d, domain=d) for d in sig]) for w in "abc"]
        self.assertEqual(induce(trs, existing=wfs)[0].reinforces, "short-drama-episode")

    def test_redact_strips_task_and_hashes_contributor(self):
        tr = _tr("wysa")
        tr.task = "给我的游戏《灵猫战纪》做开场"
        r = redact(tr)
        self.assertEqual(r.task, "")
        self.assertNotEqual(r.contributor, "wysa")


class TestV1Accessibility(unittest.TestCase):
    def test_http_v1_cards_are_auto_callable(self):
        cards = {c.id: c for c in load_cards(os.path.join(BASE, "data", "cards.json"))}
        self.assertTrue(cards["tts.mimo"].auto_callable)


if __name__ == "__main__":
    unittest.main()
