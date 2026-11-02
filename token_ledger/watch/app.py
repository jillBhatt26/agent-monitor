"""Textual console: live view of the main session and its subagents."""
import os
import time

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Grid, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import DataTable, Footer, Header, Static

from . import actions, reader


def escape(text):
    # Textual's markup parser rejects unclosed tags like "[a=b>" that rich's escape leaves alone,
    # so every "[" in external text is escaped.
    return str(text).replace("[", "\\[")


def _usd(v):
    return f"{float(v or 0):.2f}"


def _tok(n):
    if isinstance(n, dict):
        n = n.get('total')
    n = int(n or 0)
    return f"{n / 1e6:.1f}M" if n >= 1_000_000 else f"{n / 1000:.0f}k" if n >= 1000 else str(n)


def _out_line():
    """Same row as `tokens today`: Output (incl. thinking), tokens, share of the day's total."""
    from .. import report
    r = reader.report_output()
    if r is None:
        return "Output (incl. thinking)   ...   (reading ledger)"
    return f"Output (incl. thinking)   {report.fmt_tok(r[0])}   {r[1]:.1f}%"


class Confirm(ModalScreen):
    BINDINGS = [Binding("y", "answer(True)", "Yes"), Binding("n,escape", "answer(False)", "No")]
    DEFAULT_CSS = """
    Confirm { align: center middle; }
    Confirm > Static { width: 70; padding: 1 2; border: thick $primary; background: $surface; }
    """

    def __init__(self, text):
        super().__init__()
        self.text = text

    def compose(self):
        yield Static(f"{self.text}\n\n[b]y[/b] confirm   [b]n[/b] cancel")

    def action_answer(self, yes):
        self.dismiss(yes)


def _summary(an):
    c = an["counts"]
    extra = (f" ?{c['unknown']}" if c["unknown"] else "") + (f" ■{c['stopped']}" if c["stopped"] else "")
    return (f"agents {an['n']} · ✓{c['success']} ✗{c['failed']} ⟳{c['running']}{extra} · {_tok(an['tokens'])} tok · "
            f"{round(an['secs'] / 60)} min · {an['cmds']} cmds")


OUTCOME = {"success": ("✓ success", "green"), "failed": ("✗ failed", "red"), "running": ("⟳ running", "cyan"),
           "stopped": ("■ dismissed", "grey50"), "unknown": ("? unknown", "yellow")}


class Analytics(Screen):
    BINDINGS = [Binding("escape,q", "app.pop_screen", "Back"), Binding("t", "sort('tokens')", "Sort tokens"),
                Binding("m", "sort('time')", "Sort time")]

    def __init__(self):
        super().__init__()
        self.sort_by = "tokens"

    def compose(self):
        yield Header()
        with VerticalScroll():
            yield Static("", id="an-main")
            yield DataTable(id="an-agents")
            yield Static("", id="an-groups")
        yield Footer()

    def on_mount(self):
        t = self.query_one("#an-agents", DataTable)
        t.add_columns("agent", "type", "model", "tokens", "output", "peak ctx", "run time", "cmds", "outcome")
        self.set_interval(1.0, self.refresh_data)
        self.refresh_data()

    def action_sort(self, by):
        self.sort_by = by
        self.refresh_data()

    def refresh_data(self):
        snap = getattr(self.app, "snap", None)
        if not snap:
            return
        main = snap.mains[0] if snap.mains else None
        tk = (main.tokens if main else {}) or {}
        total = int(tk.get("total") or 0)
        rows = [("Fresh input", tk.get("input")), ("Cache write", tk.get("cache_write")),
                ("Cache read", tk.get("cache_read")), ("Output (incl. thinking)", tk.get("output"))]
        lines = ["[b]MAIN token breakdown[/b]", f"{'kind':<26}{'tokens':>10}{'share':>8}"]
        for n, v in rows:
            v = int(v or 0)
            lines.append(f"{n:<26}{_tok(v):>10}{(v / total * 100 if total else 0):>7.1f}%")
        lines.append(f"{'Total':<26}{_tok(total):>10}{'100%':>8}   est. cost ${_usd(main.cost_usd if main else 0)}")
        self.query_one("#an-main", Static).update("\n".join(lines) + "\n")
        an = reader.analytics(snap)
        key = (lambda a: -(a.elapsed_s or 0)) if self.sort_by == "time" else (lambda a: -a.tokens.get("total", 0))
        t = self.query_one("#an-agents", DataTable)
        t.clear()
        for a in sorted(an["agents"], key=key):
            txt, col = OUTCOME.get(a.outcome, (a.outcome, "white"))
            peak = _tok(getattr(a, "peak_context", 0)) + (" !" if getattr(a, "over_budget", False) else "")
            t.add_row(pane_title(a, 34), a.agent_type, actions.short_model(a.model), _tok(a.tokens.get("total")),
                      _tok(a.tokens.get("output")), peak, actions.fmt_dur(a.elapsed_s), str(a.tool_calls or 0),
                      f"[{col}]{txt}[/]")
        g = [f"\n[b]Summary[/b] {_summary(an)}"]
        g.append("Success rate: " + (f"{an['rate']:.0f}% of finished" if an["rate"] is not None else "n/a (none finished)"))
        for title, grp in (("By agent type", an["by_type"]), ("By model", an["by_model"])):
            g.append(f"\n[b]{title}[/b]\n{'name':<28}{'n':>4}{'tokens':>9}{'output':>9}{'min':>6}{'cmds':>6}{'ok':>4}{'fail':>5}")
            for k, v in sorted(grp.items(), key=lambda kv: -kv[1]["tokens"]):
                g.append(f"{escape(k)[:27]:<28}{v['n']:>4}{_tok(v['tokens']):>9}{_tok(v['output']):>9}"
                         f"{round(v['secs'] / 60):>6}{v['cmds']:>6}{v['ok']:>4}{v['bad']:>5}")
        g.append("\nOutcome is read from transcripts only: ✓ ended with a final answer, ✗ ended without one or on an error "
                 "(a TaskStop kill looks like this), ■ you dismissed it, ? quiet >60 min with no mark. Only agents seen in the last "
                 "--since-hours count. Keys: t sort by tokens, m sort by time, Esc back.")
        self.query_one("#an-groups", Static).update("\n".join(g))


class Notice(ModalScreen):
    BINDINGS = [Binding("escape,enter,q", "close", "Close")]
    DEFAULT_CSS = """
    Notice { align: center middle; }
    Notice > Static { width: 90; padding: 1 2; border: thick $warning; background: $surface; }
    """

    def __init__(self, text):
        super().__init__()
        self.text = text

    def compose(self):
        yield Static(self.text + "\n\n[b]Esc[/b] close")

    def action_close(self):
        self.dismiss(None)


class Detail(Screen):
    BINDINGS = [Binding("escape,q", "app.pop_screen", "Back")]

    def __init__(self, agent, stalled_after=180):
        super().__init__()
        self.agent = agent
        self.stalled_after = stalled_after

    def status_markup(self):
        now = time.time()
        _, col, txt = actions.status_info(self.agent, now, self.stalled_after)
        lines = actions.status_block(self.agent, now, self.stalled_after)
        body = "\n".join(escape(l) for l in lines[1:])
        return f"[b]Status[/b]\n[b {col}]{escape(txt)}[/]\n{body}\n"

    def refresh_status(self):
        snap = getattr(self.app, "snap", None)
        if snap:
            fresh = next((x for x in snap.agents if x.agent_id == self.agent.agent_id), None)
            if fresh:
                self.agent = fresh
        self.query_one("#status", Static).update(self.status_markup())

    def compose(self):
        a = self.agent
        yield Header()
        with VerticalScroll():
            yield Static(self.status_markup(), id="status")
            yield Static(
                f"[b]{escape(a.agent_type)}[/b] · {escape(a.description or '')} · {actions.short_model(a.model)} "
                f"· effort {escape(str(a.effort or '-'))} · {'background' if a.background else 'foreground'} "
                f"· depth {a.spawn_depth} · cache hit {a.cache_hit_pct}% · state {a.state}")
            yield Static("\n[b]Troubleshoot[/b]\n" + "\n".join("- " + escape(h) for h in actions.hints(a, time.time())))
            yield Static(f"\n[b]Task[/b] {escape(a.description or '-')} · id {escape(a.agent_id[:8])} · {escape(reader.kind_label(a.kind or 'general'))}"
                         + (f"\n{escape(a.plan)}" if a.plan else "")
                         + f"\nlast (raw): {escape(str(a.last_tool or '-'))}")
            yield Static("\n[b]Brief[/b]\n" + escape(a.brief or "(none)"))
            yield Static("\n[b]Timeline[/b]")
            yield DataTable(id="tl")
        yield Footer()

    def on_mount(self):
        self.set_interval(1.0, self.refresh_status)
        t = self.query_one("#tl", DataTable)
        t.add_columns("time", "tool", "tokens", "error")
        try:
            rows = reader.timeline(self.agent.agent_id, getattr(self.app, "project_dir", None))
        except Exception:  # noqa: BLE001
            rows = []
        for r in rows[-200:]:
            t.add_row(str(r.get("ts", ""))[-8:], str(r.get("tool", "")), str(r.get("tokens", "")), str(r.get("error") or ""))


def pane_title(a, width=40):
    t = " ".join((a.description or "").split()) or a.agent_type or a.agent_id[:8]
    return t if len(t) <= width else t[:width - 1] + "…"


class AgentPane(Static, can_focus=True):
    DEFAULT_CSS = """
    AgentPane { border: round $primary; height: 10; padding: 0 1; }
    AgentPane:focus { border: heavy $accent; }
    """

    def __init__(self, agent_id, stalled_after=180, request_dir=actions.REQUEST_DIR):
        super().__init__()
        self.agent_id = agent_id
        self.agent = None
        self.working = False
        self.stalled_after = stalled_after
        self.request_dir = request_dir

    def update_agent(self, a):
        self.agent = a
        done = a.state == "done"
        col = actions.timer_colour(a.elapsed_s, done)
        kind, scol, stxt = actions.status_info(a, time.time(), self.stalled_after)
        self.working = kind == "working"
        req = actions.read_request_status(a.agent_id, self.request_dir)
        reqtxt = f"\n[b]request:[/b] {req[0]} {req[1]}" if req else ""
        plantxt = f"{escape(a.plan[:70])}\n" if getattr(a, "plan", "") else ""
        peak = getattr(a, "peak_context", 0)
        bud = getattr(a, "budget", None)
        ctxtxt = f" · peak ctx {_tok(peak)}" + (f"/{_tok(bud)}" if bud else "")
        if getattr(a, "over_budget", False):
            ctxtxt += " [b red]OVER BUDGET[/]"
        self.update(
            f"[b]{escape(a.agent_type)}[/b] · {actions.short_model(a.model)} · run time [{col}]{actions.fmt_dur(a.elapsed_s)}[/]\n"
            f"[b {scol}]{escape(stxt)}[/]\n"
            f"{escape(reader.kind_label(a.kind or 'general'))} · [dim]{a.agent_id[:8]}[/]\n"
            f"{plantxt}"
            f"{a.tool_calls or 0} tools · {_tok((a.tokens or {}).get('total'))} tok · ${_usd(a.cost_usd)}{ctxtxt}\n"
            f"last: {escape((a.last_action or str(a.last_tool or '-'))[:60])}{reqtxt}")
        self.show_title()

    def show_title(self):
        if self.agent is not None:
            self.border_title = actions.pulse_frame(time.time(), self.working) + " " + pane_title(self.agent)


class WatchApp(App):
    TITLE = "Agents Monitor"
    CSS = """
    #main { border: round $secondary; height: auto; padding: 0 1; }
    #grid { grid-size: 2; grid-gutter: 1; height: auto; }
    #totals { dock: bottom; height: 1; background: $panel; padding: 0 1; }
    """
    BINDINGS = [
        Binding("up,left", "move(-1)", "Prev", show=False),
        Binding("down,right", "move(1)", "Next", show=False),
        Binding("enter,t", "detail", "Detail (t)"),
        Binding("f", "filter", "Filter"),
        Binding("r", "request('restart')", "Restart"),
        Binding("s", "request('stop')", "Stop"),
        Binding("x", "separate", "Run separately"),
        Binding("d", "dismiss", "Done/undo (d)"),
        Binding("a", "analytics", "Analytics (a)"),
        Binding("question_mark", "help", "Help"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, project_dir=None, stalled_after=180, since_hours=24, request_dir=None,
                 dismiss_file=actions.DISMISS_FILE, cfg=None):
        super().__init__()
        from .. import config as config_mod
        self.cfg = cfg or config_mod.Config()
        w = self.cfg.watch
        self.dismiss_file = dismiss_file
        self.project_dir = project_dir or reader.detect_project_dir() or ""
        self.stalled_after = stalled_after
        self.since_hours = since_hours
        self.request_dir = request_dir or w["request_dir"]
        self.budgets = w["agent_budget_tokens"]
        self.save_at, self.compact_at = w["main_save_tokens"], w["main_compact_tokens"]
        self.burn_minutes = w["burn_window_minutes"]
        self.alerts = bool(w["alerts"])
        self.plans_dir = w.get("plans_dir")
        self.workspace = w.get("workspace")
        self.live_only = True
        self.snap = None
        self.panes = {}
        self.alerted = set()  # (agent_id, reason) already announced, so each alert fires once

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("scanning...", id="main")
        with VerticalScroll():
            yield Grid(id="grid")
        yield Static("", id="totals")
        yield Footer()

    def on_mount(self):
        self.set_interval(1.0, self.rescan)
        self.set_interval(0.15, self.pulse)
        self.rescan()

    def pulse(self):
        for p in self.panes.values():
            if p.working:
                p.show_title()

    def rescan(self):
        kw = {"stalled_after": self.stalled_after, "since_hours": self.since_hours,
              "dismissed": actions.load_dismissed(self.dismiss_file), "plans_dir": self.plans_dir,
              "budgets": self.budgets, "burn_minutes": self.burn_minutes}
        if self.project_dir:
            kw["project_dir"] = self.project_dir
        try:
            self.snap = reader.scan(**kw)
        except Exception as e:  # noqa: BLE001
            self.query_one("#totals", Static).update(f"scan failed: {escape(repr(e))}")
            return
        self.render_snapshot()
        self.check_alerts()

    def check_alerts(self):
        """Bell + toast once per agent when it stalls or goes over its context budget, and once per main session
        when its context reaches the compact threshold."""
        if not self.alerts or not self.snap:
            return
        fresh = []
        for a in self.snap.agents:
            if a.state == "stalled":
                fresh.append(((a.agent_id, "stalled"), f"{a.agent_type} {a.agent_id[:8]} STALLED - press t to troubleshoot",
                              "warning"))
            if a.over_budget and a.state != "done":
                fresh.append(((a.agent_id, "budget"), f"{a.agent_type} {a.agent_id[:8]} over its context budget "
                              f"({_tok(a.peak_context)}/{_tok(a.budget)})", "warning"))
        for m in self.snap.mains[:1]:
            if self.compact_at and m.context_tokens >= self.compact_at:
                fresh.append(((m.session_id, "ctx"), f"MAIN context {_tok(m.context_tokens)} - checkpoint and compact now",
                              "error"))
        for key, text, sev in fresh:
            if key in self.alerted:
                continue
            self.alerted.add(key)
            self.notify(text, severity=sev, timeout=8)
            self.bell()

    def render_snapshot(self):
        s = self.snap
        now = time.time()
        lines = []
        for m in s.mains:
            idle = actions.idle_seconds(m.last_activity, now)
            idle_t = f" · idle {idle // 60}m" if idle is not None and idle >= 60 else ""
            ccol, cword = actions.ctx_level(m.context_tokens, self.save_at, self.compact_at)
            gauge = actions.ctx_bar(m.context_tokens, self.compact_at)
            lines.append(
                f"[b]MAIN[/b] · {actions.short_model(m.model)} · {escape(str(m.effort or '-'))} · {actions.fmt_dur(m.elapsed_s)} "
                f"· {_tok((m.tokens or {}).get('total'))} tok · ${_usd(m.cost_usd)}\n"
                f"context [{ccol}]{gauge}[/] {_tok(m.context_tokens)} of {_tok(self.compact_at)} · "
                f"[b {ccol}]{cword}[/] · peak {_tok(m.peak_context)}\n"
                f"{_out_line()}\n"
                f"last: {escape((m.last_text or '')[:140])}{idle_t}")
        if s.mains:
            lines.append(escape(_summary(reader.analytics(s))))
        self.query_one("#main", Static).update("\n".join(lines) or "no main session found")

        agents = [a for a in s.agents if not self.live_only or a.state != "done"]
        agents.sort(key=lambda a: (a.state == "done", -(a.elapsed_s or 0)))
        ids = {a.agent_id for a in agents}
        grid = self.query_one("#grid", Grid)
        for aid in [k for k in self.panes if k not in ids]:
            self.panes.pop(aid).remove()
        for a in agents:
            pane = self.panes.get(a.agent_id)
            if pane is None:
                pane = self.panes[a.agent_id] = AgentPane(a.agent_id, self.stalled_after, self.request_dir)
                grid.mount(pane)
            pane.update_agent(a)
        t = s.totals
        over = t.get("agents_over_budget", 0)
        cpt = t.get("cost_per_token")
        rate = t.get("burn_per_min", 0.0)
        cost_h = f" ≈ ${cpt * rate * 60:,.2f}/h" if cpt and rate else ""
        self.query_one("#totals", Static).update(
            f"{t['agents_live'] + t['agents_stalled'] + t['agents_done']} agents "
            f"({t['agents_live']} live, {t['agents_stalled']} stalled, {t['agents_done']} done"
            + (f", [b red]{over} over budget[/]" if over else "") + ") · "
            f"{_tok(t['tokens'])} tok · ${_usd(t['cost_usd'])} · burn {_tok(rate)}/min{cost_h} · "
            f"project {escape(os.path.basename(self.project_dir.rstrip('/').rstrip(os.sep)))} · "
            f"filter: {'live' if self.live_only else 'all'} · ? help")

    def selected(self):
        w = self.focused
        return w.agent if isinstance(w, AgentPane) else None

    def action_move(self, d):
        (self.screen.focus_next if d > 0 else self.screen.focus_previous)()

    def action_filter(self):
        self.live_only = not self.live_only
        if self.snap:
            self.render_snapshot()

    def action_detail(self):
        a = self.selected()
        if a:
            self.push_screen(Detail(a, self.stalled_after))

    def action_analytics(self):
        if not isinstance(self.screen, Analytics):
            self.push_screen(Analytics())

    def action_dismiss(self):
        a = self.selected()
        if not a:
            return
        now_dismissed = actions.toggle_dismissed(a.agent_id, self.dismiss_file)
        self.notify("Marked done (dismissed); press d again to undo" if now_dismissed else "Dismiss removed")
        self.rescan()

    def action_request(self, kind):
        a = self.selected()
        if not a:
            return
        if not hooks_installed():
            self.push_screen(Notice("Stop/restart requests are delivered by the `tokens hook-prompt` hook, which is not in "
                                    "your Claude Code settings yet. Run the installer with --apply-hooks, or stop the agent "
                                    "from its chat (or press x to run it separately)."))
            return
        verb = "Restart" if kind == "restart" else "Stop"

        def done(yes):
            if yes:
                actions.write_request(a, kind, self.request_dir)
                self.notify(f"{verb} request written; the main session gets it with your next message")

        self.push_screen(Confirm(f"{verb} request for {a.agent_type} ({a.agent_id[:8]})?\n"
                                 "The main session receives it with your next message in that chat; nothing is killed here."),
                         done)

    def action_separate(self):
        a = self.selected()
        if not a:
            return
        cwd = self.workspace or reader.project_cwd(self.project_dir)
        if not cwd:
            self.push_screen(Notice("Cannot run separately: the project's working directory is unknown (no cwd in its transcripts)."))
            return

        def done(yes):
            if yes:
                ok, cmd = actions.run_separately(a, cwd=cwd)
                if not ok:
                    self.push_screen(Notice("Could not open a terminal. Run this yourself:\n\n" + escape(cmd)))

        self.push_screen(Confirm(f"Run {a.agent_type} separately?\nThis starts a NEW detached claude -p in its own terminal "
                                 "with the same brief and model. It is NOT linked to this chat, so its result will not "
                                 "come back here."), done)

    def action_help(self):
        self.push_screen(Notice("arrows/tab select · Enter/t detail+troubleshoot · f live/all · r restart request · s stop request\n"
                                "x run separately (detached) · a analytics screen · d mark done / undo (finished but shown STALLED) · q quit\n\n"
                                "Status word (line 2 of each pane): green ● WORKING = wrote something under 60 s ago; amber ◐ WAITING = quiet 1-3 min; "
                                "red ⚠ STALLED = quiet over 3 min; grey ✓ FINISHED = done (with how we know). The ⠋ spinner in the border moves only while WORKING.\n"
                                "Run time (green <5m, amber 5-10m, red >10m) is only how long it has run. Red timer = long run, not a problem. Look at the status word.\n\n"
                                f"Context: the MAIN gauge turns amber at {_tok(self.save_at)} (checkpoint) and red at {_tok(self.compact_at)} (compact now). "
                                "Each agent shows its peak context against its model's budget; OVER BUDGET means its brief made it read too much.\n"
                                "Burn = tokens processed per minute over the last "
                                f"{self.burn_minutes} min (cache reads included: every call re-sends the context)."))


def hooks_installed():
    """True when some ~/.claude*/settings.json runs `tokens hook-prompt` (it delivers stop/restart requests)."""
    import glob
    for p in glob.glob(os.path.join(os.path.expanduser("~"), ".claude*", "settings.json")):
        try:
            with open(p, encoding="utf-8") as f:
                if "hook-prompt" in f.read():
                    return True
        except OSError:
            continue
    return False


def run(project_dir=None, stalled_after=180, since_hours=24, cfg=None):
    WatchApp(project_dir, stalled_after, since_hours, cfg=cfg).run()
