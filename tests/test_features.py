"""Cross-platform helpers, context budgets, burn rate, request delivery and the `agents` snapshot."""
import contextlib
import io
import json
import os
import tempfile
import unittest

from tests.test_watch_reader import Base, asst, tool, user
from token_ledger import cli, compat, config
from token_ledger.classify import Rules
from token_ledger.sources import claude_code
from token_ledger.watch import actions, reader


class CompatTests(unittest.TestCase):
    def test_posix_and_under(self):
        self.assertEqual(compat.posix("a\\b\\c.md"), "a/b/c.md")
        root = os.path.abspath(os.path.join("x", "proj"))
        self.assertTrue(compat.same_or_under(os.path.join(root, "site", "a.ts"), root))
        self.assertTrue(compat.same_or_under(root, root))
        self.assertFalse(compat.same_or_under(root + "-old", root))
        if os.name == "nt":
            self.assertTrue(compat.same_or_under(root.upper() + "\\site", root.lower()))

    def test_slug_matches_claude_code(self):
        p = os.path.abspath(os.path.join("My Work", "site.v2"))
        self.assertNotRegex(compat.slug(p), r"[^A-Za-z0-9-]")
        self.assertTrue(compat.slug(p).endswith("-My-Work-site-v2"))


class ClassifyPathTests(unittest.TestCase):
    def test_windows_style_paths_match_posix_rules(self):
        root = os.path.abspath("proj")
        r = Rules(root, {"kind": [{"path_prefix": "site/", "kind": "Implementation"},
                                  {"path_prefix": "wiki/plans/", "kind": "Planning"}],
                         "feature": [{"path_regex": "wiki/plans/(\\d{3})-([a-z-]+)\\.md", "label": "{1} {2}",
                                      "dashes_to_spaces": True}]})
        self.assertEqual(r.kind_for_path(os.path.join(root, "wiki", "plans", "005-x.md")), "Planning")
        self.assertEqual(r.kind_for_path(os.path.join(root, "site", "app", "page.tsx")), "Implementation")
        self.assertEqual(r.feature_for_path("E:\\w\\wiki\\plans\\005-agent-setup.md"), "005 agent setup")

    def test_project_label(self):
        home = compat.slug(os.path.expanduser("~"))
        self.assertEqual(claude_code.project_label(home), "home")
        self.assertEqual(claude_code.project_label(home + "-code-app"), "code-app")
        self.assertEqual(claude_code.project_label("D--Work-my-app"), "Work-my-app")
        self.assertEqual(claude_code.project_label("-srv-app"), "srv-app")

    def test_windows_path_signals(self):
        sig = claude_code._signals("Bash", {"command": "type C:\\proj\\wiki\\plans\\005-x.md && ls src/a"})
        self.assertIn("ref:C:\\proj\\wiki\\plans\\005-x.md", sig)
        self.assertIn("ref:src/a", sig)
        self.assertEqual(claude_code._signals("Bash", {"command": "npm run test"})[0], "cmd:test")
        self.assertEqual(claude_code._signals("Bash", {"command": "vercel inspect x"})[0], "cmd:ops")


class ConfigTests(unittest.TestCase):
    def test_timezone_fallback_never_crashes(self):
        tz, label = config.resolve_tz("No/Such_Zone")
        self.assertIsNotNone(tz)
        self.assertIn("local", label)
        self.assertIsNotNone(config.resolve_tz(None)[0])

    def test_watch_defaults_and_overrides(self):
        c = config.Config({"watch": {"main_compact_tokens": 150000, "agent_budget_tokens": {"haiku": 20000}}},
                          data_dir=tempfile.mkdtemp())
        self.assertEqual(c.watch["main_compact_tokens"], 150000)
        self.assertEqual(c.watch["main_save_tokens"], 120000)
        self.assertEqual(c.watch["agent_budget_tokens"]["haiku"], 20000)
        self.assertEqual(c.watch["agent_budget_tokens"]["sonnet"], 60000)
        self.assertTrue(c.watch["request_dir"].startswith(c.data_dir))


class GaugeTests(unittest.TestCase):
    def test_ctx_level_and_bar(self):
        self.assertEqual(actions.ctx_level(50000), ("green", "ok"))
        self.assertEqual(actions.ctx_level(130000), ("yellow", "checkpoint"))
        self.assertEqual(actions.ctx_level(170000), ("red", "COMPACT NOW"))
        self.assertTrue(actions.ctx_bar(80000, 160000, 10).startswith("█████░░░░░"))
        self.assertTrue(actions.ctx_bar(999999, 160000, 4).endswith("625%"))
        self.assertEqual(actions.fmt_k(61234), "61k")

    def test_budget_for(self):
        b = {"opus": 80000, "sonnet": 60000, "haiku": 30000, "default": 50000}
        self.assertEqual(reader.budget_for("claude-haiku-5-5", b), 30000)
        self.assertEqual(reader.budget_for("claude-opus-5-5", b), 80000)
        self.assertEqual(reader.budget_for("mystery", b), 50000)
        self.assertIsNone(reader.budget_for("claude-haiku-5-5", None))


class BudgetScanTests(Base):
    def test_peak_context_over_budget_and_burn(self):
        self.agent("big", [user("go", 30), asst("a", 20, [tool("t1")], cr=40000, cw=500),
                           asst("b", 10, [tool("t2")], cr=20000, cw=100)],
                   {"agentType": "test-runner", "finished": False})
        snap = reader.scan(self.dir, budgets={"haiku": 30000, "sonnet": 30000, "default": 30000}, burn_minutes=10)
        a = next(x for x in snap.agents if x.agent_id == "big")
        self.assertEqual(a.peak_context, 10 + 40000 + 500)
        self.assertEqual(a.context_tokens, 10 + 20000 + 100)
        self.assertTrue(a.over_budget)
        self.assertEqual(a.budget, 30000)
        self.assertEqual(snap.totals["agents_over_budget"], 1)
        self.assertGreater(snap.totals["burn_tokens"], 60000)
        self.assertAlmostEqual(snap.totals["burn_per_min"], snap.totals["burn_tokens"] / 10)
        self.assertIn("over its", " ".join(actions.hints(a, 0)))

    def test_no_budgets_means_no_flag(self):
        self.agent("x", [user("go", 30), asst("a", 20, [tool("t1")], cr=900000)], {"agentType": "builder"})
        a = next(x for x in reader.scan(self.dir).agents if x.agent_id == "x")
        self.assertFalse(a.over_budget)
        self.assertIsNone(a.budget)


class RequestDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cfg = config.Config({"watch": {"request_dir": self.d}}, data_dir=tempfile.mkdtemp())

    def write(self, aid, sid, action):
        a = type("A", (), {"agent_id": aid, "session_id": sid, "agent_type": "builder", "model": "m",
                           "description": "Build it", "transcript_path": "/t/agent.jsonl"})()
        return actions.write_request(a, action, self.d)

    def test_delivers_once_for_its_session_only(self):
        self.write("a1", "s1", "stop")
        self.write("a2", "s2", "restart")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli.deliver_requests(self.cfg, "s1")
        out = json.loads(buf.getvalue())["hookSpecificOutput"]
        self.assertEqual(out["hookEventName"], "UserPromptSubmit")
        self.assertIn("STOP builder agent a1", out["additionalContext"])
        self.assertNotIn("a2", out["additionalContext"])
        self.assertEqual(actions.read_request_status("a1", self.d), ("stop", "delivered"))
        self.assertEqual(actions.read_request_status("a2", self.d), ("restart", "pending"))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli.deliver_requests(self.cfg, "s1")
        self.assertEqual(buf.getvalue(), "")

    def test_restart_text_points_at_brief(self):
        self.write("a3", "s3", "restart")
        txt = actions.request_context(actions.pending_requests("s3", self.d))
        self.assertIn("RESTART", txt)
        self.assertIn("/t/agent.jsonl", txt)


class AgentsCommandTests(Base):
    def test_json_snapshot(self):
        self.agent("j1", [user("go", 20), asst("a", 10, [tool("t1")])], {"agentType": "builder", "description": "Do it"})
        os.environ["TOKEN_LEDGER_CONFIG_DIR"] = tempfile.mkdtemp()
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = cli.main(["--data-dir", tempfile.mkdtemp(), "agents", "--project-dir", self.dir, "--json"])
        finally:
            os.environ.pop("TOKEN_LEDGER_CONFIG_DIR", None)
        self.assertEqual(rc, 0)
        d = json.loads(buf.getvalue())
        self.assertEqual([a["agent_id"] for a in d["agents"]], ["j1"])
        self.assertNotIn("brief", d["agents"][0])
        self.assertIn("burn_per_min", d["totals"])


if __name__ == "__main__":
    unittest.main()
