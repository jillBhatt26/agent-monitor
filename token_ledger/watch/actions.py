"""Pure helpers for the watch console: timer colours, request files, brief extraction, detached launch."""
import datetime as dt
import json
import os
import shlex
import shutil
import subprocess

from ..compat import IS_MAC, IS_WIN, detached_kwargs, temp_dir

# Default request folder; the app passes the configured one (config [watch] request_dir).
REQUEST_DIR = os.path.expanduser("~/.local/share/token-ledger/agent-requests")
TMP_DIR = temp_dir()
DISMISS_FILE = os.path.expanduser("~/.local/state/token-ledger/watch-dismissed.json")


def timer_colour(elapsed_s, done=False):
    if done:
        return "grey50"
    if elapsed_s < 300:
        return "green"
    if elapsed_s <= 600:
        return "yellow"
    return "red"


def fmt_dur(sec):
    sec = max(0, int(sec or 0))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


WORKING_UNDER = 60
PULSE = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
PULSE_IDLE = "⠿"


def fmt_age(sec):
    """Compact age: 3s, 1m40s, 4m, 1h02m."""
    sec = max(0, int(sec or 0))
    if sec < 60:
        return f"{sec}s"
    m, s = divmod(sec, 60)
    if m < 60:
        return f"{m}m{s:02d}s" if s else f"{m}m"
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m"


def pulse_frame(now_ts, working):
    return PULSE[int(now_ts * 8) % len(PULSE)] if working else PULSE_IDLE


def clock(ts):
    e = to_epoch(ts)
    return "-" if e is None else dt.datetime.fromtimestamp(e).strftime("%H:%M")


def status_info(a, now_ts, stalled_after=180):
    """Health of an agent: (kind, colour, text). kind is working | waiting | stalled | finished."""
    last = to_epoch(a.last_activity)
    age = None if last is None else max(0.0, now_ts - last)
    src = getattr(a, "finish_source", "") or ""
    if a.state == "done":
        took = fmt_age(a.elapsed_s)
        txt = f"✓ FINISHED · {clock(a.ended or a.last_activity)} · took {took}"
        if src:
            txt += f" · {src}"
        return "finished", "grey50", txt
    pend = getattr(a, "pending_action", "") or ""
    run = f" · running: {pend}" if pend else ""
    if age is None:
        return "waiting", "yellow", "◐ WAITING · no activity time" + run
    if age < WORKING_UNDER:
        return "working", "green", f"● WORKING · active {fmt_age(age)} ago" + run
    if age > stalled_after:
        return "stalled", "red", f"⚠ STALLED · quiet {fmt_age(age)}" + run
    return "waiting", "yellow", f"◐ WAITING · quiet {fmt_age(age)}" + run


def status_block(a, now_ts, stalled_after=180):
    """Plain-text lines for the top of the detail view."""
    kind, _, txt = status_info(a, now_ts, stalled_after)
    last = to_epoch(a.last_activity)
    age = "-" if last is None else fmt_age(max(0, now_ts - last))
    out = [txt, f"started {clock(a.started)} · last activity {clock(a.last_activity)} ({age} ago)"]
    if kind == "finished":
        out.append(f"finished {clock(a.ended or a.last_activity)} · how we know: {a.finish_source or 'state'}")
    out.append("last actions:")
    acts = list(getattr(a, "recent_actions", []) or [])
    out += [f"  {clock(t)}  {p}" for t, p in acts] or ["  (none yet)"]
    if kind != "finished" and getattr(a, "pending_action", ""):
        out.append(f"current tool call (no result yet): {a.pending_action}")
    if kind == "finished":
        ft = getattr(a, "final_text", "") or ""
        out.append("final result:")
        out += [f"  {l}" for l in ft.splitlines()[:2]] or ["  (no final text)"]
    return out


def short_model(model):
    m = (model or "").lower()
    for key, name in (("opus", "Opus"), ("sonnet", "Sonnet"), ("haiku", "Haiku")):
        if key in m:
            return name
    return model or "?"


def request_path(agent_id, request_dir=REQUEST_DIR):
    return os.path.join(request_dir, f"{agent_id}.json")


def write_request(agent, action, request_dir=REQUEST_DIR, now=None):
    """The hook reads the brief from transcript_path, so the brief itself is never stored here."""
    if action not in ("restart", "stop"):
        raise ValueError(action)
    os.makedirs(request_dir, exist_ok=True)
    data = {
        "agent_id": agent.agent_id,
        "session_id": agent.session_id,
        "action": action,
        "agent_type": agent.agent_type,
        "model": agent.model,
        "description": agent.description,
        "transcript_path": agent.transcript_path,
        "requested_at": (now or dt.datetime.now(dt.timezone.utc)).isoformat(timespec="seconds"),
        "status": "pending",
    }
    path = request_path(agent.agent_id, request_dir)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)
    return path


def read_request_status(agent_id, request_dir=REQUEST_DIR):
    try:
        with open(request_path(agent_id, request_dir), encoding="utf-8") as f:
            d = json.load(f)
        return d.get("action", "?"), d.get("status", "pending")
    except (OSError, ValueError):
        return None


def extract_brief(transcript_path):
    """First user message of the agent transcript, unmasked (it is the brief the agent was given)."""
    try:
        with open(transcript_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                if o.get("type") != "user":
                    continue
                c = (o.get("message") or {}).get("content")
                if isinstance(c, str):
                    return c
                if isinstance(c, list):
                    parts = [b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text"]
                    if parts:
                        return "\n".join(parts)
    except OSError:
        pass
    return ""


def find_terminal():
    if IS_WIN:
        # Windows Terminal when present, else a plain new PowerShell console window.
        return "wt" if shutil.which("wt") else "powershell"
    if IS_MAC:
        return "osascript" if shutil.which("osascript") else None
    for name in ("gnome-terminal", "x-terminal-emulator", "konsole"):
        p = shutil.which(name)
        if p:
            return name
    return None


def _ps_quote(s):
    return "'" + str(s).replace("'", "''") + "'"


def build_run_command(model, brief_file, cwd):
    """Never adds --dangerously-skip-permissions: the project's guards and permission prompts stay in force.
    POSIX: a bash line. Windows: a PowerShell line."""
    m = model or "sonnet"
    if IS_WIN:
        return (f"Set-Location -LiteralPath {_ps_quote(cwd)}; claude -p --model {_ps_quote(m)} "
                f"(Get-Content -Raw -LiteralPath {_ps_quote(brief_file)}); Read-Host 'Done. Press Enter to close'")
    return (f'cd {shlex.quote(cwd)} && claude -p --model {shlex.quote(m)} "$(cat {shlex.quote(brief_file)})"; '
            f'echo; read -p "Done. Press Enter to close." _')


def terminal_argv(terminal, inner):
    if terminal == "wt":
        return ["wt", "new-tab", "powershell", "-NoProfile", "-Command", inner]
    if terminal == "powershell":
        return ["powershell", "-NoProfile", "-Command", inner]
    if terminal == "osascript":
        script = inner.replace("\\", "\\\\").replace('"', '\\"')
        return ["osascript", "-e", f'tell application "Terminal" to do script "{script}"']
    if terminal == "gnome-terminal":
        return ["gnome-terminal", "--", "bash", "-lc", inner]
    if terminal == "konsole":
        return ["konsole", "-e", "bash", "-lc", inner]
    return [terminal, "-e", "bash", "-lc", inner]


def run_separately(agent, cwd, tmp_dir=TMP_DIR, launch=True):
    """Returns (ok, command_text). Writes the brief to a temp file; opens a terminal when one exists."""
    brief = extract_brief(agent.transcript_path)
    if not brief:
        return False, "no brief found in the transcript"
    os.makedirs(tmp_dir, exist_ok=True)
    bf = os.path.join(tmp_dir, f"{agent.agent_id}.txt")
    with open(bf, "w", encoding="utf-8") as f:
        f.write(brief)
    os.chmod(bf, 0o600)
    inner = build_run_command(agent.model, bf, cwd)
    term = find_terminal()
    if not term or not launch:
        return False, inner
    kw = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if IS_WIN and term == "powershell":
        kw["creationflags"] = getattr(subprocess, "CREATE_NEW_CONSOLE", 0x10)  # its own visible window
    else:
        kw.update(detached_kwargs())
    subprocess.Popen(terminal_argv(term, inner), **kw)
    return True, inner


def to_epoch(ts):
    if ts is None or ts == "":
        return None
    if isinstance(ts, (int, float)):
        return float(ts)
    if isinstance(ts, dt.datetime):
        return (ts if ts.tzinfo else ts.replace(tzinfo=dt.timezone.utc)).timestamp()
    try:
        return dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def idle_seconds(last_activity, now_ts):
    e = to_epoch(last_activity)
    return None if e is None else max(0, int(now_ts - e))


def hints(a, now_ts):
    """Troubleshoot lines for the detail screen."""
    out = []
    idle = idle_seconds(a.last_activity, now_ts)
    if a.state == "stalled":
        mins = f"{idle // 60}m" if idle is not None else "a while"
        out.append(f"waiting on tool for {mins} - maybe permission prompt or long Bash")
        out.append("Finished but shown as stalled? press d")
    if a.repeat_hint:
        out.append(f"same call repeated: {a.repeat_hint} - likely loop")
    if a.last_error:
        out.append(f"last error: {a.last_error}")
    if getattr(a, "over_budget", False):
        out.append(f"context peaked at {fmt_k(a.peak_context)}, over its {fmt_k(a.budget)} budget - "
                   "give it exact files/lines instead of 'explore', or split the brief")
    if not out:
        out.append("no problems detected")
    return out


def fmt_k(n):
    n = int(n or 0)
    return f"{n / 1e6:.1f}M" if n >= 1_000_000 else f"{round(n / 1000)}k" if n >= 1000 else str(n)


def ctx_level(ctx, save=120000, compact=160000):
    """(colour, word) for a main session's context size: ok < save <= checkpoint < compact <= compact now."""
    ctx = int(ctx or 0)
    if compact and ctx >= compact:
        return "red", "COMPACT NOW"
    if save and ctx >= save:
        return "yellow", "checkpoint"
    return "green", "ok"


def ctx_bar(ctx, limit, width=12):
    """Text gauge of context against `limit`, e.g. ███████░░░░░ 58%."""
    if not limit:
        return ""
    frac = max(0.0, min(1.0, (ctx or 0) / limit))
    full = round(frac * width)
    return "█" * full + "░" * (width - full) + f" {round((ctx or 0) / limit * 100)}%"


def pending_requests(session_id, request_dir=REQUEST_DIR):
    """Restart/stop requests the monitor wrote for agents of this session that the session has not been told about."""
    out = []
    try:
        names = sorted(os.listdir(request_dir))
    except OSError:
        return out
    for n in names:
        if not n.endswith(".json"):
            continue
        p = os.path.join(request_dir, n)
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            continue
        if d.get("status") == "pending" and (not session_id or d.get("session_id") == session_id):
            out.append((p, d))
    return out


def request_context(reqs):
    """Instruction text for the main session (UserPromptSubmit additionalContext) covering every pending request."""
    lines = ["AGENT MONITOR REQUESTS (sent by the owner from the agent monitor; act on them before anything else):"]
    for _, d in reqs:
        who = f"{d.get('agent_type')} agent {str(d.get('agent_id'))[:8]} ('{d.get('description') or '-'}')"
        if d.get("action") == "stop":
            lines.append(f"- STOP {who}: stop it with TaskStop if it is still running, then tell the owner in one line.")
        else:
            lines.append(f"- RESTART {who}: stop it if still running, then start the same agent type with the same brief "
                         f"(the first user message of {d.get('transcript_path')}); tell the owner in one line.")
    return "\n".join(lines)


def mark_requests(reqs, status="delivered", now=None):
    for p, d in reqs:
        d["status"] = status
        d["delivered_at"] = (now or dt.datetime.now(dt.timezone.utc)).isoformat(timespec="seconds")
        tmp = p + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(d, f, indent=2)
            os.replace(tmp, p)
        except OSError:
            pass


def load_dismissed(path=DISMISS_FILE):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return {x for x in d if isinstance(x, str)} if isinstance(d, list) else set()
    except (OSError, ValueError):
        return set()


def toggle_dismissed(agent_id, path=DISMISS_FILE):
    """Flip the agent in the local dismiss list; returns True when it is now dismissed. Transcripts are never touched."""
    cur = load_dismissed(path)
    now_dismissed = agent_id not in cur
    cur.symmetric_difference_update({agent_id})
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(sorted(cur), f)
    os.replace(tmp, path)
    return now_dismissed
