import os
import sys
import time
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
from token_ledger.watch import actions, reader  # noqa: E402
from tests.test_watch_reader import Base, asst, result, tool, user  # noqa: E402

NOW = 1_800_000_000.0


def ag(age=5, state="live", **kw):
    d = dict(state=state, last_activity=NOW - age, started=NOW - 800, ended=None, elapsed_s=785,
             finish_source="", pending_action="", recent_actions=[], final_text="")
    d.update(kw)
    return types.SimpleNamespace(**d)


class Classify(unittest.TestCase):
    def kind(self, a):
        return actions.status_info(a, NOW)[0]

    def test_boundaries(self):
        self.assertEqual(self.kind(ag(59)), "working")
        self.assertEqual(self.kind(ag(61)), "waiting")
        self.assertEqual(self.kind(ag(180)), "waiting")
        self.assertEqual(self.kind(ag(181)), "stalled")
        self.assertEqual(actions.status_info(ag(59), NOW)[1], "green")
        self.assertEqual(actions.status_info(ag(100), NOW)[1], "yellow")
        self.assertEqual(actions.status_info(ag(240), NOW)[1], "red")

    def test_text(self):
        self.assertEqual(actions.status_info(ag(3), NOW)[2], "● WORKING · active 3s ago")
        self.assertEqual(actions.status_info(ag(100), NOW)[2], "◐ WAITING · quiet 1m40s")
        self.assertEqual(actions.status_info(ag(240), NOW)[2], "⚠ STALLED · quiet 4m")

    def test_pending_tool(self):
        t = actions.status_info(ag(10, pending_action="running command: pytest"), NOW)[2]
        self.assertEqual(t, "● WORKING · active 10s ago · running: running command: pytest")

    def test_finished(self):
        k, c, t = actions.status_info(ag(900, "done", ended=NOW - 900, finish_source="ended turn"), NOW)
        self.assertEqual((k, c), ("finished", "grey50"))
        self.assertIn("✓ FINISHED · ", t)
        self.assertIn("took 13m05s", t)
        self.assertTrue(t.endswith("ended turn"))

    def test_fmt_age_and_pulse(self):
        self.assertEqual(actions.fmt_age(3725), "1h02m")
        self.assertEqual(actions.pulse_frame(NOW, False), actions.PULSE_IDLE)
        self.assertIn(actions.pulse_frame(NOW, True), actions.PULSE)
        self.assertNotEqual(actions.pulse_frame(NOW, True), actions.pulse_frame(NOW + 0.13, True))

    def test_block_working_and_finished(self):
        a = ag(5, recent_actions=[(NOW - 20, "reading a.py"), (NOW - 5, "editing b.py")], pending_action="editing b.py")
        b = "\n".join(actions.status_block(a, NOW))
        self.assertIn("WORKING", b)
        self.assertIn("reading a.py", b)
        self.assertIn("current tool call (no result yet): editing b.py", b)
        self.assertNotIn("final result", b)
        f = ag(900, "done", ended=NOW - 900, finish_source="meta finished", final_text="All done.\nTests green.")
        b = "\n".join(actions.status_block(f, NOW))
        self.assertIn("how we know: meta finished", b)
        self.assertIn("final result:\n  All done.\n  Tests green.", b)
        self.assertNotIn("current tool call", b)


class FromTranscript(Base):
    def test_sources_and_pending(self):
        self.agent("m", [user("go", 900), asst("a", 20, [tool("t1")])], {"agentType": "builder", "finished": True})
        self.assertEqual(self.get("m").finish_source, "meta finished")
        self.agent("e", [user("go", 90), asst("a", 20, [{"type": "text", "text": "Done.\n\nSecond line\nthird"}]).copy()],
                   {"agentType": "builder"})
        self.assertFalse(self.get("e").finished)  # no stop_reason: not an ended turn
        ended = asst("b", 20, [{"type": "text", "text": "Done.\n\nSecond line\nthird"}])
        ended["message"]["stop_reason"] = "end_turn"
        self.agent("t", [user("go", 90), ended], {"agentType": "builder"})
        a = self.get("t")
        self.assertEqual((a.finish_source, a.final_text), ("ended turn", "Done.\nSecond line"))
        self.agent("q", [user("go", 4000), asst("a", 3900, [tool("t1")])], {"agentType": "builder"})
        self.assertEqual(self.get("q").finish_source, "done? (quiet >60 min)")
        self.agent("d", [user("go", 30), asst("a", 10, [tool("t1")])], {"agentType": "builder"})
        a = reader.scan(self.dir, dismissed={"d"}).agents
        self.assertEqual(next(x for x in a if x.agent_id == "d").finish_source, "dismissed")
        self.agent("p", [user("go", 30), asst("a", 10, [tool("t1", command="pytest -q")])], {"agentType": "builder", "finished": False})
        p = self.get("p")
        self.assertEqual(p.pending_action, "running command: pytest -q")
        self.assertEqual(len(p.recent_actions), 1)
        self.agent("r", [user("go", 30), asst("a", 10, [tool("t1")]), result("t1", 5)], {"agentType": "builder", "finished": False})
        r = self.get("r")
        self.assertEqual(r.pending_action, "")
        self.assertEqual(r.finish_source, "")


if __name__ == "__main__":
    unittest.main()
