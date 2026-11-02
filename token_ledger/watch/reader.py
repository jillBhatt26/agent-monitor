"""Read-only incremental scanner of Claude Code transcripts."""
import glob
import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .. import prices as _prices
from ..compat import slug  # noqa: F401  (re-exported: callers use reader.slug)

KIND_MAP = {
    "builder": "implementation", "caveman:cavecrew-builder": "implementation",
    "test-runner": "testing", "test-judge": "testing",
    "git-scout": "git ops", "aws-reader": "cloud ops", "release-runner": "deploy",
    "wiki-syncer": "docs", "dev-manager": "dev env",
    "Explore": "research", "caveman:cavecrew-investigator": "research", "Plan": "research",
    "preflight-reviewer": "review", "caveman:cavecrew-reviewer": "review",
    "general-purpose": "general", "fork": "general", "claude": "general",
    "claude-code-guide": "general",
}

GENERIC_TYPES = ("general-purpose", "claude", "fork")
# First matching row wins; generic agents only (named agent types keep KIND_MAP).
KEYWORD_KINDS = (
    (r"\b(test\w*|browser|playwright|e2e)\b", "testing"),
    (r"\b(sync|git|commit|branch|pr)\b", "git ops"),
    (r"\b(aws|server|srv|ssm)\b", "cloud ops"),
    (r"\b(wiki|docs?|tutorials?|readme)\b", "docs"),
    (r"\b(review\w*|audit\w*)\b", "review"),
    (r"\b(research\w*|requirements?|investigat\w*)\b", "research"),
    (r"\b(build\w*|fix\w*|implement\w*)\b", "implementation"),
)
KIND_ICON = {"testing": "🧪", "implementation": "🔨", "git ops": "🌿", "cloud ops": "☁️", "docs": "📝",
             "review": "🔍", "research": "🔎", "deploy": "🚀", "dev env": "🖥️", "general": "⚙️"}
_PLAN_RE = re.compile(r"\b(0\d\d)\b")
_plan_cache = {}

DEFAULT_DIR = None  # None = the most recently used Claude Code project
NO_META_DONE_AFTER = 1800
UNKNOWN_AFTER = 3600  # no `finished` and quiet this long: probably done, but we cannot tell
FINAL_STOPS = ("end_turn", "stop_sequence")
PROJECTS_ROOT = "~/.claude/projects"


def infer_kind(agent_type, description):
    """Readable kind; generic agents are classified from keywords in their description."""
    kind = KIND_MAP.get(agent_type, agent_type)
    if agent_type in GENERIC_TYPES:
        for rx, k in KEYWORD_KINDS:
            if re.search(rx, description or "", re.I):
                return k
    return kind


def kind_label(kind):
    return "%s %s" % (KIND_ICON.get(kind, "•"), kind)


def plan_title(num, plans_dir=None):
    """Human title of wiki plan `num`, or None when the folder or plan is missing."""
    d = plans_dir or os.environ.get("TOKENS_WATCH_PLANS_DIR")
    if not d:
        return None
    d = os.path.expanduser(d)
    key = (d, num)
    if key in _plan_cache:
        return _plan_cache[key]
    title = None
    try:
        hits = sorted(glob.glob(os.path.join(glob.escape(d), "%s-*.md" % num)))
    except OSError:
        hits = []
    if hits:
        fp = hits[0]
        try:
            with open(fp, encoding="utf-8", errors="replace") as f:
                for i, line in enumerate(f):
                    if i > 20:
                        break
                    if line.startswith("# "):
                        title = re.sub(r"^\s*Plan\s+%s\s*[—–:-]*\s*" % num, "", line[2:].strip(), flags=re.I) or None
                        break
        except OSError:
            pass
        if not title:
            title = os.path.basename(fp)[len(num) + 1:-3].replace("-", " ").replace("_", " ")
    if os.path.isdir(d):  # a missing dir is not cached, so a later mount is picked up
        _plan_cache[key] = title
    return title


def plan_line(description, brief, plans_dir=None):
    first = (brief or "").strip().splitlines()[0] if (brief or "").strip() else ""
    for text in (description or "", first):
        for m in _PLAN_RE.finditer(text):
            t = plan_title(m.group(1), plans_dir)
            if t:
                return "Plan %s · %s" % (m.group(1), t)
    return ""


def friendly_action(name, inp):
    """Short plain phrase for a tool call."""
    inp = inp if isinstance(inp, dict) else {}
    home = os.path.expanduser("~")

    def base(k="file_path"):
        v = inp.get(k)
        return os.path.basename(v) if isinstance(v, str) and v else "file"

    if name == "Bash":
        cmd = _flat(inp.get("command") or "").replace(home, "~")
        return "running command: " + cmd[:40]
    if name in ("Read", "NotebookRead"):
        return "reading " + base()
    if name in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        return "editing " + base()
    if name in ("Grep", "Glob"):
        return "searching"
    if name in ("Agent", "Task"):
        return "started helper: " + _flat(inp.get("description") or inp.get("subagent_type") or "")[:40]
    if name == "WebFetch":
        m = re.match(r"\w+://([^/\s]+)", str(inp.get("url") or ""))
        return "reading web page " + (m.group(1) if m else "")
    if name == "WebSearch":
        return "searching the web"
    return "using " + str(name)


def newest_project_dir(projects_root=PROJECTS_ROOT):
    """The Claude Code project folder with the most recently written transcript, or None."""
    root = os.path.expanduser(projects_root)
    best, best_t = None, -1.0
    for d in glob.glob(os.path.join(root, "*")):
        for fp in glob.glob(os.path.join(d, "*.jsonl")):
            try:
                t = os.path.getmtime(fp)
            except OSError:
                continue
            if t > best_t:
                best, best_t = d, t
    return best


def detect_project_dir(cwd=None, projects_root=PROJECTS_ROOT, default=DEFAULT_DIR):
    """Claude Code's project folder for cwd, trying parent folders too. When none exists: `default`, else the most
    recently used project."""
    root = os.path.expanduser(projects_root)
    path = os.path.abspath(cwd or os.getcwd())
    while True:
        cand = os.path.join(root, slug(path))
        if os.path.isdir(cand):
            return cand
        parent = os.path.dirname(path)
        if parent == path:
            return os.path.expanduser(default) if default else newest_project_dir(projects_root)
        path = parent


def default_plans_dir(project_dir):
    """<project's real folder>/wiki/plans when it exists (plan numbers in agent descriptions become titles)."""
    cwd = project_cwd(project_dir) if project_dir else None
    d = os.path.join(cwd, "wiki", "plans") if cwd else None
    return d if d and os.path.isdir(d) else None


def budget_for(model, budgets):
    """Context budget (tokens) for an agent's model family; None when budgets are off."""
    if not budgets:
        return None
    m = str(model or "").lower()
    for fam in ("opus", "sonnet", "haiku", "fable"):
        if fam in m and budgets.get(fam):
            return int(budgets[fam])
    return int(budgets["default"]) if budgets.get("default") else None


def project_cwd(project_dir):
    """Real working directory of a project, read from the `cwd` field of a transcript line; None if unknown."""
    files = sorted(glob.glob(os.path.join(os.path.expanduser(project_dir), "*.jsonl")), key=os.path.getmtime, reverse=True)
    for fp in files[:3]:
        try:
            with open(fp, encoding="utf-8", errors="replace") as f:
                for i, line in enumerate(f):
                    if i > 50:
                        break
                    try:
                        c = json.loads(line).get("cwd")
                    except ValueError:
                        continue
                    if isinstance(c, str) and os.path.isdir(c):
                        return c
        except OSError:
            continue
    return None

_SECRET_KV = re.compile(
    r"\b([A-Za-z0-9_.-]*(?:KEY|TOKEN|SECRET|PASSWORD|PASS|AUTH|WEBHOOK)[A-Za-z0-9_.-]*)=(\"[^\"]*\"|'[^']*'|\S+)",
    re.I)
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_LONG = re.compile(r"[A-Za-z0-9+/_=-]{32,}")


def mask(text):
    if not text:
        return text
    text = _SECRET_KV.sub(lambda m: m.group(1) + "=***", text)
    text = _EMAIL.sub("***@***", text)
    # Paths and plain words are long too; only mask strings that mix digits with letters.
    return _LONG.sub(lambda m: "***" if re.search(r"\d", m.group(0)) and re.search(r"[A-Za-z]", m.group(0)) else m.group(0), text)


@dataclass
class AgentView:
    agent_id: str
    session_id: str
    agent_type: str
    kind: str
    description: str
    model: object
    effort: object
    background: bool
    spawn_depth: int
    started: datetime
    last_activity: datetime
    finished: bool
    ended: object
    state: str
    tool_calls: int
    last_tool: object
    last_error: object
    tokens: dict
    cost_usd: object
    cache_hit_pct: object
    brief: str
    repeat_hint: object
    transcript_path: str
    elapsed_s: float
    done_note: str = ""  # "" | "done?" (inferred from silence) | "dismissed"
    last_action: str = ""  # plain-language phrase for last_tool
    plan: str = ""  # "Plan 047 · title" when the description or brief names a known plan
    finish_source: str = ""  # how we know it finished: meta finished | ended turn | dismissed | done? (quiet >60 min) | quiet >30 min
    pending_action: str = ""  # phrase of the tool call that has no result yet
    recent_actions: list = field(default_factory=list)  # last 3 (timestamp, phrase), oldest first
    final_text: str = ""  # first 2 lines of the last assistant text, masked
    outcome: str = "running"  # success | failed | running | stopped | unknown (see outcome_of)
    context_tokens: int = 0  # context of the latest call (what the next call re-sends)
    peak_context: int = 0  # largest context seen in this run
    budget: object = None  # context budget for its model family (tokens), None = off
    over_budget: bool = False  # peak context went over the budget


@dataclass
class MainView:
    session_id: str
    title: str
    model: object
    effort: object
    started: datetime
    last_activity: datetime
    context_tokens: int
    tokens: dict
    cost_usd: object
    last_text: str
    transcript_path: str
    elapsed_s: float
    peak_context: int = 0


@dataclass
class Snapshot:
    mains: list
    agents: list
    totals: dict
    taken_at: datetime


@dataclass
class _State:
    offset: int = 0
    first_ts: object = None
    last_ts: object = None
    model: object = None
    effort: object = None
    brief: str = ""
    msgs: dict = field(default_factory=dict)  # message id -> (model, usage)
    tool_ids: set = field(default_factory=set)
    last_tool: object = None
    last_action: str = ""
    last_error: object = None
    last_text: str = ""
    last_sig: object = None
    run: int = 0
    hint: object = None
    timeline: list = field(default_factory=list)
    turn_by_msg: dict = field(default_factory=dict)
    turn_by_tool: dict = field(default_factory=dict)
    context_tokens: int = 0
    peak_context: int = 0
    anon: int = 0
    pending: dict = field(default_factory=dict)  # tool_use id -> phrase, until its result arrives
    recent: list = field(default_factory=list)
    final_text: str = ""
    tail_error: bool = False  # the newest transcript line is an errored tool result or an API error
    final: bool = False  # last assistant line ended the turn with no tool_use and nothing followed it


_cache = {}
_plans_default = {}


def _recent_tokens(st, since_epoch):
    """Tokens of this transcript's calls made after `since_epoch` (for the burn rate)."""
    n = 0
    for t in reversed(st.timeline):
        ts = t.get("ts")
        if ts is None or ts.timestamp() < since_epoch:
            break
        n += int(t.get("tokens") or 0)
    return n


def _ts(s):
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _short_arg(inp):
    if not isinstance(inp, dict):
        return ""
    for k in ("command", "file_path", "path", "pattern", "url", "description", "prompt", "query"):
        if isinstance(inp.get(k), str):
            return inp[k]
    for v in inp.values():
        if isinstance(v, str):
            return v
    return ""


def _flat(s):
    return " ".join(str(s).split())


def _text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content
                        if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _ingest(st, d):
    if not isinstance(d, dict):
        return
    t = d.get("type")
    ts = _ts(d.get("timestamp")) if d.get("timestamp") else None
    if t not in ("user", "assistant"):
        return
    if ts:
        st.first_ts = st.first_ts or ts
        st.last_ts = ts
    msg = d.get("message") if isinstance(d.get("message"), dict) else {}
    content = msg.get("content")
    if t == "user":
        st.final = False
        st.tail_error = False
        if isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    st.pending.pop(b.get("tool_use_id"), None)
                    if b.get("is_error"):
                        st.tail_error = True
                        st.last_error = mask(_flat(_text_of(b.get("content")) or "error"))[:300]
                        i = st.turn_by_tool.get(b.get("tool_use_id"))
                        if i is not None:
                            st.timeline[i]["error"] = True
        elif not st.brief:
            st.brief = mask(_text_of(content))[:2000]
        if not st.brief and isinstance(content, list):
            txt = _text_of(content)
            if txt and not any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
                st.brief = mask(txt)[:2000]
        return
    # assistant
    st.tail_error = bool(d.get("isApiErrorMessage") or d.get("error"))
    mid = msg.get("id")
    if not mid:
        st.anon += 1
        mid = "anon%d" % st.anon
    model = d.get("requestedModel") or msg.get("model")
    if msg.get("model") and not str(msg["model"]).startswith("<"):
        model = msg["model"]
    if model and not str(model).startswith("<"):
        st.model = model
    if d.get("effort"):
        st.effort = d["effort"]
    usage = msg.get("usage") if isinstance(msg.get("usage"), dict) else {}
    st.msgs[mid] = (model or st.model, usage)
    u = _norm(usage)
    st.context_tokens = u["input"] + u["cache_read"] + u["cache_write"]
    st.peak_context = max(st.peak_context, st.context_tokens)
    ti = st.turn_by_msg.get(mid)
    if ti is None:
        ti = len(st.timeline)
        st.turn_by_msg[mid] = ti
        st.timeline.append({"ts": ts, "tool": None, "tokens": 0, "error": False})
    turn = st.timeline[ti]
    turn["tokens"] = sum(u.values())
    st.final = (msg.get("stop_reason") in FINAL_STOPS
                and not any(isinstance(b, dict) and b.get("type") == "tool_use"
                            for b in (content if isinstance(content, list) else [])))
    for b in content if isinstance(content, list) else []:
        if not isinstance(b, dict):
            continue
        if b.get("type") == "text" and b.get("text", "").strip():
            st.last_text = mask(_flat(b["text"]))[:200]
            lines = [l.strip() for l in b["text"].splitlines() if l.strip()][:2]
            st.final_text = mask("\n".join(l[:160] for l in lines))
        elif b.get("type") == "tool_use":
            bid = b.get("id") or ("t%d" % len(st.tool_ids))
            if bid in st.tool_ids:
                continue
            st.tool_ids.add(bid)
            name = b.get("name") or "?"
            inp = b.get("input")
            turn["tool"] = name
            st.turn_by_tool[bid] = ti
            st.last_action = mask(friendly_action(name, inp))[:80]
            st.pending[bid] = st.last_action
            st.recent = (st.recent + [(ts, st.last_action)])[-3:]
            st.last_tool = mask("%s: %s" % (name, _flat(_short_arg(inp))))[:80].rstrip(": ")
            try:
                sig = (name, json.dumps(inp, sort_keys=True, default=str))
            except Exception:
                sig = (name, repr(inp))
            if sig == st.last_sig:
                st.run += 1
            else:
                st.last_sig, st.run = sig, 1
            if st.run >= 3:
                st.hint = "%s x%d same input" % (name, st.run)
            elif st.run == 1:
                st.hint = None


def outcome_of(st, finished, note):
    """How an agent ended, from its transcript tail and meta only. TaskStop kills leave no mark we can read
    (they look like an interrupted run, so FAILED); that is the one guess here."""
    if note == "dismissed":
        return "stopped"
    if not finished:
        return "running"
    if note == "done?":
        return "unknown"
    return "success" if st.final and not st.tail_error else "failed"


def _norm(usage):
    g = lambda k: int(usage.get(k) or 0) if isinstance(usage, dict) else 0
    return {"input": g("input_tokens"), "output": g("output_tokens"),
            "cache_read": g("cache_read_input_tokens"), "cache_write": g("cache_creation_input_tokens")}


def _read(path):
    """Advance the cached state for `path` by whatever bytes were appended."""
    st = _cache.get(path)
    try:
        size = os.path.getsize(path)
    except OSError:
        return None
    if st is None or size < st.offset:
        st = _cache[path] = _State()
    if size > st.offset:
        try:
            with open(path, "rb") as f:
                f.seek(st.offset)
                data = f.read(size - st.offset)
        except OSError:
            return st
        end = data.rfind(b"\n")
        if end >= 0:
            for line in data[:end].split(b"\n"):
                if line.strip():
                    try:
                        _ingest(st, json.loads(line))
                    except Exception:
                        pass
            st.offset += end + 1
    return st


def _totals(st):
    tok = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    cost, known = 0.0, False
    try:
        pr = _prices.load()
    except Exception:
        pr = None
    for model, usage in st.msgs.values():
        u = _norm(usage)
        for k in tok:
            tok[k] += u[k]
        if pr and model:
            try:
                c = _prices.cost_usd(pr, model, usage.get("speed") if isinstance(usage, dict) else None,
                                     u["input"], u["cache_write"], 0, u["cache_read"], u["output"])
            except Exception:
                c = None
            if c is not None:
                cost += c
                known = True
    tok["total"] = sum(tok.values())
    return tok, (cost if known else None)


def _load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _msum(a, b):
    return {k: a.get(k, 0) + b.get(k, 0) for k in ("input", "output", "cache_read", "cache_write", "total")}


def scan(project_dir=DEFAULT_DIR, stalled_after=180, since_hours=24, dismissed=(), plans_dir=None, budgets=None,
         burn_minutes=10):
    project_dir = os.path.expanduser(project_dir or newest_project_dir() or "")
    if plans_dir is None:
        plans_dir = _plans_default.setdefault(project_dir, default_plans_dir(project_dir))
    burn_from = time.time() - burn_minutes * 60
    burn = 0
    now = datetime.now(timezone.utc)
    nowt = time.time()
    cutoff = nowt - since_hours * 3600
    mains, agents = [], []
    tot = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "total": 0}
    cost_sum, cost_known = 0.0, False
    for mp in glob.glob(os.path.join(project_dir, "*.jsonl")):
        sid = os.path.basename(mp)[:-6]
        try:
            if os.path.getmtime(mp) < cutoff:
                continue
        except OSError:
            continue
        st = _read(mp)
        if st is None:
            continue
        tok, cost = _totals(st)
        if not tok["total"] or not st.model:
            continue  # stub session (resumed/compacted) with no real assistant turn
        started = st.first_ts or datetime.fromtimestamp(os.path.getmtime(mp), timezone.utc)
        last = st.last_ts or started
        title = _load_json(os.path.join(project_dir, sid, "custom-title.json")).get("customTitle", "")
        mains.append(MainView(sid, title if isinstance(title, str) else "", st.model, st.effort, started, last,
                              st.context_tokens, tok, cost, st.last_text, mp, (now - started).total_seconds(),
                              st.peak_context))
        burn += _recent_tokens(st, burn_from)
        tot = _msum(tot, tok)
        if cost is not None:
            cost_sum += cost
            cost_known = True
    for ap in glob.glob(os.path.join(project_dir, "*", "subagents", "agent-*.jsonl")):
        try:
            mt = os.path.getmtime(ap)
        except OSError:
            continue
        if mt < cutoff:
            continue
        st = _read(ap)
        if st is None:
            continue
        sid = os.path.basename(os.path.dirname(os.path.dirname(ap)))
        aid = os.path.basename(ap)[len("agent-"):-len(".jsonl")]
        meta = _load_json(ap[:-6] + ".meta.json")
        atype = meta.get("agentType") or "unknown"
        started = st.first_ts or datetime.fromtimestamp(mt, timezone.utc)
        last = st.last_ts or started
        note = ""
        src = ""
        if "finished" in meta:
            finished = bool(meta["finished"])
            src = "meta finished"
        elif st.final:
            finished, src = True, "ended turn"  # skill forks never get a `finished` key; a closed turn means done
        elif (now - last).total_seconds() > UNKNOWN_AFTER:
            finished, note, src = True, "done?", "done? (quiet >60 min)"
        else:
            finished = nowt - mt > NO_META_DONE_AFTER
            src = "quiet >30 min" if finished else ""
        if aid in dismissed:
            finished, note, src = True, "dismissed", "dismissed"
        if not finished:
            src = ""
        if finished:
            state, ended = "done", last
        else:
            ended = None
            state = "stalled" if (now - last).total_seconds() > stalled_after else "live"
        tok, cost = _totals(st)
        denom = tok["input"] + tok["cache_read"] + tok["cache_write"]
        hit = round(100.0 * tok["cache_read"] / denom, 1) if denom else None
        try:
            depth = int(meta.get("spawnDepth") or 0)
        except (TypeError, ValueError):
            depth = 0
        bud = budget_for(st.model, budgets)
        burn += _recent_tokens(st, burn_from)
        agents.append(AgentView(
            aid, sid, atype, infer_kind(atype, meta.get("description") or ""), meta.get("description") or "",
            st.model, st.effort, meta.get("requestShape") == "background", depth, started, last,
            finished, ended, state, len(st.tool_ids), st.last_tool, st.last_error, tok, cost, hit,
            st.brief, st.hint, ap, ((ended or now) - started).total_seconds(), note,
            st.last_action, plan_line(meta.get("description") or "", st.brief, plans_dir),
            src, list(st.pending.values())[-1] if st.pending else "", list(st.recent), st.final_text,
            outcome_of(st, finished, note), st.context_tokens, st.peak_context, bud,
            bool(bud and st.peak_context > bud)))
        tot = _msum(tot, tok)
        if cost is not None:
            cost_sum += cost
            cost_known = True
    mains.sort(key=lambda m: m.last_activity, reverse=True)
    agents.sort(key=lambda a: a.last_activity, reverse=True)
    totals = {"agents_live": sum(a.state == "live" for a in agents),
              "agents_stalled": sum(a.state == "stalled" for a in agents),
              "agents_done": sum(a.state == "done" for a in agents),
              "tokens": tot, "cost_usd": cost_sum if cost_known else None,
              "agents_over_budget": sum(a.over_budget for a in agents),
              "burn_tokens": burn, "burn_minutes": burn_minutes,
              "burn_per_min": burn / burn_minutes if burn_minutes else 0.0,
              "cost_per_token": (cost_sum / tot["total"]) if cost_known and tot["total"] else None}
    return Snapshot(mains, agents, totals, now)


def timeline(agent_id, project_dir=DEFAULT_DIR):
    project_dir = os.path.expanduser(project_dir or newest_project_dir() or "")
    hits = glob.glob(os.path.join(project_dir, "*", "subagents", "agent-%s.jsonl" % agent_id))
    if not hits:
        return []
    st = _read(hits[0])
    return [dict(t) for t in st.timeline] if st else []


def output_share(tokens):
    """(output tokens incl. thinking, share of total in %), the same share as the `tokens` report's Output row."""
    t = int((tokens or {}).get("total") or 0)
    o = int((tokens or {}).get("output") or 0)
    return o, (o / t * 100 if t else 0.0)


_ledger = {"val": None, "at": 0.0, "busy": False}


def _ledger_today():
    """(output, share %) exactly as the `tokens today` report row 'Output (incl. thinking)': ingest, then the day's totals."""
    import argparse
    from .. import config, ingest, report, store
    cfg = config.load()
    ingest.run(cfg, quiet=True)
    conn = store.connect(cfg)
    try:
        store.refresh_days(conn, cfg)
        flt = report.Flt(argparse.Namespace(all=False), conn)
        _, a, b, _, _ = report.period_bounds(cfg, "today")
        cur = report.sum_row(conn, flt, "day BETWEEN ? AND ?", [a, b])
    finally:
        conn.close()
    t = report.total(cur)
    return int(cur[3]), (cur[3] / t * 100 if t else 0.0)


def report_output(max_age=60.0):
    """Cached today-report output figure; refreshed in a background thread because ingest is slow. None until first result."""
    import threading
    now = time.time()
    if not _ledger["busy"] and now - _ledger["at"] >= max_age:
        _ledger["busy"] = True

        def work():
            try:
                _ledger["val"] = _ledger_today()
            except Exception:
                pass
            _ledger["at"] = time.time()
            _ledger["busy"] = False
        threading.Thread(target=work, daemon=True).start()
    return _ledger["val"]


def analytics(snap):
    """Session-level agent stats for the newest main session, finished agents included."""
    sid = snap.mains[0].session_id if snap.mains else None
    ags = [a for a in snap.agents if sid is None or a.session_id == sid]
    counts = {k: sum(a.outcome == k for a in ags) for k in ("success", "failed", "running", "stopped", "unknown")}

    def group(key):
        out = {}
        for a in ags:
            g = out.setdefault(key(a) or "-", {"n": 0, "tokens": 0, "output": 0, "secs": 0.0, "cmds": 0, "ok": 0, "bad": 0})
            g["n"] += 1
            g["tokens"] += a.tokens.get("total", 0)
            g["output"] += a.tokens.get("output", 0)
            g["secs"] += a.elapsed_s or 0
            g["cmds"] += a.tool_calls or 0
            g["ok"] += a.outcome == "success"
            g["bad"] += a.outcome == "failed"
        return out

    done = counts["success"] + counts["failed"]
    return {"agents": ags, "counts": counts, "n": len(ags),
            "tokens": sum(a.tokens.get("total", 0) for a in ags),
            "output": sum(a.tokens.get("output", 0) for a in ags),
            "secs": sum(a.elapsed_s or 0 for a in ags), "cmds": sum(a.tool_calls or 0 for a in ags),
            "rate": (counts["success"] / done * 100) if done else None,
            "by_type": group(lambda a: a.agent_type), "by_model": group(lambda a: actions_short(a.model))}


def actions_short(model):
    m = str(model or "-")
    for k in ("opus", "sonnet", "haiku"):
        if k in m.lower():
            return k.capitalize()
    return m
