"""Data for the Plans screens of `tokens watch`. No Textual here, so it can be dumped as text and tested."""
import json
import os
import re
import statistics
import time
from datetime import datetime

from ..board import events as board_events
from ..board import plans as board_plans

TASKS_FILE = board_events.AGENT_TASKS
GROUPS = ["Planned", "In execution", "Paused", "Done", "System", "Unsorted"]
UNSORTED = "unsorted"


def load_tasks(path=TASKS_FILE):
    """agent_id -> latest tag row. Missing or broken file means no tags."""
    out = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if isinstance(r, dict) and r.get("agent_id"):
                    out[str(r["agent_id"]).removeprefix("agent-")] = r
    except OSError:
        pass
    return out


def agent_plan(a, tasks):
    """Plan number the agent was tagged with, or None (Unsorted)."""
    r = tasks.get(str(a.agent_id).removeprefix("agent-"))
    p = r.get("plan") if r else None
    return str(p) if p else None


def agent_task(a, tasks):
    r = tasks.get(str(a.agent_id).removeprefix("agent-"))
    return (r or {}).get("task") or ""


_ctx = {"at": 0.0, "val": None}
_rec = {"at": 0.0}
CTX_TTL_S = 10
RECORD_EVERY_S = 60


def _eta_context(now):
    from ..board import eta
    if _ctx["val"] is None or now - _ctx["at"] > CTX_TTL_S:
        _ctx["val"] = eta.load_context(board_plans.load_plans(), now)
        _ctx["at"] = now
    return eta, _ctx["val"]


def eta_label(plan, now=None):
    try:
        eta, ctx = _eta_context(time.time())
        return str(eta.estimate_with_context(plan, ctx, now or time.time()).label) or "–"
    except Exception:  # noqa: BLE001 - ETA is optional
        return "–"


BUDGET_RE = re.compile(r"~?\s*(\d+)\s*min", re.I)
DEFAULT_BUDGET_MIN = 10


def parse_budget_min(text):
    """Minutes from a brief's 'Budget: max ~15 min ...' line (or the first '~N min'); None if absent."""
    text = text or ""
    m = re.search(r"budget[^\n]*", text, re.I)
    for chunk in ((m.group(0),) if m else ()) + (text,):
        b = BUDGET_RE.search(chunk)
        if b:
            return int(b.group(1))
    return None


def agent_budget_min(a, tasks):
    """Budget in minutes: hook-recorded budget_min first, then the brief in the agent's transcript."""
    r = tasks.get(str(a.agent_id).removeprefix("agent-")) or {}
    try:
        if r.get("budget_min"):
            return int(r["budget_min"])
    except (TypeError, ValueError):
        pass
    return parse_budget_min(getattr(a, "brief", ""))


def _elapsed_s(a, now):
    st = getattr(a, "started", None)
    if st is not None:
        try:
            return max((datetime.fromtimestamp(now, st.tzinfo) - st).total_seconds(), 0)
        except (TypeError, ValueError, OverflowError):
            pass
    return a.elapsed_s or 0


def agent_remaining_s(a, tasks, done, now):
    """Seconds left for one live agent (negative = over budget). No budget: median of today's finished agents, else 10 min."""
    b = agent_budget_min(a, tasks)
    if b is None:
        durs = [d.elapsed_s for d in done if d.elapsed_s]
        b = statistics.median(durs) / 60 if durs else DEFAULT_BUDGET_MIN
    return b * 60 - _elapsed_s(a, now)


def plan_live_eta(live, done, tasks, now=None):
    """Label for a plan's active agents: the largest remaining time (they run in parallel); '–' with none active."""
    if not live:
        return "–"
    now = time.time() if now is None else now
    rem = max(agent_remaining_s(a, tasks, done, now) for a in live)
    if rem < 0:
        return f"+{max(round(-rem / 60), 1)}m over"
    m = max(round(rem / 60), 1)
    return f"{m // 60}h {m % 60:02d}m" if m >= 60 else f"{m}m"


def record_board_changes(now=None):
    """Append plan-change events, at most once a minute; never raises."""
    now = time.time() if now is None else now
    if now - _rec["at"] < RECORD_EVERY_S:
        return
    _rec["at"] = now
    try:
        board_events.record_changes(board_plans.load_plans(), now)
    except Exception:  # noqa: BLE001 - the UI must keep going
        pass


def pct(v):
    return "–" if v is None else f"{v}%"


def visible_plans(plans=None):
    return [p for p in (board_plans.load_plans() if plans is None else plans) if not p.guessed]


def plan_agents(snap, tasks, plan_id, today=None):
    """(live, finished_today) agents for a plan; plan_id None means Unsorted."""
    today = today or datetime.now().date()
    live, done = [], []
    for a in snap.agents if snap else []:
        pid = agent_plan(a, tasks)
        if plan_id == UNSORTED:
            if pid is not None:
                continue
        elif pid != plan_id:
            continue
        if a.state != "done":
            live.append(a)
        elif a.last_activity.astimezone().date() == today:
            done.append(a)
    live.sort(key=lambda a: -(a.elapsed_s or 0))
    done.sort(key=lambda a: a.last_activity, reverse=True)
    return live, done


def group_rows(snap, tasks, plans=None, keep_empty=False):
    """[(group, [row dict])] in display order; empty groups are left out unless keep_empty. Row keys: id, plan, title, phase, state, local, stage, live, eta, next."""
    plans = visible_plans(plans)
    by = {g: [] for g in GROUPS}
    for p in plans:
        by[board_plans.phase_column(p)].append(p)
    by["Done"].sort(key=lambda p: p.updated or datetime.min, reverse=True)
    by["Done"] = by["Done"][:5]
    out = []
    for g in GROUPS:
        rows = []
        for p in by[g]:
            live, done = plan_agents(snap, tasks, p.plan)
            rows.append({"id": p.plan, "plan": p.plan, "title": p.title, "phase": p.phase, "state": p.state,
                         "local": pct(p.local_pct), "stage": pct(p.stage_pct), "live": len(live) + len(done), "live_now": len(live),
                         "eta": eta_label(p), "agent_eta": plan_live_eta(live, done, tasks), "next": p.next, "obj": p})
        if g == "Unsorted":
            live, done = plan_agents(snap, tasks, UNSORTED)
            rows = [{"id": UNSORTED, "plan": "–", "title": "untagged agents", "phase": "", "state": "", "local": "–",
                     "stage": "–", "live": len(live) + len(done), "live_now": len(live), "eta": "–", "agent_eta": plan_live_eta(live, done, tasks), "next": "", "obj": None}] if live or done else []
        if rows or (keep_empty and g != "Unsorted"):
            out.append((g, rows))
    return out


def _clip(t, width):
    return t if width <= 0 or len(t) <= width else t[:max(width - 1, 1)] + "…"


def card_lines(r, width=0):
    """The three plain-text lines of a board card (width 0 = no truncation)."""
    head = f"{r['plan']} {r['title']}" if r["plan"] != "–" else r["title"]
    if r.get("live_now"):
        head = "● " + head
    bits = [b for b in (r["phase"], f"Local {r['local']}" if r["local"] != "–" else "",
                        f"Stage {r['stage']}" if r["stage"] != "–" else "") if b]
    third = f"active {r.get('live_now', 0)} · ETA {r.get('agent_eta') or '–'}"
    return [_clip(head, width), _clip(" · ".join(bits) or "–", width), _clip(third, width)]


def row_text(r):
    return (f"{r['plan']:<16} {r['title'][:28]:<28} {r['phase']:<10} L={r['local']:<5} S={r['stage']:<5} "
            f"agents {r['live']:<2} ETA {r['eta']}")


def dump_text(snap, tasks, plans=None):
    lines = ["Plans"]
    for g, rows in group_rows(snap, tasks, plans):
        lines.append(f"\n== {g} ({len(rows)})")
        lines += ["  " + row_text(r) for r in rows]
    return "\n".join(lines)


def tasks_for_plan(plan_id, tasks, snap):
    """[(task slug, started datetime or None, duration seconds or None)] for a plan, newest first."""
    by_id = {str(a.agent_id).removeprefix("agent-"): a for a in (snap.agents if snap else [])}
    out = []
    for aid, r in tasks.items():
        if str(r.get("plan") or "") != plan_id:
            continue
        a = by_id.get(aid)
        started = a.started if a else None
        out.append((r.get("task") or aid[:8], started, a.elapsed_s if a else None, r.get("ts", "")))
    out.sort(key=lambda t: t[3], reverse=True)
    return [(t[0], t[1], t[2]) for t in out]


def find_plan(plan_id):
    return next((p for p in visible_plans() if p.plan == plan_id), None)


def dump_main(project_dir=None, stalled_after=180, since_hours=24):
    from . import actions, reader
    snap = reader.scan(project_dir=project_dir or reader.detect_project_dir(), stalled_after=stalled_after,
                       since_hours=since_hours, dismissed=actions.load_dismissed(),
                       plans_dir=board_plans.DEFAULT_PLANS_DIR)
    return dump_text(snap, load_tasks())
