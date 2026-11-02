import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone

from token_ledger.watch import reader


def iso(secs_ago):
    return (datetime.now(timezone.utc) - timedelta(seconds=secs_ago)).isoformat()


def user(text, ago):
    return {"type": "user", "timestamp": iso(ago), "message": {"role": "user", "content": text}}


def asst(mid, ago, blocks, inp=10, out=5, cr=100, cw=20, model="claude-sonnet-5-5"):
    return {"type": "assistant", "timestamp": iso(ago), "effort": "medium", "requestedModel": model,
            "message": {"id": mid, "model": model, "content": blocks,
                        "usage": {"input_tokens": inp, "output_tokens": out,
                                  "cache_read_input_tokens": cr, "cache_creation_input_tokens": cw}}}


def tool(tid, name="Bash", **inp):
    return {"type": "tool_use", "id": tid, "name": name, "input": inp or {"command": "ls"}}


def result(tid, ago, err=False, text="ok"):
    return {"type": "user", "timestamp": iso(ago),
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid,
                                                      "is_error": err, "content": text}]}}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.sid = "sess-1"
        os.makedirs(os.path.join(self.dir, self.sid, "subagents"))
        self.write_lines(os.path.join(self.dir, self.sid + ".jsonl"),
                         [user("hi", 500), asst("m1", 400, [{"type": "text", "text": "hello there"}])])
        reader._cache.clear()

    def tearDown(self):
        self.tmp.cleanup()

    def write_lines(self, path, lines, mode="w"):
        with open(path, mode, encoding="utf-8") as f:
            for l in lines:
                f.write(json.dumps(l) + "\n")

    def agent(self, aid, lines, meta=None):
        p = os.path.join(self.dir, self.sid, "subagents", "agent-%s.jsonl" % aid)
        self.write_lines(p, lines)
        if meta is not None:
            with open(p[:-6] + ".meta.json", "w", encoding="utf-8") as f:
                json.dump(meta, f)
        return p

    def get(self, aid, **kw):
        snap = reader.scan(self.dir, **kw)
        return next(a for a in snap.agents if a.agent_id == aid)


class StateTests(Base):
    def test_states(self):
        self.agent("live", [user("go", 30), asst("a", 10, [tool("t1")])], {"agentType": "builder", "finished": False})
        self.agent("stal", [user("go", 600), asst("a", 400, [tool("t1")])], {"agentType": "git-scout", "finished": False})
        self.agent("done", [user("go", 100), asst("a", 50, [tool("t1")])],
                   {"agentType": "weird-type", "finished": True, "requestShape": "background", "spawnDepth": 2})
        snap = reader.scan(self.dir)
        st = {a.agent_id: a for a in snap.agents}
        self.assertEqual(st["live"].state, "live")
        self.assertEqual(st["stal"].state, "stalled")
        self.assertEqual(st["done"].state, "done")
        self.assertEqual(st["live"].kind, "implementation")
        self.assertEqual(st["stal"].kind, "git ops")
        self.assertEqual(st["done"].kind, "weird-type")
        self.assertTrue(st["done"].background)
        self.assertEqual(st["done"].spawn_depth, 2)
        self.assertIsNotNone(st["done"].ended)
        self.assertAlmostEqual(st["done"].elapsed_s, 50, delta=2)
        self.assertEqual((snap.totals["agents_live"], snap.totals["agents_stalled"], snap.totals["agents_done"]), (1, 1, 1))
        self.assertEqual(len(snap.mains), 1)

    def test_no_meta_finished_uses_mtime(self):
        p = self.agent("old", [user("go", 5000), asst("a", 4000, [])], {"agentType": "builder"})
        old = time.time() - 3600
        os.utime(p, (old, old))
        self.assertEqual(self.get("old", since_hours=24).state, "done")
        p2 = self.agent("new", [user("go", 5), asst("a", 4, [])], {"agentType": "builder"})
        self.assertEqual(self.get("new").state, "live")

    def final_msg(self, ago, stop="end_turn", blocks=None):
        m = asst("f", ago, blocks or [{"type": "text", "text": "all done"}])
        m["message"]["stop_reason"] = stop
        return m

    def test_missing_finished_end_turn_is_done(self):
        self.agent("fk", [user("go", 400), self.final_msg(300)], {"agentType": "general-purpose"})
        a = self.get("fk")
        self.assertEqual((a.state, a.done_note), ("done", ""))
        self.assertAlmostEqual(a.elapsed_s, 100, delta=3)

    def test_real_skill_fork_fixture(self):
        fx = os.path.join(os.path.dirname(os.path.realpath(__file__)), "fixtures")
        p = os.path.join(self.dir, self.sid, "subagents", "agent-fx.jsonl")
        with open(os.path.join(fx, "agent-a93f9fa8.tail.jsonl"), encoding="utf-8") as src, \
                open(p, "w", encoding="utf-8") as dst:
            dst.write(src.read())
        with open(os.path.join(fx, "agent-a93f9fa8.meta.json")) as src, open(p[:-6] + ".meta.json", "w") as dst:
            dst.write(src.read())
        self.assertEqual(self.get("fx").state, "done")

    def test_missing_finished_tool_use_last_stays_live_or_stalled(self):
        self.agent("tu", [user("go", 30), self.final_msg(10, "tool_use", [tool("t1")])], {"agentType": "builder"})
        self.assertEqual(self.get("tu").state, "live")
        self.agent("tw", [user("go", 600), self.final_msg(400, "end_turn", [tool("t1")])], {"agentType": "builder"})
        self.assertEqual(self.get("tw").state, "stalled")
        self.agent("tr", [user("go", 600), self.final_msg(400), result("t1", 300)], {"agentType": "builder"})
        self.assertEqual(self.get("tr").state, "stalled")

    def test_explicit_finished_wins(self):
        self.agent("ex", [user("go", 600), self.final_msg(400)], {"agentType": "builder", "finished": False})
        self.assertEqual(self.get("ex").state, "stalled")
        self.agent("ey", [user("go", 30), self.final_msg(10, "tool_use", [tool("t1")])],
                   {"agentType": "builder", "finished": True})
        self.assertEqual(self.get("ey").state, "done")

    def test_quiet_over_an_hour_is_done_unknown(self):
        self.agent("q", [user("go", 5000), self.final_msg(4000, "tool_use", [tool("t1")])], {"agentType": "builder"})
        a = self.get("q")
        self.assertEqual((a.state, a.done_note), ("done", "done?"))

    def test_dismissed_shows_done(self):
        self.agent("dm", [user("go", 30), self.final_msg(10, "tool_use", [tool("t1")])], {"agentType": "builder"})
        snap = reader.scan(self.dir, dismissed={"dm"})
        a = next(x for x in snap.agents if x.agent_id == "dm")
        self.assertEqual((a.state, a.done_note), ("done", "dismissed"))

    def test_since_hours_filters(self):
        p = self.agent("x", [user("go", 5)], {"agentType": "builder"})
        old = time.time() - 3 * 3600
        os.utime(p, (old, old))
        self.assertEqual(reader.scan(self.dir, since_hours=1).agents, [])

    def test_missing_fields_and_garbage(self):
        p = self.agent("bare", [{"type": "assistant"}, {"foo": 1}, {"type": "user", "message": {}}])
        with open(p, "a", encoding="utf-8") as f:
            f.write("not json\n")
        a = self.get("bare")
        self.assertEqual(a.agent_type, "unknown")
        self.assertEqual(a.tool_calls, 0)
        self.assertIsNone(a.cost_usd)
        self.assertIsNone(a.cache_hit_pct)


class TokenTests(Base):
    def test_sums_dedupe_and_cost(self):
        m = asst("m1", 20, [{"type": "thinking"}], inp=10, out=5, cr=100, cw=20)
        m2 = asst("m1", 20, [tool("t1")], inp=10, out=5, cr=100, cw=20)  # same message split in two lines
        m3 = asst("m2", 10, [tool("t2", command="pwd")], inp=1, out=2, cr=3, cw=4)
        self.agent("a", [user("go", 30), m, m2, m3], {"agentType": "builder", "finished": False})
        a = self.get("a")
        self.assertEqual(a.tokens["input"], 11)
        self.assertEqual(a.tokens["output"], 7)
        self.assertEqual(a.tokens["cache_read"], 103)
        self.assertEqual(a.tokens["cache_write"], 24)
        self.assertEqual(a.tokens["total"], 11 + 7 + 103 + 24)
        self.assertEqual(a.tool_calls, 2)
        self.assertGreater(a.cost_usd, 0)
        self.assertAlmostEqual(a.cache_hit_pct, round(100 * 103 / (11 + 103 + 24), 1))
        self.assertEqual(len(reader.timeline("a", self.dir)), 2)

    def test_unknown_model_cost_none(self):
        self.agent("u", [user("go", 5), asst("m", 3, [], model="mystery-1")], {"agentType": "builder"})
        self.assertIsNone(self.get("u").cost_usd)

    def test_main_context_and_title(self):
        with open(os.path.join(self.dir, self.sid, "custom-title.json"), "w") as f:
            json.dump({"customTitle": "my-project"}, f)
        m = reader.scan(self.dir).mains[0]
        self.assertEqual(m.title, "my-project")
        self.assertEqual(m.context_tokens, 10 + 100 + 20)
        self.assertEqual(m.last_text, "hello there")


class SyntheticTests(Base):
    def test_stub_main_excluded_and_synthetic_model_ignored(self):
        stub = {"type": "assistant", "timestamp": iso(50),
                "message": {"id": "s1", "model": "<synthetic>", "content": [],
                            "usage": {"input_tokens": 0, "output_tokens": 0}}}
        self.write_lines(os.path.join(self.dir, "stub.jsonl"), [user("x", 60), stub])
        snap = reader.scan(self.dir)
        self.assertEqual([m.session_id for m in snap.mains], [self.sid])
        self.write_lines(os.path.join(self.dir, self.sid + ".jsonl"), [stub], mode="a")
        reader._cache.clear()
        self.assertEqual(reader.scan(self.dir).mains[0].model, "claude-sonnet-5-5")


class ToolTests(Base):
    def test_repeat_hint_and_last_tool(self):
        lines = [user("go", 60)] + [asst("m%d" % i, 50 - i, [tool("t%d" % i, command="ls -la")]) for i in range(3)]
        self.agent("r", lines, {"agentType": "builder", "finished": False})
        a = self.get("r")
        self.assertIsNotNone(a.repeat_hint)
        self.assertEqual(a.last_tool, "Bash: ls -la")
        lines.append(asst("m9", 5, [tool("t9", command="pwd")]))
        self.agent("r", lines, {"agentType": "builder", "finished": False})
        reader._cache.clear()
        self.assertIsNone(self.get("r").repeat_hint)

    def test_no_hint_for_two(self):
        lines = [user("go", 60)] + [asst("m%d" % i, 50 - i, [tool("t%d" % i)]) for i in range(2)]
        self.agent("r2", lines, {"agentType": "builder"})
        self.assertIsNone(self.get("r2").repeat_hint)

    def test_error_capture(self):
        lines = [user("go", 60), asst("m1", 50, [tool("t1")]), result("t1", 45, True, "boom " + "x" * 400)]
        self.agent("e", lines, {"agentType": "builder"})
        a = self.get("e")
        self.assertLessEqual(len(a.last_error), 300)
        self.assertTrue(a.last_error.startswith("boom"))
        self.assertTrue(reader.timeline("e", self.dir)[0]["error"])

    def test_last_tool_truncated(self):
        self.agent("l", [user("go", 5), asst("m1", 4, [tool("t1", command="echo " + "a b " * 50)])], {"agentType": "builder"})
        self.assertLessEqual(len(self.get("l").last_tool), 80)


class MaskTests(Base):
    def test_mask_function(self):
        self.assertEqual(reader.mask("API_KEY=abc123 and DB_PASSWORD='x y'"), "API_KEY=*** and DB_PASSWORD=***")
        self.assertIn("***@***", reader.mask("mail bob@example.com now"))
        self.assertNotIn("bob", reader.mask("mail bob@example.com now"))
        self.assertEqual(reader.mask("tok " + "a1" * 20), "tok ***")
        self.assertEqual(reader.mask("/home/dev/project/app/Services/SomeVeryLongNameService.php"),
                         "/home/dev/project/app/Services/SomeVeryLongNameService.php")

    def test_masked_in_views(self):
        self.agent("m", [user("use SECRET_TOKEN=hunter2 mail a@b.io", 10),
                         asst("m1", 5, [tool("t1", command="curl WEBHOOK_URL=http://x")]),
                         asst("m2", 4, [{"type": "text", "text": "key is " + "f0" * 20}])],
                   {"agentType": "builder"})
        a = self.get("m")
        self.assertNotIn("hunter2", a.brief)
        self.assertNotIn("a@b.io", a.brief)
        self.assertNotIn("http://x", a.last_tool)
        self.assertEqual(reader.scan(self.dir).mains[0].last_text, "hello there")


class IncrementalTests(Base):
    def test_append_and_partial_line(self):
        p = self.agent("i", [user("go", 30), asst("m1", 20, [tool("t1")])], {"agentType": "builder", "finished": False})
        self.assertEqual(self.get("i").tool_calls, 1)
        full = json.dumps(asst("m2", 10, [tool("t2", command="pwd")], inp=1, out=1, cr=1, cw=1)) + "\n"
        cut = len(full) // 2
        with open(p, "a") as f:
            f.write(full[:cut])
        self.assertEqual(self.get("i").tool_calls, 1)
        st = reader._cache[p]
        off = st.offset
        with open(p, "a") as f:
            f.write(full[cut:])
        a = self.get("i")
        self.assertEqual(a.tool_calls, 2)
        self.assertGreater(reader._cache[p].offset, off)
        self.assertEqual(a.tokens["input"], 11)

    def test_truncated_file_resets(self):
        p = self.agent("t", [user("go", 30), asst("m1", 20, [tool("t1")]), asst("m2", 10, [tool("t2")])], {"agentType": "builder"})
        self.assertEqual(self.get("t").tool_calls, 2)
        self.write_lines(p, [user("go", 30)])
        self.assertEqual(self.get("t").tool_calls, 0)


class TestProjectDetect(unittest.TestCase):
    def test_slug_and_walk_up(self):
        if os.name == "nt":
            self.assertEqual(reader.slug("E:\\MY PROJIES\\site"), "E--MY-PROJIES-site")
            base = "C:\\work\\proj"
        else:
            self.assertEqual(reader.slug("/home/dev/proj"), "-home-dev-proj")
            self.assertEqual(reader.slug("/home/a/.x/y"), "-home-a--x-y")
            base = "/home/dev/proj"
        with tempfile.TemporaryDirectory() as root:
            want = os.path.join(root, reader.slug(base))
            os.makedirs(want)
            self.assertEqual(reader.detect_project_dir(base, root), want)
            self.assertEqual(reader.detect_project_dir(os.path.join(base, "src", "app"), root), want)
            self.assertEqual(reader.detect_project_dir(os.path.join(base + "-other", "x"), root, default="/dflt"),
                             os.path.expanduser("/dflt"))
            os.makedirs(os.path.join(root, "zz-newest"))
            with open(os.path.join(root, "zz-newest", "s.jsonl"), "w", encoding="utf-8") as f:
                f.write("{}\n")
            self.assertEqual(reader.detect_project_dir(os.path.join(base + "-other", "x"), root),
                             os.path.join(root, "zz-newest"))

    def test_project_cwd(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(reader.project_cwd(d))
            with open(os.path.join(d, "s.jsonl"), "w", encoding="utf-8") as f:
                f.write(json.dumps({"type": "user", "cwd": d}) + "\n")
            self.assertEqual(reader.project_cwd(d), d)


if __name__ == "__main__":
    unittest.main()


class TestLabels(Base):
    def plans(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "047-checkout-redesign.md"), "w", encoding="utf-8") as f:
            f.write("---\nx: y\n---\n# Plan 047 — checkout_flow redesign and payment retries\n")
        with open(os.path.join(d, "046-agent-monitor-console.md"), "w", encoding="utf-8") as f:
            f.write("no heading here\n")
        return d

    def test_plan_title_and_line(self):
        d = self.plans()
        self.assertEqual(reader.plan_line("Local browser test 047 2b/2c", "", d),
                         "Plan 047 · checkout_flow redesign and payment retries")
        self.assertEqual(reader.plan_line("x", "Do plan 046 now\nmore", d), "Plan 046 · agent monitor console")
        self.assertEqual(reader.plan_line("test 999", "", d), "")

    def test_missing_dir_is_quiet(self):
        self.assertEqual(reader.plan_line("test 047", "", "/nonexistent/plans"), "")

    def test_plan_cache(self):
        d = self.plans()
        reader._plan_cache.clear()
        self.assertTrue(reader.plan_title("047", d))
        os.rename(os.path.join(d, "047-checkout-redesign.md"), os.path.join(d, "x.md"))
        self.assertIn("checkout_flow", reader.plan_title("047", d))

    def test_keyword_kinds(self):
        k = reader.infer_kind
        self.assertEqual(k("general-purpose", "Local browser test 047"), "testing")
        self.assertEqual(k("claude", "Fix login bug"), "implementation")
        self.assertEqual(k("fork", "sync develop branch"), "git ops")
        self.assertEqual(k("general-purpose", "check srv logs"), "cloud ops")
        self.assertEqual(k("general-purpose", "update wiki tutorial"), "docs")
        self.assertEqual(k("general-purpose", "audit the guard"), "review")
        self.assertEqual(k("general-purpose", "research requirements"), "research")
        self.assertEqual(k("general-purpose", "do stuff"), "general")
        self.assertEqual(k("builder", "test things"), "implementation")
        self.assertTrue(reader.kind_label("testing").endswith(" testing"))

    def test_friendly_action(self):
        f = reader.friendly_action
        home = os.path.expanduser("~")
        self.assertEqual(f("Bash", {"command": "cd %s/src/x; ls" % home}), "running command: cd ~/src/x; ls")
        self.assertEqual(len(f("Bash", {"command": "x" * 100})), len("running command: ") + 40)
        self.assertEqual(f("Read", {"file_path": "/a/b/c.php"}), "reading c.php")
        self.assertEqual(f("Edit", {"file_path": "/a/b/c.php"}), "editing c.php")
        self.assertEqual(f("Write", {"file_path": "/a/d.md"}), "editing d.md")
        self.assertEqual(f("Grep", {"pattern": "x"}), "searching")
        self.assertEqual(f("Agent", {"description": "Run tests"}), "started helper: Run tests")
        self.assertEqual(f("WebFetch", {"url": "https://example.com/a/b"}), "reading web page example.com")

    def test_scan_fills_fields(self):
        d = self.plans()
        self.agent("a1ae5a16", [user("Brief for plan 047", 100),
                                asst("a1", 90, [tool("t1", "Read", file_path="/x/y/foo.py")])],
                   {"agentType": "general-purpose", "description": "Local browser test 047 2b/2c"})
        s = reader.scan(self.dir, plans_dir=d)
        a = [x for x in s.agents if x.agent_id == "a1ae5a16"][0]
        self.assertEqual(a.kind, "testing")
        self.assertEqual(a.last_action, "reading foo.py")
        self.assertTrue(a.last_tool.startswith("Read"))
        self.assertTrue(a.plan.startswith("Plan 047 · checkout_flow"))


class ClearTests(Base):
    def clear_rec(self, ago):
        return {"type": "system", "subtype": "local_command", "timestamp": iso(ago),
                "content": "<local-command-stdout></local-command-stdout>", "commandRun": {"command": "clear", "args": ""}}

    def new_session(self, sid, clear_ago):
        self.write_lines(os.path.join(self.dir, sid + ".jsonl"),
                         [self.clear_rec(clear_ago), user("next task", clear_ago - 5),
                          asst("n-" + sid, clear_ago - 10, [{"type": "text", "text": "on it"}])])

    def test_cleared_session_hidden_but_counted(self):
        # base session's last record is 400 s ago; /clear lands 2 s later
        self.new_session("sess-2", 398)
        snap = reader.scan(self.dir)
        self.assertEqual([m.session_id for m in snap.mains], ["sess-2"])
        self.assertEqual(snap.totals["mains_cleared"], 1)
        self.assertEqual(snap.totals["tokens"]["output"], 10)

    def test_parallel_session_not_hidden(self):
        # a /clear long after the other session's last record does not retire it
        self.new_session("sess-2", 100)
        snap = reader.scan(self.dir)
        self.assertEqual(sorted(m.session_id for m in snap.mains), ["sess-1", "sess-2"])
        self.assertEqual(snap.totals["mains_cleared"], 0)

    def test_clear_as_user_command_text(self):
        rec = user("<command-name>/clear</command-name>\n<command-message>clear</command-message>", 399)
        self.write_lines(os.path.join(self.dir, "sess-3.jsonl"),
                         [rec, asst("n3", 390, [{"type": "text", "text": "ok"}])])
        self.assertEqual([m.session_id for m in reader.scan(self.dir).mains], ["sess-3"])
