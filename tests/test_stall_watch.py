import importlib.util
import os
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
from tests.test_watch_reader import Base, asst, result, tool, user  # noqa: E402

# Tests for an optional companion hook (agent-stall-watch.py) that lives in a project workspace, not in this repo.
# Point AGENT_STALL_WATCH at it to run them; otherwise they are skipped.
SCRIPT = os.environ.get("AGENT_STALL_WATCH", "")
sw = None
if SCRIPT and os.path.exists(SCRIPT):
    spec = importlib.util.spec_from_file_location("agent_stall_watch", SCRIPT)
    sw = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sw)

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)


def ag(aid="a1", idle_min=1, pending="", finished=False, session="s1"):
    return types.SimpleNamespace(
        agent_id=aid, session_id=session, finished=finished, pending_action=pending,
        last_activity=NOW - timedelta(minutes=idle_min), description="Do thing", agent_type="builder",
        model="claude-sonnet-5-5", transcript_path="/nonexistent/agent-x.jsonl")


def stalls(agents, reported=None, dismissed=()):
    return sw.find_stalls(agents, "s1", NOW, 5, 20, reported or {}, dismissed)


@unittest.skipUnless(sw, "set AGENT_STALL_WATCH to the companion hook script to run these")
class FindStalls(unittest.TestCase):
    def test_idle_stalled(self):
        s, n = stalls([ag(idle_min=6)])
        self.assertEqual(len(s), 1)
        self.assertEqual(n, 1)

    def test_recent_not_stalled(self):
        self.assertEqual(stalls([ag(idle_min=4)])[0], [])

    def test_pending_tool_short_is_ok(self):
        self.assertEqual(stalls([ag(idle_min=15, pending="running command: x")])[0], [])

    def test_pending_tool_long_stalls(self):
        s, _ = stalls([ag(idle_min=21, pending="running command: x")])
        self.assertEqual(len(s), 1)
        self.assertIn('pending="running command: x"', sw.format_stall(s[0], NOW))

    def test_finished_ignored(self):
        self.assertEqual(stalls([ag(idle_min=30, finished=True)]), ([], 0))

    def test_dismissed_ignored(self):
        self.assertEqual(stalls([ag(idle_min=30)], dismissed={"a1"}), ([], 0))

    def test_other_session_ignored(self):
        self.assertEqual(stalls([ag(idle_min=30, session="s2")]), ([], 0))

    def test_already_reported_same_activity(self):
        a = ag(idle_min=30)
        self.assertEqual(stalls([a], reported={"a1": a.last_activity.isoformat()})[0], [])

    def test_reported_but_new_activity_stalls_again(self):
        a = ag(idle_min=30)
        old = (a.last_activity - timedelta(minutes=10)).isoformat()
        self.assertEqual(len(stalls([a], reported={"a1": old})[0]), 1)

    def test_line_format(self):
        line = sw.format_stall(ag(idle_min=7), NOW)
        self.assertEqual(line, 'STALL agent=a1 desc="Do thing" idle=7m pending="none" type=builder model=claude-sonnet-5-5')


@unittest.skipUnless(sw, "set AGENT_STALL_WATCH to the companion hook script to run these")
class Lock(unittest.TestCase):
    def test_pid_lock(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(sw.watcher_running("s1", d))
            with open(sw.state_paths("s1", d)[1], "w") as f:
                f.write("0")
            self.assertFalse(sw.watcher_running("s1", d))
            with open(sw.state_paths("s1", d)[1], "w") as f:
                f.write("99999999")
            self.assertFalse(sw.watcher_running("s1", d))


@unittest.skipUnless(sw, "set AGENT_STALL_WATCH to the companion hook script to run these")
class EndToEnd(Base):
    def run_watch(self, state_dir, **kw):
        out = []
        code = sw.run(self.sid, self.dir, kw.get("idle", 5), 20, 0.01, state_dir=state_dir, out=out.append)
        return code, out

    def test_stalled_agent_reported_once(self):
        self.agent("a1", [user("brief", 900), asst("m1", 700, [{"type": "text", "text": "working"}])],
                   meta={"agentType": "builder", "description": "Build x", "model": "sonnet", "finished": False})
        with tempfile.TemporaryDirectory() as d:
            code, out = self.run_watch(d)
            self.assertEqual(code, 0)
            self.assertEqual(len(out), 1)
            self.assertIn("STALL agent=a1", out[0])
            self.assertIn("model=sonnet", out[0])
            # Restart: same stall is not reported again; no unfinished-with-new-activity -> ALL-DONE path is not hit,
            # so cut it short with a tiny max runtime.
            out2 = []
            sw.run(self.sid, self.dir, 5, 20, 0.01, state_dir=d, max_runtime=0.05, out=out2.append)
            self.assertEqual(out2, ["TIMEOUT"])

    def test_finished_agent_gives_all_done(self):
        self.agent("a2", [user("brief", 900), asst("m1", 700, [{"type": "text", "text": "ok"}])],
                   meta={"agentType": "builder", "description": "Build y", "finished": True})
        with tempfile.TemporaryDirectory() as d:
            code, out = self.run_watch(d)
            self.assertEqual(out, ["ALL-DONE"])


if __name__ == "__main__":
    unittest.main()
