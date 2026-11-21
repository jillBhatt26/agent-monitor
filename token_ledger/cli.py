"""Command line entry point."""
import argparse
import datetime as dt
import json
import os
import subprocess
import sys

from . import compat
from . import config as config_mod


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--data-dir", default=argparse.SUPPRESS, help="folder for tokens.sqlite etc. (default from config)")
    flt = argparse.ArgumentParser(add_help=False)
    flt.add_argument("--project")
    flt.add_argument("--all", action="store_true", help="every project, not just the active one")
    flt.add_argument("--account")
    flt.add_argument("--model")
    flt.add_argument("--machine")
    flt.add_argument("--source")
    flt.add_argument("--config-dir", dest="config_dir")
    p = argparse.ArgumentParser(prog="tokens", parents=[common], description="Token use per feature, task kind, model and day.")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("ingest", parents=[common], help="import new log lines")
    s.add_argument("--quiet", action="store_true")
    s.add_argument("--force", action="store_true", help="re-read every file (adds nothing already stored)")
    s.add_argument("--reclassify", action="store_true", help="re-read every file and re-sort stored calls with the current rules and tags")
    for name in ("today", "week", "month", "year"):
        sub.add_parser(name, parents=[common, flt], help=f"report for {name}")
    s = sub.add_parser("features", parents=[common, flt], help="feature -> task kind tree")
    s.add_argument("--since", metavar="YYYY-MM-DD")
    s = sub.add_parser("feature", parents=[common, flt], help="one feature in detail (text match)")
    s.add_argument("text")
    s = sub.add_parser("daily", parents=[common, flt], help="per-day table")
    s.add_argument("--days", type=int, default=30)
    s = sub.add_parser("tag", parents=[common], help="label upcoming work with a feature (and optional kind)")
    s.add_argument("feature", nargs="?")
    s.add_argument("kind", nargs="?")
    s.add_argument("--session")
    s.add_argument("--clear", action="store_true")
    s = sub.add_parser("html", parents=[common], help="write the HTML dashboard")
    s.add_argument("--no-open", action="store_true")
    sub.add_parser("check", parents=[common], help="self-checks")
    s = sub.add_parser("export", parents=[common], help="write normalized rows as JSON lines to stdout")
    s.add_argument("--since", metavar="YYYY-MM-DD")
    s = sub.add_parser("import", parents=[common], help="merge a file made by `tokens export`")
    s.add_argument("file")
    s = sub.add_parser("watch", parents=[common], help="live console of the main session and its subagents")
    s.add_argument("--stalled-after", type=int, default=180, metavar="SECONDS")
    s.add_argument("--since-hours", type=int, default=24)
    s.add_argument("--project-dir")
    s.add_argument("--dump-plans", action="store_true", help="print the Plans screen as text and exit")
    s = sub.add_parser("agents", parents=[common], help="one-shot snapshot of the main session and its agents (no TUI)")
    s.add_argument("--stalled-after", type=int, default=180, metavar="SECONDS")
    s.add_argument("--since-hours", type=int, default=24)
    s.add_argument("--project-dir")
    s.add_argument("--all", action="store_true", help="include finished agents")
    s.add_argument("--json", action="store_true", help="machine-readable output")
    sub.add_parser("hook-session-start", parents=[common], help="Claude Code SessionStart hook")
    sub.add_parser("hook-prompt", parents=[common], help="Claude Code UserPromptSubmit hook")
    return p


def hook(cfg, start):
    """Never raises: a hook must not disturb a session. Prints only when the monitor has restart/stop requests for
    this session (UserPromptSubmit stdout becomes context for the model)."""
    from . import store
    from .sources import claude_code
    try:
        raw = "" if sys.stdin.isatty() else sys.stdin.read()
        try:
            sid = (json.loads(raw) or {}).get("session_id", "") if raw.strip() else ""
        except ValueError:
            sid = ""
        sid = sid or os.environ.get("CLAUDE_SESSION_ID", "")
        conn = store.connect(cfg)
        email, uid = claude_code.current_account()
        store.record_mark(conn, sid, email, uid)
        if start and sid:
            conn.execute("INSERT INTO session_accounts(session_id, account, email, first_seen) "
                         "SELECT ?,?,?,? WHERE NOT EXISTS (SELECT 1 FROM session_accounts WHERE session_id=? AND email IS ?)",
                         (sid, claude_code.current_config_label(), email, store.now_ts(), sid, email))
            conn.commit()
        conn.close()
        if start:
            script = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "tokens")
            subprocess.Popen([sys.executable, script, "ingest", "--quiet", "--data-dir", cfg.data_dir],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=open(cfg.log_path, "a", encoding="utf-8"), **compat.detached_kwargs())
        elif sid:
            deliver_requests(cfg, sid)
    except Exception as e:  # noqa: BLE001
        store.log(cfg, f"hook error: {e!r}")
    return 0


def deliver_requests(cfg, sid):
    """Hand the monitor's pending restart/stop requests for this session to the model, once."""
    from .watch import actions
    reqs = actions.pending_requests(sid, cfg.watch["request_dir"])
    if not reqs:
        return
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                             "additionalContext": actions.request_context(reqs)}}))
    actions.mark_requests(reqs)


def cmd_agents(cfg, a):
    """Plain snapshot (no Textual needed): for scripts, CI, or a model checking on its own agents."""
    from .watch import actions, reader
    project = os.path.expanduser(a.project_dir) if a.project_dir else reader.detect_project_dir()
    w = cfg.watch
    snap = reader.scan(project, a.stalled_after, a.since_hours, actions.load_dismissed(),
                       w.get("plans_dir"), w["agent_budget_tokens"], w["burn_window_minutes"])
    agents = [x for x in snap.agents if a.all or x.state != "done"]
    if a.json:
        def view(o):
            d = dict(vars(o))
            for k, v in d.items():
                if isinstance(v, dt.datetime):
                    d[k] = v.isoformat()
            d.pop("brief", None)  # briefs can be long; the transcript has them
            return d
        print(json.dumps({"project_dir": project, "taken_at": snap.taken_at.isoformat(), "totals": snap.totals,
                          "mains": [view(m) for m in snap.mains], "agents": [view(x) for x in agents]}, default=str))
        return 0
    now = snap.taken_at.timestamp()
    t = snap.totals
    print(f"project {os.path.basename(str(project).rstrip(os.sep))} · {len(snap.mains)} main · "
          f"{t['agents_live']} live · {t['agents_stalled']} stalled · {t['agents_done']} done · "
          f"{actions.fmt_k(t['tokens']['total'])} tok · burn {actions.fmt_k(t['burn_per_min'])}/min")
    for m in snap.mains[:3]:
        col, word = actions.ctx_level(m.context_tokens, w["main_save_tokens"], w["main_compact_tokens"])
        print(f"MAIN {m.session_id[:8]} {actions.short_model(m.model)} ctx {actions.fmt_k(m.context_tokens)} "
              f"[{word}] {actions.ctx_bar(m.context_tokens, w['main_compact_tokens'])}")
    for x in agents:
        _, _, txt = actions.status_info(x, now, a.stalled_after)
        over = f" OVER BUDGET ({actions.fmt_k(x.peak_context)}/{actions.fmt_k(x.budget)})" if x.over_budget else ""
        print(f"- {x.agent_id[:8]} {x.agent_type} {actions.short_model(x.model)} · {txt} · "
              f"{x.tool_calls} tools · peak ctx {actions.fmt_k(x.peak_context)}{over} · {x.description[:50]}")
    return 0


def cmd_tag(cfg, a):
    from . import store
    os.makedirs(cfg.data_dir, exist_ok=True)
    if a.clear:
        sid = a.session or os.environ.get("CLAUDE_SESSION_ID") or "*"
        line = {"ts": store.now_ts(), "session_id": sid, "feature": None}
    elif a.feature:
        sid = a.session or os.environ.get("CLAUDE_SESSION_ID") or "*"
        line = {"ts": store.now_ts(), "session_id": sid, "feature": a.feature}
        if a.kind:
            line["kind"] = a.kind
    else:
        from . import classify
        tags = classify.load_tags(cfg.tags_path)
        sid = os.environ.get("CLAUDE_SESSION_ID") or "*"
        t = classify.tag_at(tags, sid, "9999")
        print(f"Current tag: {t['feature']}" + (f" ({t['kind']})" if t.get('kind') else "") if t and t.get("feature")
              else "No tag set (calls are sorted by the rules).")
        return 0
    with open(cfg.tags_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(line) + "\n")
    print("Tag cleared." if a.clear else f"Tagged: {a.feature}" + (f" / {a.kind}" if a.kind else "")
          + f"  (applies to {'every session' if sid == '*' else 'session ' + sid[:8]} from now on, on the next ingest)")
    return 0


def cmd_watch(cfg, a):
    """Textual lives in .venv-watch (the system Python may be externally managed)."""
    from .watch import reader
    project = os.path.expanduser(a.project_dir) if a.project_dir else reader.detect_project_dir()
    if a.dump_plans:
        from .watch import plans_view
        print(plans_view.dump_main(project, a.stalled_after, a.since_hours))
        return 0
    if not project:
        print("No Claude Code project found under ~/.claude/projects. Start a Claude Code session first, "
              "or pass --project-dir.")
        return 1
    try:
        import textual  # noqa: F401
    except ImportError:
        root = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
        py = compat.venv_python(root)
        if not py:
            print("Textual is missing. Run the installer once:  python install.py")
            return 1
        args = [py, "-m", "token_ledger.cli", "watch", "--stalled-after", str(a.stalled_after),
                "--since-hours", str(a.since_hours), "--project-dir", project]
        if getattr(a, "data_dir", None):
            args += ["--data-dir", a.data_dir]
        if compat.IS_WIN:  # os.execv on Windows spawns and returns at once, which breaks the terminal UI
            return subprocess.call(args, cwd=root)
        os.chdir(root)
        os.execv(py, args)
    from .watch import app
    app.run(project_dir=project, stalled_after=a.stalled_after, since_hours=a.since_hours, cfg=cfg)
    return 0


def main(argv=None):
    compat.utf8_stdio()
    a = build_parser().parse_args(argv)
    cfg = config_mod.load(getattr(a, "data_dir", None))
    if a.cmd in ("hook-session-start", "hook-prompt"):
        return hook(cfg, a.cmd == "hook-session-start")
    if a.cmd == "tag":
        return cmd_tag(cfg, a)
    if a.cmd == "watch":
        return cmd_watch(cfg, a)
    if a.cmd == "agents":
        return cmd_agents(cfg, a)
    from . import ingest, report, store
    if a.cmd == "ingest":
        ingest.run(cfg, quiet=a.quiet, force=a.force, reclassify=a.reclassify)
        return 0
    if a.cmd == "import":
        conn = store.connect(cfg)
        with open(a.file, encoding="utf-8") as f:
            n = store.import_rows(conn, f)
        print(f"imported {n} new calls (rows already present were skipped)")
        return 0
    conn = store.connect(cfg)
    if a.cmd == "export":
        for r in store.export_rows(conn, a.since):
            sys.stdout.write(json.dumps(r) + "\n")
        return 0
    if a.cmd == "check":
        ingest.run(cfg, quiet=True)
        args = argparse.Namespace(all=True)
        return report.cmd_check(conn, cfg, report.Flt(args, conn), lambda: ingest.run(cfg, quiet=True, force=True))
    ingest.run(cfg, quiet=True)
    store.refresh_days(conn, cfg)
    conn.close()
    conn = store.connect(cfg)
    if a.cmd == "html":
        from . import html as dash
        path, size = dash.write(conn, cfg)
        print(f"wrote {path} ({size / 1024:,.0f} kB)")
        if not a.no_open and not compat.open_file(path):
            print("Could not open a browser; open the file above yourself.")
        return 0
    flt = report.Flt(a, conn)
    if a.cmd in ("today", "week", "month", "year"):
        report.cmd_period(conn, cfg, flt, a.cmd)
    elif a.cmd == "features":
        report.cmd_features(conn, cfg, flt, a.since)
    elif a.cmd == "feature":
        report.cmd_feature(conn, cfg, flt, a.text)
    elif a.cmd == "daily":
        report.cmd_daily(conn, cfg, flt, a.days)
    return 0


if __name__ == "__main__":
    sys.exit(main())
