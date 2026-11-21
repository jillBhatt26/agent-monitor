import datetime as dt
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
from token_ledger.watch import plans_view  # noqa: E402


def ag(aid, state, last=None):
    return types.SimpleNamespace(agent_id=aid, state=state, elapsed_s=5,
                                 last_activity=last or dt.datetime.now(dt.timezone.utc))


def plan(pid):
    return types.SimpleNamespace(plan=pid, title="T " + pid, phase="build", state="active", local_pct=1, stage_pct=None,
                                 next="n", guessed=False, waiting_on="", updated=None, eta=None)


class T(unittest.TestCase):
    def test_counts_live_and_finished_today(self):
        old = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=3)
        snap = types.SimpleNamespace(agents=[ag("a1", "live"), ag("agent-a2", "done"), ag("a3", "done", old)], mains=[])
        tasks = {"a1": {"plan": "047"}, "a2": {"plan": "047"}, "a3": {"plan": "047"}}
        live, done = plans_view.plan_agents(snap, tasks, "047")
        self.assertEqual((len(live), len(done)), (1, 1))
        rows = dict(plans_view.group_rows(snap, tasks, [plan("047")]))
        self.assertEqual(rows["In execution"][0]["live"], 2)

    def test_budget_parse(self):
        p = plans_view.parse_budget_min
        self.assertEqual(p("Board: x\n\nBudget: max ~15 min / ~30 tool calls"), 15)
        self.assertEqual(p("do it, max ~10 min"), 10)
        self.assertIsNone(p("no limit here"))
        self.assertEqual(plans_view.agent_budget_min(ag("a1", "live"), {"a1": {"budget_min": 7}}), 7)

    def _live(self, aid, mins_ago, brief=""):
        now = dt.datetime.now(dt.timezone.utc)
        return types.SimpleNamespace(agent_id=aid, state="live", elapsed_s=mins_ago * 60, brief=brief,
                                     started=now - dt.timedelta(minutes=mins_ago), last_activity=now)

    def test_remaining_overrun_fallback_max(self):
        now = dt.datetime.now(dt.timezone.utc).timestamp()
        a = self._live("a", 9, "Budget: max ~15 min")
        self.assertAlmostEqual(plans_view.agent_remaining_s(a, {}, [], now) / 60, 6, delta=0.1)
        self.assertEqual(plans_view.plan_live_eta([a], [], {}, now), "6m")
        over = self._live("o", 18, "Budget: max ~15 min")
        self.assertEqual(plans_view.plan_live_eta([over], [], {}, now), "+3m over")
        nb = self._live("n", 4)
        self.assertEqual(plans_view.plan_live_eta([nb], [], {}, now), "6m")
        d = [types.SimpleNamespace(elapsed_s=s) for s in (1200, 1800, 3000)]
        self.assertEqual(plans_view.plan_live_eta([nb], d, {}, now), "26m")
        self.assertEqual(plans_view.plan_live_eta([a, nb], d, {}, now), "26m")
        big = self._live("b", 0, "Budget: max ~65 min")
        self.assertEqual(plans_view.plan_live_eta([big], [], {}, now), "1h 05m")
        self.assertEqual(plans_view.plan_live_eta([], d, {}, now), "–")

    def test_empty_groups_and_unsorted_hidden(self):
        snap = types.SimpleNamespace(agents=[], mains=[types.SimpleNamespace(context_tokens=1)])
        groups = [g for g, _ in plans_view.group_rows(snap, {}, [plan("047")])]
        self.assertEqual(groups, ["In execution"])

    def test_card_lines(self):
        r = {"plan": "047", "title": "catalog_management review of a very long title", "phase": "build", "local": "40%",
             "stage": "–", "live": 3, "live_now": 1, "eta": "10–18 h rough", "agent_eta": "6m"}
        l = plans_view.card_lines(r)
        self.assertEqual(l[0], "● 047 catalog_management review of a very long title")
        self.assertEqual(l[1], "build · Local 40%")
        self.assertEqual(l[2], "active 1 · ETA 6m")
        self.assertEqual(len(plans_view.card_lines(r, 20)[0]), 20)
        r.update(live_now=0, stage="100%", eta="–", agent_eta="–")
        l = plans_view.card_lines(r)
        self.assertEqual((l[0][:3], l[1], l[2]), ("047", "build · Local 40% · Stage 100%", "active 0 · ETA –"))

    def test_column_order_and_hidden_empty(self):
        ps = [plan("1"), plan("2")]
        ps[1].phase, ps[1].state = "done", "done"
        snap = types.SimpleNamespace(agents=[], mains=[])
        groups = [g for g, _ in plans_view.group_rows(snap, {}, ps)]
        order = [g for g in plans_view.GROUPS if g in groups]
        self.assertEqual(groups, order)
        self.assertNotIn("Paused", groups)

    def test_keep_empty_gives_four_columns(self):
        snap = types.SimpleNamespace(agents=[], mains=[])
        groups = [g for g, _ in plans_view.group_rows(snap, {}, [plan("047")], keep_empty=True)]
        self.assertEqual(groups[:4], ["Planned", "In execution", "Paused", "Done"])
        self.assertNotIn("Unsorted", groups)

    def test_waiting_owner_goes_to_paused(self):
        from token_ledger.board import plans as board_plans
        p = board_plans.Plan(plan="1", phase="build", state="waiting-owner")
        self.assertEqual(board_plans.phase_column(p), "Paused")
        p = board_plans.Plan(plan="1", phase="build", state="active")
        self.assertEqual(board_plans.phase_column(p), "In execution")


if __name__ == "__main__":
    unittest.main()
