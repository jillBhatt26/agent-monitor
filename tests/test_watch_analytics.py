import asyncio
import tempfile
import time
import unittest
import unittest.mock

from token_ledger.watch import reader
from tests.test_watch_reader import Base, asst, result, tool, user


def ended(mid, ago, text="all done", **kw):
    d = asst(mid, ago, [{"type": "text", "text": text}], **kw)
    d["message"]["stop_reason"] = "end_turn"
    return d


class AnalyticsTests(Base):
    def setUp(self):
        super().setUp()
        self.write_lines(self.dir + "/" + self.sid + ".jsonl",
                         [user("hi", 500), asst("m1", 400, [{"type": "text", "text": "hello"}],
                                                inp=1000, out=300, cr=98000, cw=700)])
        self.agent("ok", [user("go", 100), asst("a", 90, [tool("t1")]), result("t1", 80), ended("b", 50)],
                   {"agentType": "builder", "finished": True, "description": "Build it"})
        self.agent("bad", [user("go", 100), asst("a", 90, [tool("t1")]), result("t1", 80, err=True, text="boom")],
                   {"agentType": "test-runner", "finished": True, "description": "Run tests"})
        self.agent("run", [user("go", 30), asst("a", 10, [tool("t1")])], {"agentType": "builder", "finished": False})
        self.agent("dis", [user("go", 30), asst("a", 10, [tool("t1")])], {"agentType": "git-scout", "finished": False})

    def test_outcomes(self):
        snap = reader.scan(self.dir, dismissed={"dis"})
        o = {a.agent_id: a.outcome for a in snap.agents}
        self.assertEqual(o, {"ok": "success", "bad": "failed", "run": "running", "dis": "stopped"})

    def test_finished_without_final_answer_is_failed(self):
        self.agent("cut", [user("go", 100), asst("a", 90, [tool("t1")])], {"agentType": "builder", "finished": True})
        self.assertEqual(self.get("cut").outcome, "failed")

    def test_aggregates(self):
        an = reader.analytics(reader.scan(self.dir, dismissed={"dis"}))
        self.assertEqual(an["n"], 4)
        self.assertEqual(an["counts"], {"success": 1, "failed": 1, "running": 1, "stopped": 1, "unknown": 0})
        self.assertEqual(an["rate"], 50.0)
        self.assertEqual(an["cmds"], 4)
        self.assertEqual(an["by_type"]["builder"]["n"], 2)
        self.assertEqual(an["by_type"]["builder"]["ok"], 1)
        self.assertIn("Sonnet", an["by_model"])

    def test_main_output_share(self):
        m = reader.scan(self.dir).mains[0]
        out, pct = reader.output_share(m.tokens)
        self.assertEqual(out, 300)
        self.assertAlmostEqual(pct, 300 / 100000 * 100, places=3)


class AppTests(Base):
    def test_main_line_and_analytics_screen(self):
        try:
            from token_ledger.watch.app import Analytics, WatchApp
        except ImportError:
            self.skipTest("textual not installed")
        self.write_lines(self.dir + "/" + self.sid + ".jsonl",
                         [user("hi", 500), asst("m1", 400, [{"type": "text", "text": "hello"}],
                                                inp=1000, out=300, cr=98000, cw=700)])
        self.agent("ok", [user("go", 100), ended("b", 50)], {"agentType": "builder", "finished": True})

        reader._ledger.update(val=(308000, 0.9), at=time.time(), busy=False)

        async def go():
            app = WatchApp(project_dir=self.dir, request_dir=tempfile.mkdtemp(), dismiss_file=self.dir + "/d.json")
            async with app.run_test() as pilot:
                await pilot.pause(0.5)
                main = str(app.query_one("#main").render())
                await pilot.press("2")  # Plans is the default screen; "a" opens Analytics from All agents
                await pilot.pause(0.2)
                await pilot.press("a")
                await pilot.pause(0.5)
                shown = isinstance(app.screen, Analytics)
                rows = app.screen.query_one("#an-agents").row_count
                await pilot.press("escape")
                await pilot.pause(0.2)
                return main, shown, rows, isinstance(app.screen, Analytics)

        main, shown, rows, after = asyncio.run(go())
        self.assertIn("Output (incl. thinking)   308k   0.9%", main)
        self.assertNotIn(" out ", main)
        self.assertIn("agents 1 · ✓1 ✗0 ⟳0", main)
        self.assertTrue(shown)
        self.assertEqual(rows, 1)
        self.assertFalse(after)

    def test_ledger_today_matches_report_row(self):
        import argparse
        import contextlib
        import io
        from token_ledger import config, report, store
        cfg = config.load()
        conn = store.connect(cfg)
        flt = report.Flt(argparse.Namespace(all=False), conn)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            report.cmd_period(conn, cfg, flt, "today")
        row = [ln for ln in buf.getvalue().splitlines() if ln.startswith("Output (incl. thinking)")][0].split()
        conn.close()
        with unittest.mock.patch("token_ledger.ingest.run"):
            out, pct = reader._ledger_today()
        self.assertEqual(row[-2:], [report.fmt_tok(out), f"{pct:.1f}%"])


if __name__ == "__main__":
    unittest.main()
