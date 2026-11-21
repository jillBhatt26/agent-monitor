"""Board change events: diff plans against a snapshot and append what changed. Stdlib only."""
import glob
import json
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional

from . import project_slug, workspace

_WS = workspace()
STATE_DIR = os.path.join(_WS, "tools", "state")
SNAPSHOT = os.path.join(STATE_DIR, "board-snapshot.json")
EVENTS = os.path.join(STATE_DIR, "board-events.jsonl")
AGENT_TASKS = os.path.join(STATE_DIR, "agent-tasks.jsonl")
PROJECT_DIR = os.path.join("~", ".claude", "projects", project_slug(_WS))


def parse_ts(s) -> Optional[float]:
    """ISO string (with Z / offset, or naive UTC) to epoch seconds."""
    if not s:
        return None
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.timestamp()


def iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="seconds")


def _snap_of(plan) -> dict:
    return {
        "phase": plan.phase,
        "state": plan.state,
        "local": {i.n: i.local for i in plan.checklist},
        "stage": {i.n: i.stage for i in plan.checklist},
    }


def _load(path: str) -> Optional[dict]:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def diff(old: dict, new: dict, plan_id: str, ts: str) -> List[dict]:
    out: List[dict] = []

    def ev(kind, frm, to, item=""):
        out.append({"ts": ts, "plan": plan_id, "kind": kind, "from": frm, "to": to, "item": item})

    if old.get("phase") != new["phase"]:
        ev("phase", old.get("phase"), new["phase"])
    if old.get("state") != new["state"]:
        ev("state", old.get("state"), new["state"])
    for lane in ("local", "stage"):
        before, after = old.get(lane, {}), new[lane]
        for n, val in after.items():
            if n not in before:
                continue
            was = before[n]
            if val is True and was is not True:
                ev("tick_" + lane, was, True, n)
            elif was is True and val is False:
                ev("untick", lane, False, n)
    return out


def record_changes(plans, now: Optional[float] = None, snapshot_path: str = SNAPSHOT,
                   events_path: str = EVENTS) -> List[dict]:
    """Append events for changes since the last snapshot; the first run only seeds it."""
    import time
    ts = iso(now if now is not None else time.time())
    old = _load(snapshot_path)
    new = {p.plan: _snap_of(p) for p in plans}
    emitted: List[dict] = []
    if old is not None:
        for pid, snap in new.items():
            if pid in old:  # a plan seen for the first time is seeded silently
                emitted.extend(diff(old[pid], snap, pid, ts))
    if emitted:
        os.makedirs(os.path.dirname(events_path), exist_ok=True)
        with open(events_path, "a", encoding="utf-8") as f:
            for e in emitted:
                f.write(json.dumps(e) + "\n")
    if old != new:
        os.makedirs(os.path.dirname(snapshot_path), exist_ok=True)
        tmp = snapshot_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(new, f, indent=1, sort_keys=True)
        os.replace(tmp, snapshot_path)
    return emitted


def load_events(events_path: str = EVENTS) -> List[dict]:
    out = []
    try:
        with open(events_path, encoding="utf-8") as f:
            for ln in f:
                try:
                    e = json.loads(ln)
                except ValueError:
                    continue
                t = parse_ts(e.get("ts"))
                if t is not None:
                    e["t"] = t
                    out.append(e)
    except OSError:
        pass
    return out


def agent_spans(tasks_path: str = AGENT_TASKS, project_dir: str = PROJECT_DIR) -> Dict[str, List[dict]]:
    """Per plan: one span per launched agent, {start, end, agent_id}; end is the transcript's last write.

    The mtime of the transcript file is the time of the agent's last message.
    """
    spans: Dict[str, List[dict]] = {}
    root = os.path.expanduser(project_dir)
    try:
        with open(tasks_path, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return spans
    for ln in lines:
        try:
            d = json.loads(ln)
        except ValueError:
            continue
        start, aid, plan = parse_ts(d.get("ts")), d.get("agent_id"), d.get("plan")
        if start is None or not aid or not plan:
            continue
        end = start
        for p in glob.glob(os.path.join(glob.escape(root), "*", "subagents", "agent-%s.jsonl" % aid)):
            try:
                end = max(end, os.path.getmtime(p))
            except OSError:
                pass
        spans.setdefault(str(plan), []).append({"start": start, "end": end, "agent_id": aid})
    return spans
