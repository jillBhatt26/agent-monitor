import asyncio
import io
import os
import sys
import tempfile
import types
import unittest
import unittest.mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))

try:
    from token_ledger.watch import app as appmod
except ImportError:  # textual missing
    appmod = None


def plain(renderable, width=140):
    from rich.console import Console
    c = Console(width=width, record=True, file=io.StringIO())
    c.print(renderable)
    return c.export_text()


def fake_monitor(red):
    d = {"built": True, "generated": "2026-11-13T10:00:00", "since_days": 7, "alerts": red,
         "index": {"db_bytes": 1048576, "pct_cap": 10, "integrity": "ok", "stale": 0, "skipped": {},
                   "roots": {"wa": {"files": 3, "chunks": 9}}, "last_run": None},
         "search": {"count": 2, "tokens_avg": 5, "tokens_max": 9, "cursor_cuts": 0, "ceiling_cuts": 0,
                    "top_queries": [("needle", 2)], "per_day": {}},
         "misses": {"count": 0, "zero_hit": 0, "unused_hit": 0, "wording_suspect": 0, "by_category": {}, "list": []},
         "guard": None, "restore": None, "growth": {"big_files": []}}
    return types.SimpleNamespace(
        collect=lambda **kw: d, alerts=lambda data: list(red), text_summary=lambda data: "ctx monitor OVERVIEW-MARK",
        week_report=lambda data: "# week", save_week=lambda text, date: "saved-week.md",
        mb=lambda b: "1.0 MB", RESTORE_LIMIT=8192)


class AppCase(unittest.TestCase):
    def run_app(self, mon, steps, size=(80, 24)):
        patch = unittest.mock.patch.object(appmod, "ctx_monitor", lambda: mon)
        patch.start()
        self.addCleanup(patch.stop)

        async def go():
            d = tempfile.mkdtemp()
            app = appmod.WatchApp(project_dir=d, request_dir=d, dismiss_file=d + "/d.json")
            async with app.run_test(size=size) as pilot:
                await pilot.pause(0.3)
                return await steps(app, pilot)

        return asyncio.run(go())


@unittest.skipIf(appmod is None, "textual not installed")
class ContextScreenTests(AppCase):
    def test_open_switch_back(self):
        async def steps(app, pilot):
            await pilot.press("3")
            await pilot.pause(0.6)
            opened = isinstance(app.screen, appmod.ContextScreen)
            overview = plain(app.screen.query_one("#ctx-body").content) + "".join(
                plain(c.content) for c in app.screen.query(appmod.CtxCard))
            await pilot.press("i")
            await pilot.pause(0.2)
            index = str(app.screen.query_one("#ctx-body").render())
            await pilot.press("escape")
            await pilot.pause(0.2)
            return opened, overview, index, isinstance(app.screen, appmod.ContextScreen)

        opened, overview, index, after = self.run_app(fake_monitor([]), steps)
        self.assertTrue(opened)
        self.assertIn("Index by root", overview)
        self.assertIn("ALL OK", overview)
        self.assertIn("MAIN context", overview)
        self.assertIn("root wa", index)
        self.assertFalse(after)

    def test_week_save(self):
        async def steps(app, pilot):
            await pilot.press("3")
            await pilot.pause(0.6)
            await pilot.press("w")
            await pilot.press("e")
            await pilot.pause(0.2)
            return str(app.screen.query_one("#ctx-body").render())

        self.assertIn("saved: saved-week.md", self.run_app(fake_monitor([]), steps))

    def test_missing_monitor_note(self):
        async def steps(app, pilot):
            await pilot.press("3")
            await pilot.pause(0.5)
            note = str(app.screen.query_one("#ctx-body").render())
            await pilot.press("escape")
            await pilot.pause(0.2)
            return note, isinstance(app.screen, appmod.ContextScreen)

        note, still = self.run_app(None, steps)
        self.assertIn("Context monitor not built", note)
        self.assertFalse(still)

    def test_badge_on_red_only(self):
        async def steps(app, pilot):
            await pilot.pause(0.6)
            return app.has_class("ctx-red"), app.sub_title

        red, sub = self.run_app(fake_monitor(["hook errors: 2"]), steps)
        self.assertTrue(red)
        self.assertIn("CTX alert", sub)
        red, sub = self.run_app(fake_monitor([]), steps)
        self.assertFalse(red)
        self.assertEqual(sub, "")

    def test_overview_cards_and_alert_text(self):
        d = fake_monitor(["hook errors: 2"]).collect()
        d["guard"] = {"allowed": 9, "denied": 1, "hook_errors": 2, "hook_ms_p50": 5, "hook_ms_p95": 9, "denied_files": [],
                      "per_day": {}}
        d["restore"] = {"last_bytes": 6000, "max_bytes": 6000, "series": [5000, 6000]}
        d["secrets"] = {"total": 3, "today": 1, "by_kind": {"password": 3}}
        main = types.SimpleNamespace(context_tokens=130000, context_history=[50000, 90000, 130000])
        for w in (120, 160):
            out = plain(appmod.ctx_board.overview(d, [main], ["hook errors: 2"], w), w)
            for word in ("MAIN context", "130k / 160k", "ALERT", "Guard", "Restore", "Secrets", "Growth", "Index by root", "120k auto-save"):
                self.assertIn(word, out)
            self.assertTrue(all(len(l) <= w for l in out.splitlines()))

    def test_overview_explanations_and_focus(self):
        d = fake_monitor([]).collect()
        d["index"]["last_run"] = {"ts": "t", "ms": 564, "peak_rss_mb": 80, "age_h": 0.7}
        out = plain(appmod.ctx_board.overview(d, [], [], 120), 120)
        for key in ("index", "last", "search", "root"):
            self.assertIn(appmod.ctx_board.EXPLAIN_SHORT[key].split(";")[0][:30], out)
        self.assertIn("42 min", out)
        self.assertIn("ago", out)
        self.assertIn("564 ms", out)

        async def steps(app, pilot):
            await pilot.press("3")
            await pilot.pause(0.6)
            ex = lambda: plain(app.screen.query_one("#ctx-explain").content)
            first = ex()
            await pilot.press("tab")
            await pilot.pause(0.2)
            second = ex()
            await pilot.press("right")
            await pilot.pause(0.2)
            return first, second, ex(), app.screen.focused.key

        first, second, third, key = self.run_app(fake_monitor([]), steps)
        self.assertIn("Move with Tab or hover", first)
        self.assertIn("MAIN context", second)
        self.assertIn("Index", third)
        self.assertEqual(key, "index")

    def test_main_card_has_no_gap_above_grid(self):
        async def steps(app, pilot):
            main_view = types.SimpleNamespace(context_tokens=90_000, context_history=[40_000, 60_000, 90_000])
            app.snap = types.SimpleNamespace(mains=[main_view])
            await pilot.press("3")
            await pilot.pause(0.6)
            main = app.screen.query_one("#card-main").region
            first = app.screen.query_one("#card-index").region
            return main.bottom, first.y

        bottom, top = self.run_app(fake_monitor([]), steps, size=(190, 50))
        self.assertEqual(bottom, top)

    def test_badge_survives_broken_monitor(self):
        def boom(**kw):
            raise RuntimeError("db gone")

        mon = fake_monitor([])
        mon.collect = boom

        async def steps(app, pilot):
            await pilot.pause(0.5)
            return app.has_class("ctx-red")

        self.assertFalse(self.run_app(mon, steps))


@unittest.skipIf(appmod is None, "textual not installed")
class PlansScreenTests(AppCase):
    def test_plans_is_default_and_keys_switch_screens(self):
        async def steps(app, pilot):
            first = isinstance(app.screen, appmod.PlansScreen)
            await pilot.press("2")
            await pilot.pause(0.2)
            all_agents = len(app.screen_stack) == 1
            await pilot.press("1")
            await pilot.pause(0.2)
            back = isinstance(app.screen, appmod.PlansScreen)
            await pilot.press("3")
            await pilot.pause(0.3)
            ctx = isinstance(app.screen, appmod.ContextScreen)
            await pilot.press("1")
            await pilot.pause(0.2)
            return first, all_agents, back, ctx, isinstance(app.screen, appmod.PlansScreen)

        self.assertEqual(self.run_app(fake_monitor([]), steps), (True, True, True, True, True))

    def test_plans_screen_survives_empty_snapshot(self):
        async def steps(app, pilot):
            await pilot.pause(0.5)
            return app.screen.query_one("#pmain").content, app.screen.query_one("#board") is not None

        text, has_board = self.run_app(fake_monitor([]), steps)
        self.assertTrue(has_board)
        self.assertTrue(str(text))


if __name__ == "__main__":
    unittest.main()
