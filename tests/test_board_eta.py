import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
from token_ledger.board import eta, events  # noqa: E402
from token_ledger.board.plans import ChecklistItem, Plan  # noqa: E402

NOW = 1_800_000_000.0
H = 3600.0


def mkplan(phase="build", state="active", local=(False,) * 8, **kw):
    p = Plan(plan="900", phase=phase, state=state, **kw)
    p.checklist = [ChecklistItem(str(i), "x", v, False) for i, v in enumerate(local, 1)]
    return p


def ticks(n, start, step):
    return [{"t": start + i * step, "plan": "900", "kind": "tick_local", "item": str(i)} for i in range(n)]


class EtaTests(unittest.TestCase):
    def test_burst_of_ticks_lowers_eta(self):
        p = mkplan(local=(True,) * 4 + (False,) * 4)
        base = eta.estimate(p, NOW, [{"t": NOW - 1.5 * H, "plan": "900", "kind": "state"}])
        fast = eta.estimate(p, NOW, ticks(4, NOW - 0.6 * H, 300) + [{"t": NOW - 60, "plan": "900", "kind": "state"}])
        self.assertLess(fast.high_h, base.high_h)
        self.assertGreater(fast.speed, base.speed)

    def test_idle_raises_eta_and_stuck_after_2h(self):
        p = mkplan()
        ev = ticks(3, NOW - 4 * H, 300)
        fresh = eta.estimate(p, NOW - 3.5 * H + 600, ev)
        idle1 = eta.estimate(p, NOW - 2.5 * H, ev)
        idle3 = eta.estimate(p, NOW, ev)
        self.assertFalse(fresh.stuck)
        self.assertGreater(idle1.high_h, fresh.high_h)
        self.assertTrue(idle3.stuck)
        self.assertGreater(idle3.high_h, idle1.high_h)
        self.assertIn("stuck", idle3.label)

    def test_waiting_owner_excluded(self):
        p = mkplan(state="waiting-owner")
        e = eta.estimate(p, NOW, ticks(2, NOW - 10 * H, 60))
        self.assertTrue(e.waiting_owner)
        self.assertFalse(e.stuck)
        self.assertEqual(e.label, "⏸ waiting on you")

    def test_done_and_paused_labels(self):
        self.assertEqual(eta.estimate(mkplan("done"), NOW).label, "done")
        self.assertEqual(eta.estimate(mkplan(state="paused"), NOW).label, "paused")

    def test_remaining_units_scales_current_phase(self):
        p = mkplan("build", local=(True, True, False, False))
        self.assertAlmostEqual(eta.remaining_units(p), 4 * 0.5 + 2 + 1 + 1 + 1 + 1)

    def test_label_format(self):
        self.assertEqual(eta.fmt_range(0.67, 1.5 / 1.0 * 0.6), "40–54 min")
        self.assertEqual(eta.fmt_range(2, 5), "2–5 h")
        self.assertEqual(eta.fmt_range(24, 48), "1–2 d")

    def test_prior_from_finished_plans(self):
        done = mkplan("done")
        ev = [{"t": NOW - 5 * H + i * 600, "plan": "900", "kind": "tick_local", "item": str(i)} for i in range(4)]
        pr = eta.prior_speed([done], ev, {}, NOW)
        self.assertIsNotNone(pr)
        self.assertNotIn("rough", eta.estimate(mkplan(), NOW, [], [], pr).label)
        self.assertIn("rough", eta.estimate(mkplan(), NOW, [], [], None).label)


class RecordTests(unittest.TestCase):
    def test_record_changes(self):
        with tempfile.TemporaryDirectory() as d:
            snap, evp = os.path.join(d, "s.json"), os.path.join(d, "e.jsonl")
            p = mkplan("build", local=(False, False))
            self.assertEqual(events.record_changes([p], NOW, snap, evp), [])  # seed
            self.assertFalse(os.path.exists(evp))
            p.phase = "local-test"
            p.checklist[0].local = True
            out = events.record_changes([p], NOW + 60, snap, evp)
            self.assertEqual({e["kind"] for e in out}, {"phase", "tick_local"})
            self.assertEqual(events.record_changes([p], NOW + 120, snap, evp), [])  # idempotent
            p.checklist[0].local = False
            self.assertEqual([e["kind"] for e in events.record_changes([p], NOW + 180, snap, evp)], ["untick"])
            with open(evp, encoding="utf-8") as f:
                self.assertEqual(len(f.readlines()), 3)
            self.assertEqual(len(events.load_events(evp)), 3)


if __name__ == "__main__":
    unittest.main()
