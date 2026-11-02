import asyncio
import json
import os
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
from token_ledger.watch import actions  # noqa: E402


def agent(tp=""):
    return types.SimpleNamespace(agent_id="abc123", session_id="s1", agent_type="builder", model="claude-sonnet-5-5",
                                 description="d", transcript_path=tp, state="stalled", last_activity=1000,
                                 repeat_hint="Bash x3", last_error="boom")


class T(unittest.TestCase):
    def test_colours(self):
        self.assertEqual(actions.timer_colour(299), "green")
        self.assertEqual(actions.timer_colour(300), "yellow")
        self.assertEqual(actions.timer_colour(601), "red")
        self.assertEqual(actions.timer_colour(900, done=True), "grey50")
        self.assertEqual(actions.fmt_dur(125), "02:05")
        self.assertEqual(actions.fmt_dur(3725), "1:02:05")

    def test_request_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = actions.write_request(agent("/t.jsonl"), "restart", os.path.join(d, "sub"))
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
            self.assertEqual(data["status"], "pending")
            self.assertEqual(data["transcript_path"], "/t.jsonl")
            self.assertNotIn("brief", data)
            self.assertEqual(actions.read_request_status("abc123", os.path.join(d, "sub")), ("restart", "pending"))
            self.assertIsNone(actions.read_request_status("nope", d))
            with self.assertRaises(ValueError):
                actions.write_request(agent(), "kill", d)

    def test_command_and_brief(self):
        with tempfile.TemporaryDirectory() as d:
            tp = os.path.join(d, "t.jsonl")
            with open(tp, "w", encoding="utf-8") as f:
                f.write(json.dumps({"type": "user", "message": {"content": [{"type": "text", "text": "do it"}]}}) + "\n")
            self.assertEqual(actions.extract_brief(tp), "do it")
            ok, cmd = actions.run_separately(agent(tp), d, tmp_dir=os.path.join(d, "m"), launch=False)
            self.assertFalse(ok)
            self.assertIn("claude -p --model", cmd)
            self.assertIn("claude-sonnet-5-5", cmd)
            self.assertNotIn("dangerously", cmd)
            with open(os.path.join(d, "m", "abc123.txt"), encoding="utf-8") as f:
                self.assertEqual(f.read(), "do it")
        self.assertEqual(actions.terminal_argv("gnome-terminal", "x")[:2], ["gnome-terminal", "--"])

    def test_hints(self):
        h = actions.hints(agent(), 1000 + 240)
        self.assertIn("waiting on tool for 4m", h[0])
        self.assertEqual(len(h), 4)


class TestDismiss(unittest.TestCase):
    def test_toggle_persists(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "sub", "dismissed.json")
            self.assertEqual(actions.load_dismissed(f), set())
            self.assertTrue(actions.toggle_dismissed("abc", f))
            self.assertEqual(actions.load_dismissed(f), {"abc"})
            self.assertFalse(actions.toggle_dismissed("abc", f))
            self.assertEqual(actions.load_dismissed(f), set())

    def test_stalled_hint_mentions_d(self):
        self.assertIn("press d", " ".join(actions.hints(agent(), 1240)))

    def test_d_key_dismisses_in_app(self):
        try:
            from token_ledger.watch.app import WatchApp
        except ImportError:
            self.skipTest("textual not installed")
        d = tempfile.mkdtemp()
        f = os.path.join(d, "dismissed.json")

        async def go():
            app = WatchApp(project_dir=d, request_dir=d, dismiss_file=f)
            async with app.run_test() as pilot:
                await pilot.pause(0.3)
                app.selected = lambda: agent()
                await pilot.press("d")
                await pilot.pause(0.2)

        asyncio.run(go())
        self.assertEqual(actions.load_dismissed(f), {"abc123"})


class TestApp(unittest.TestCase):
    def test_headless(self):
        try:
            from token_ledger.watch.app import WatchApp
        except ImportError:
            self.skipTest("textual not installed")

        async def go():
            app = WatchApp(request_dir=tempfile.mkdtemp())
            async with app.run_test() as pilot:
                await pilot.pause(2.5)
                await pilot.press("f", "tab", "enter", "escape", "question_mark", "escape")
                await pilot.pause(0.5)
            return app.snap

        snap = asyncio.run(go())
        self.assertIsNotNone(snap)

    def test_requests_need_the_prompt_hook(self):
        try:
            from token_ledger.watch import app as appmod
            from token_ledger.watch.app import Confirm, Notice, WatchApp
        except ImportError:
            self.skipTest("textual not installed")
        import unittest.mock
        proj = tempfile.mkdtemp()
        reqs = tempfile.mkdtemp()

        async def go(installed):
            app = WatchApp(project_dir=proj, request_dir=reqs)
            with unittest.mock.patch.object(appmod, "hooks_installed", lambda: installed):
                async with app.run_test() as pilot:
                    await pilot.pause(0.5)
                    app.selected = lambda: agent()
                    app.action_request("stop")
                    await pilot.pause(0.2)
                    screen = type(app.screen)
                    if installed:
                        await pilot.press("y")
                        await pilot.pause(0.2)
                    return screen

        self.assertIs(asyncio.run(go(False)), Notice)
        self.assertEqual(os.listdir(reqs), [])
        self.assertIs(asyncio.run(go(True)), Confirm)
        self.assertEqual(os.listdir(reqs), ["abc123.json"])


if __name__ == "__main__":
    unittest.main()


class TestPaneTitle(unittest.TestCase):
    def test_title_is_description(self):
        try:
            from token_ledger.watch.app import pane_title
        except ImportError:
            self.skipTest("textual not installed")
        from types import SimpleNamespace as N
        a = N(description="Local browser test 047 2b/2c", agent_type="general-purpose", agent_id="a1ae5a16xx")
        self.assertEqual(pane_title(a), "Local browser test 047 2b/2c")
        a.description = "x" * 80
        self.assertEqual(len(pane_title(a)), 40)
        a.description = ""
        self.assertEqual(pane_title(a), "general-purpose")


class TestMarkupEscape(unittest.TestCase):
    def test_brackets_in_agent_text_do_not_break_markup(self):
        try:
            from textual.markup import to_content
            from token_ledger.watch.app import Detail, escape
        except ImportError:
            self.skipTest("textual not installed")
        bad = "run [bold and [x>\n and [a=b>\n"
        a = agent()
        a.description = bad
        a.last_error = bad
        a.repeat_hint = bad
        a.started = 900
        to_content(Detail(a).status_markup())
        self.assertEqual(to_content(escape(bad)).plain, bad)
