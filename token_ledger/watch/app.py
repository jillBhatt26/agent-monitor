"""Textual console: live view of the main session and its subagents."""
import os
import sys
import time

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Horizontal, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import DataTable, Footer, Header, Static

from ..board import workspace
from . import actions, ctx_board, plans_view, reader
from .fmt import human_age, human_ms


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


CTX_MISSING = "Context monitor not built — run python tools/ctx/ctx.py index"
_ctx_mod = None


def ctx_monitor():
    """tools/ctx/monitor.py of the workspace, or None when it is missing; the app must keep working without it."""
    global _ctx_mod
    if _ctx_mod is None:
        try:
            ctx_dir = os.path.join(workspace(), "tools", "ctx")
            if ctx_dir not in sys.path:
                sys.path.insert(0, ctx_dir)
            import monitor
            _ctx_mod = monitor
        except Exception:  # noqa: BLE001
            return None
    return _ctx_mod


def ctx_tab_text(mon, d, tab):
    """Plain-text body of one Context sub-tab, from monitor.collect() data."""
    if tab == "overview":
        return None
    if tab == "week":
        return escape(mon.week_report(d))
    i, s, m, g, r = d["index"], d["search"], d["misses"], d["guard"], d["restore"]
    L = []
    if tab == "index":
        L.append(f"db {mon.mb(i['db_bytes'])} = {i['pct_cap']}% of cap | integrity {i['integrity']} | stale {i['stale']}")
        L += [f"root {k:<14} files {v['files']:>6} chunks {v['chunks']:>7}" for k, v in i["roots"].items()]
        L.append("skipped: " + (", ".join(f"{k}={v}" for k, v in i["skipped"].items()) or "none"))
        lr = i["last_run"]
        L.append(f"last index: {lr['ts']} took {human_ms(lr['ms'])}, {human_age(lr['age_h'] * 3600)} ago" if lr else "last index: none recorded")
    elif tab == "searches":
        L.append(f"{s['count']} searches | tokens avg {s['tokens_avg']} max {s['tokens_max']} | "
                 f"cursor cuts {s['cursor_cuts']} ceiling cuts {s['ceiling_cuts']}")
        L.append("\ntop queries")
        L += [f"  {q} ({n})" for q, n in s["top_queries"][:15]]
        L.append("\nper day")
        L += [f"  {k}: {v}" for k, v in s["per_day"].items()]
    elif tab == "misses":
        L.append(f"{m['count']} misses | 0-hit {m['zero_hit']} | unused {m['unused_hit']} | wording-suspect {m['wording_suspect']}")
        L.append("categories: " + (", ".join(f"{k}={v}" for k, v in m["by_category"].items()) or "none"))
        L.append("\nrecent misses")
        L += [f"  {x['q']} ({x['cat']})" for x in m["list"][-20:]] or ["  none"]
    elif tab == "guard":
        if not g:
            return "guard: no data yet"
        L.append(f"allowed {g['allowed']} | denied {g['denied']} | hook errors {g['hook_errors']} | "
                 f"ms p50 {g['hook_ms_p50']} p95 {g['hook_ms_p95']}")
        L.append("\ndenied files")
        L += [f"  {f['path']} size {f['size']} free {f['free']}" for f in g["denied_files"][-10:]] or ["  none"]
        L.append("\nper day (allowed, denied)")
        L += [f"  {k}: {v[0]}, {v[1]}" for k, v in g["per_day"].items()]
    elif tab == "restore":
        if not r:
            return "restore: no data yet"
        L.append(f"last {r['last_bytes']} B | max {r['max_bytes']} B | limit {mon.RESTORE_LIMIT} B")
        L.append("series: " + ", ".join(str(x) for x in r["series"][-14:]))
        gr = d["growth"]
        L.append(f"\ngrowth: {len(gr['big_files'])} files > 200 KB")
        L += [f"  {x['path']} {mon.mb(x['bytes'])}" for x in gr["big_files"][:10]]
    return escape("\n".join(L))


class CtxCard(Static, can_focus=True):
    """One Overview card; focusing or hovering it fills the Explain panel."""

    DEFAULT_CSS = "CtxCard { height: auto; width: 100%; } CtxCard:focus { background: #262b33; }"

    def __init__(self, key):
        super().__init__("", id=f"card-{key}")
        self.key = key
        self.tooltip = ctx_board.EXPLAIN_SHORT[key]

    def on_focus(self, event):
        self.screen.show_explain(self.key)

    def on_enter(self, event):
        self.screen.show_explain(self.key)

    def on_leave(self, event):
        foc = self.screen.focused
        self.screen.show_explain(foc.key if isinstance(foc, CtxCard) else None)


class ContextScreen(Screen):
    DEFAULT_CSS = """
    #ctx-ov { display: none; height: auto; grid-rows: auto; grid-gutter: 0; }
    #ctx-explain { display: none; height: auto; max-height: 8; padding: 0 1; border-top: solid #3c424b; }
    """
    TABS = {"overview": "Overview", "index": "Index (i)", "searches": "Searches (s)", "misses": "Misses (m)",
            "guard": "Guard (g)", "restore": "Restore (r)", "week": "Week review (w)"}
    BINDINGS = [Binding("escape,q,backspace", "app.pop_screen", "Back"), Binding("i", "tab('index')", "Index"),
                Binding("s", "tab('searches')", "Searches"), Binding("m", "tab('misses')", "Misses"),
                Binding("g", "tab('guard')", "Guard"), Binding("r", "tab('restore')", "Restore"),
                Binding("w", "tab('week')", "Week"), Binding("o", "tab('overview')", "Overview"),
                Binding("e", "save_week", "Save week"),
                Binding("left", "move(-1)", "Prev card", show=False), Binding("right", "move(1)", "Next card", show=False),
                Binding("up", "move(-3)", "Card above", show=False), Binding("down", "move(3)", "Card below", show=False)]

    def __init__(self):
        super().__init__()
        self.tab = "overview"
        self.data = None
        self.saved = ""
        self.busy = False

    def compose(self):
        yield Header()
        yield Static("", id="ctx-tabs")
        with VerticalScroll():
            yield Static("loading...", id="ctx-body")
            yield CtxCard("main")
            with Grid(id="ctx-ov"):
                for key in ctx_board.CARD_KEYS[1:-1]:
                    yield CtxCard(key)
            yield CtxCard("root")
        yield Static(ctx_board.DEFAULT_EXPLAIN, id="ctx-explain")
        yield Footer()

    def on_mount(self):
        self.set_interval(30.0, self.refresh_data)
        self.refresh_data()

    def action_tab(self, tab):
        self.tab = tab
        self.render_tab()

    def show_explain(self, key):
        if key is None:
            txt = Text(ctx_board.DEFAULT_EXPLAIN, style=ctx_board.MUTED)
        else:
            txt = Text()
            txt.append(ctx_board.CARD_NAMES[key] + "\n", style="bold " + ctx_board.FG)
            txt.append(ctx_board.EXPLAIN_LONG[key], style=ctx_board.FG)
        self.query_one("#ctx-explain", Static).update(txt)

    def action_move(self, step):
        cards = list(self.query(CtxCard))
        if self.tab != "overview" or not cards:
            return
        if self.focused not in cards:
            cards[0].focus()
            return
        cur = cards.index(self.focused)
        # the "up" step jumps a whole row; stepping above the first row lands on the MAIN card
        nxt = 0 if cur + step < 1 and abs(step) > 1 else max(0, min(len(cards) - 1, cur + step))
        cards[nxt].focus()

    def refresh_data(self):
        if self.busy:
            return
        self.busy = True
        self.run_worker(self._load, thread=True, exclusive=False)

    def _load(self):
        data = None
        try:
            mon = ctx_monitor()
            if mon is not None:
                data = mon.collect(since_days=7)
        except Exception:  # noqa: BLE001
            data = None
        self.app.call_from_thread(self._loaded, data)

    def _loaded(self, data):
        self.busy = False
        self.data = data
        self.render_tab()

    def action_save_week(self):
        mon = ctx_monitor()
        if self.tab != "week" or mon is None or not self.data:
            self.notify("Open the week review (w) first, then press e to save it")
            return
        try:
            self.saved = mon.save_week(mon.week_report(self.data), self.data["generated"][:10])
        except Exception as e:  # noqa: BLE001
            self.saved = f"save failed: {e!r}"
        self.render_tab()

    def render_tab(self):
        tabs = "  ".join(f"[b reverse] {n} [/]" if k == self.tab else n for k, n in self.TABS.items())
        self.query_one("#ctx-tabs", Static).update(tabs + "   (o) · e saves week · Esc")
        mon = ctx_monitor()
        if mon is None or not self.data or not self.data.get("built"):
            body = CTX_MISSING
        else:
            try:
                body = ctx_tab_text(mon, self.data, self.tab)
                if body is None:
                    snap = getattr(self.app, "snap", None)
                    body = ctx_board.overview_parts(self.data, snap.mains if snap else [],
                                                    self.data.get("alerts") or [], self.size.width)
            except Exception as e:  # noqa: BLE001
                body = escape(f"could not render: {e!r}")
            if self.saved and self.tab == "week":
                body = f"[#6fbf73]saved: {escape(self.saved)}[/]\n\n" + body
        parts = body if isinstance(body, dict) else None
        self.query_one("#ctx-body", Static).update(parts["head"] if parts else body)
        ov, ex = self.query_one("#ctx-ov"), self.query_one("#ctx-explain")
        ov.display = ex.display = parts is not None
        self.query_one("#card-main").display = self.query_one("#card-root").display = parts is not None
        if parts:
            ov.styles.grid_size_columns = parts["ncol"]
            panels = {"main": parts["main"], "root": parts["root"], **dict(parts["cards"])}
            for card in self.query(CtxCard):
                card.update(panels[card.key])


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
    BINDINGS = [Binding("escape,backspace,q", "app.pop_screen", "Back")]

    def __init__(self, agent, stalled_after=180, crumb=""):
        super().__init__()
        self.agent = agent
        self.stalled_after = stalled_after
        self.crumb = crumb

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
        if self.crumb:
            yield Static(self.crumb, classes="crumb")
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


def main_text(s, save_at, compact_at):
    """The MAIN block shown on top of the Plans screen and the All agents screen."""
    now = time.time()
    lines = []
    for m in s.mains:
        idle = actions.idle_seconds(m.last_activity, now)
        idle_t = f" · idle {idle // 60}m" if idle is not None and idle >= 60 else ""
        ccol, cword = actions.ctx_level(m.context_tokens, save_at, compact_at)
        gauge = actions.ctx_bar(m.context_tokens, compact_at)
        lines.append(
            f"[b]MAIN[/b] · {actions.short_model(m.model)} · {escape(str(m.effort or '-'))} · {actions.fmt_dur(m.elapsed_s)} "
            f"· {_tok((m.tokens or {}).get('total'))} tok · ${_usd(m.cost_usd)}\n"
            f"context [{ccol}]{gauge}[/] {_tok(m.context_tokens)} of {_tok(compact_at)} · "
            f"[b {ccol}]{cword}[/] · peak {_tok(m.peak_context)}\n"
            f"{_out_line()}\n"
            f"last: {escape((m.last_text or '')[:140])}{idle_t}")
    if s.mains:
        lines.append(escape(_summary(reader.analytics(s))))
    return "\n".join(lines) or "no main session found"


class PlanCard(Static, can_focus=True):
    """One plan on the board."""
    DEFAULT_CSS = """
    PlanCard { border: round $primary; height: 5; padding: 0 1; margin-bottom: 0; }
    PlanCard.live { border: round green; }
    PlanCard:focus { border: heavy $accent; background: $boost; }
    """

    def __init__(self, row):
        super().__init__()
        self.row = row
        self.plan_id = row["id"]

    def set_row(self, row):
        self.row = row
        self.set_class(bool(row.get("live_now")), "live")
        self._paint()

    def on_resize(self, ev):
        self._paint()

    def on_click(self, ev):
        ev.stop()
        self.focus()
        self.screen.action_open()

    def _paint(self):
        w = self.size.width - 4 if self.size.width else 30
        l1, l2, l3 = plans_view.card_lines(self.row, w)
        self.update(f"[b]{escape(l1)}[/b]\n{escape(l2)}\n{escape(l3)}")


class PlanColumn(VerticalScroll):
    DEFAULT_CSS = "PlanColumn { width: 1fr; height: 100%; border: none; padding: 0 1; } .colhead { text-style: bold; background: $panel; padding: 0 1; }"


class PlansScreen(Screen):
    """Default screen: a kanban board of every plan, one column per status group. Enter opens plan detail."""
    BINDINGS = [Binding("enter,t", "open", "Open (t)"), Binding("a", "app.show_all", "All agents"),
                Binding("tab", "app.show_all", "All agents", show=False),
                Binding("left", "step(-1,0)", "Column", show=False), Binding("right", "step(1,0)", "Column", show=False),
                Binding("up", "step(0,-1)", "Card", show=False), Binding("down", "step(0,1)", "Card", show=False)]
    DEFAULT_CSS = """
    .crumb { background: $panel; padding: 0 1; }
    #pmain { border: round $secondary; height: auto; padding: 0 1; }
    #board { height: 1fr; }
    """

    def __init__(self):
        super().__init__()
        self.shape = []   # [(group, [plan id])]
        self.cols = []     # [[PlanCard]]
        self.busy = False

    def compose(self):
        yield Header()
        yield Static("", id="pmain")
        yield Static("Plans", classes="crumb")
        yield Horizontal(id="board")
        yield Footer()

    def on_mount(self):
        self.set_interval(2.0, self.refresh_data)
        self.set_interval(1.0, self.refresh_main)
        self.refresh_main()
        self.run_worker(self.refresh_data(), exclusive=True, group="board")

    def on_screen_resume(self):
        self.focus_card(*self.pos() or (0, 0))

    def refresh_main(self):
        snap = getattr(self.app, "snap", None)
        self.query_one("#pmain", Static).update(
            main_text(snap, self.app.save_at, self.app.compact_at) if snap else "scanning...")

    def pos(self):
        f = self.focused
        for ci, col in enumerate(self.cols):
            if f in col:
                return ci, col.index(f)
        return None

    def focus_card(self, ci, ri):
        if not any(self.cols):
            return
        ci = max(0, min(ci, len(self.cols) - 1))
        if not self.cols[ci]:
            ci = next((c for c in range(len(self.cols)) if self.cols[c]), ci)
        ri = max(0, min(ri, len(self.cols[ci]) - 1))
        self.cols[ci][ri].focus()

    async def refresh_data(self):
        if self.busy:
            return
        self.busy = True
        try:
            await self._refresh()
        finally:
            self.busy = False

    async def _refresh(self):
        self.refresh_main()
        board = self.query_one("#board", Horizontal)
        snap = getattr(self.app, "snap", None)
        plans_view.record_board_changes()
        try:
            # Untagged agents stay on the All agents screen, so System/Unsorted get no column here.
            groups = [(g, rows) for g, rows in plans_view.group_rows(snap, plans_view.load_tasks(), keep_empty=True)
                      if g not in ("System", "Unsorted")]
        except Exception as e:  # noqa: BLE001
            await board.remove_children()
            self.cols = []
            self.shape = None
            await board.mount(Static(f"plans failed: {escape(repr(e))}"))
            return
        layout = [(g, [r["id"] for r in rows]) for g, rows in groups]
        if layout != self.shape:
            cur = self.focused.plan_id if isinstance(self.focused, PlanCard) else None
            await board.remove_children()
            self.cols = []
            columns = []
            for g, rows in groups:
                cards = [PlanCard(r) for r in rows]
                self.cols.append(cards)
                columns.append(PlanColumn(Static(f"{escape(g)} ({len(rows)})", classes="colhead"), *cards))
            self.shape = layout
            await board.mount(*columns)
            if self.is_current:
                at = next(((ci, ri) for ci, c in enumerate(self.cols) for ri, k in enumerate(c) if k.plan_id == cur), (0, 0))
                self.focus_card(*at)
        for (g, rows), cards in zip(groups, self.cols):
            for r, card in zip(rows, cards):
                card.set_row(r)

    def selected_agent(self):
        return None

    def action_step(self, dx, dy):
        p = self.pos()
        if p is None:
            self.focus_card(0, 0)
            return
        ci, ri = p
        if dx:
            nxt = ci + dx
            while 0 <= nxt < len(self.cols) and not self.cols[nxt]:
                nxt += dx
            if 0 <= nxt < len(self.cols):
                ci = nxt
        self.focus_card(ci, ri + dy)

    def action_open(self):
        f = self.focused
        if isinstance(f, PlanCard) and f.plan_id != "main":
            self.app.push_screen(PlanDetail(f.plan_id))


class PlanDetail(Screen):
    BINDINGS = [Binding("escape,backspace", "app.pop_screen", "Back"), Binding("enter,t", "open", "Agent (t)")]
    DEFAULT_CSS = """
    .crumb { background: $panel; padding: 0 1; }
    #pd-grid { grid-size: 2; grid-gutter: 1; height: auto; }
    """

    def __init__(self, plan_id):
        super().__init__()
        self.plan_id = plan_id
        self.ids = []
        self.panes = {}

    def crumb(self):
        plan = None if self.plan_id == plans_view.UNSORTED else plans_view.find_plan(self.plan_id)
        name = f"{self.plan_id} {plan.title[:28]}" if plan else "Unsorted"
        return f"Plans › {escape(name)}"

    def compose(self):
        yield Header()
        yield Static(self.crumb(), classes="crumb")
        yield Static("", id="pd-head")
        with VerticalScroll(can_focus=False):
            yield Grid(id="pd-grid")
        yield Footer()

    def on_mount(self):
        self.set_interval(1.5, self.refresh_data)
        self.set_interval(0.15, self.pulse)
        self.refresh_data()

    def on_screen_resume(self):
        if not isinstance(self.focused, AgentPane) and self.panes:
            next(iter(self.panes.values())).focus()

    def pulse(self):
        for p in self.panes.values():
            if p.working:
                p.show_title()

    def selected_agent(self):
        f = self.focused
        return f.agent if isinstance(f, AgentPane) else None

    def refresh_data(self):
        snap = getattr(self.app, "snap", None)
        tasks = plans_view.load_tasks()
        live, done = plans_view.plan_agents(snap, tasks, self.plan_id)
        self.query_one("#pd-head", Static).update(f"[b]Agents[/b] {len(live)} live, {len(done)} finished today")
        self.ids = live + done
        keep = {a.agent_id for a in self.ids}
        grid = self.query_one("#pd-grid", Grid)
        kept = [k for k in self.panes if k in keep]
        if kept != [a.agent_id for a in self.ids if a.agent_id in self.panes]:
            keep = set()  # order changed (live before finished): remount in order
        for aid in [k for k in self.panes if k not in keep]:
            self.panes.pop(aid).remove()
        fresh = False
        for a in self.ids:
            pane = self.panes.get(a.agent_id)
            if pane is None:
                pane = self.panes[a.agent_id] = AgentPane(a.agent_id, self.app.stalled_after, self.app.request_dir)
                grid.mount(pane)
                fresh = True
            pane.update_agent(a)
        if fresh and not isinstance(self.focused, AgentPane) and self.panes and self.is_current:
            self.set_timer(0.05, self.on_screen_resume)

    def action_open(self):
        a = self.selected_agent()
        if a:
            self.app.push_screen(Detail(a, self.app.stalled_after, f"{self.crumb()} › {escape(pane_title(a, 30))}"))


class WatchApp(App):
    TITLE = "Agents Monitor"
    CSS = """
    #main { border: round $secondary; height: auto; padding: 0 1; }
    #grid { grid-size: 2; grid-gutter: 1; height: auto; }
    #totals { dock: bottom; height: 1; background: $panel; padding: 0 1; }
    WatchApp.ctx-red Header { background: #5a1f1f; color: #ffffff; text-style: bold; }
    WatchApp.ctx-red HeaderTitle { color: #ffffff; text-style: bold; }
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
        Binding("1", "show_plans", "Plans (1)"),
        Binding("2", "show_all", "All agents (2)"),
        Binding("3", "show_context", "Context (3)"),
        Binding("escape,backspace", "back", "Back", show=False),
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
        self.set_interval(60.0, self.check_ctx_badge)
        self.rescan()
        self.push_screen(PlansScreen())
        self.check_ctx_badge()

    def check_ctx_badge(self):
        self.run_worker(self._badge_work, thread=True, exclusive=False, group="ctx-badge")

    def _badge_work(self):
        red = []
        try:
            mon = ctx_monitor()
            if mon is not None:
                red = mon.alerts(mon.collect(since_days=1))
        except Exception:  # noqa: BLE001
            red = []
        self.call_from_thread(self._set_badge, bool(red))

    def _set_badge(self, red):
        self.set_class(red, "ctx-red")
        self.sub_title = "CTX alert — press 3" if red else ""

    def action_show_context(self):
        if not isinstance(self.screen, (Confirm, Notice, ContextScreen)):
            self.push_screen(ContextScreen())

    def action_show_plans(self):
        if isinstance(self.screen, (Confirm, Notice)):
            return
        while len(self.screen_stack) > 1 and not isinstance(self.screen, PlansScreen):
            self.pop_screen()
        if not isinstance(self.screen, PlansScreen):
            self.push_screen(PlansScreen())

    def action_show_all(self):
        if isinstance(self.screen, (Confirm, Notice)):
            return
        while len(self.screen_stack) > 1:
            self.pop_screen()

    def action_back(self):
        if len(self.screen_stack) > 2 and not isinstance(self.screen, (PlansScreen, Confirm, Notice)):
            self.pop_screen()

    def base(self):
        # The monitor screens sit on top of the default screen, which keeps owning the agent panes.
        return self.screen_stack[0]

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
            self.base().query_one("#totals", Static).update(f"scan failed: {escape(repr(e))}")
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
        self.base().query_one("#main", Static).update(main_text(s, self.save_at, self.compact_at))

        agents = [a for a in s.agents if not self.live_only or a.state != "done"]
        agents.sort(key=lambda a: (a.state == "done", -(a.elapsed_s or 0)))
        ids = {a.agent_id for a in agents}
        grid = self.base().query_one("#grid", Grid)
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
        self.base().query_one("#totals", Static).update(
            f"{t['agents_live'] + t['agents_stalled'] + t['agents_done']} agents "
            f"({t['agents_live']} live, {t['agents_stalled']} stalled, {t['agents_done']} done"
            + (f", [b red]{over} over budget[/]" if over else "") + ") · "
            f"{_tok(t['tokens'])} tok · ${_usd(t['cost_usd'])} · burn {_tok(rate)}/min{cost_h} · "
            f"project {escape(os.path.basename(self.project_dir.rstrip('/').rstrip(os.sep)))} · "
            f"filter: {'live' if self.live_only else 'all'} · ? help")

    def selected(self):
        sel = getattr(self.screen, "selected_agent", None)
        if sel is not None:
            return sel()
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
        if not isinstance(self.screen, (Analytics, PlansScreen)):
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
        self.push_screen(Notice("SCREENS: 1 Plans (default) · 2 All agents · 3 Context (inside: i index · s searches · m misses · g guard · "
                                "r restore · w week review, e saves it · o overview · Esc back; a red header = a context alert) · "
                                "Tab/a on Plans = All agents · Enter opens plan/agent · Esc/Backspace back\n"
                                "On a plan's agent list the keys below act on the selected agent. Up/down select.\n\n"
                                "arrows/tab select · Enter/t detail+troubleshoot · f live/all · r restart request · s stop request\n"
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
