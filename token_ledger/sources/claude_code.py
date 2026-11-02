"""Adapter for Claude Code session logs: <config dir>/projects/<project>/<session>.jsonl and subagents/."""
import glob
import json
import os
import re

from ..compat import slug

NAME = "claude-code"

TEST_CMD = re.compile(r"phpunit|pytest|unittest|tools/e2e|playwright|npx\s+tsc|eslint|pint\s+--test|jest|vitest|"
                      r"npm\s+(run\s+)?test|go\s+test|cargo\s+test")
GIT_CMD = re.compile(r"(^|[;&|(]\s*)(git|gh)\s")
OPS_CMD = re.compile(r"\baws\s|tools/dev|\bdocker\b|\bkubectl\b|\bsystemctl\b|\bterraform\b|\bvercel\b|\bsupabase\b")
BRANCH_CMD = re.compile(r"(?:checkout\s+-b|switch\s+-c)\s+([^\s;&|'\"]+)")
# POSIX paths (a/b, /x/y) and Windows paths (C:\x\y, sub\dir\file)
PATHISH = re.compile(r"^([A-Za-z]:)?[\w.~@+-]*([\\/][\w.@+-]+)+$")


def config_dirs(cfg):
    """(label, path) of every Claude config dir that has projects/: ~/.claude*, $CLAUDE_CONFIG_DIR, extras."""
    home = os.path.expanduser("~")
    cands = glob.glob(os.path.join(home, ".claude")) + sorted(glob.glob(os.path.join(home, ".claude-*")))
    if os.environ.get("CLAUDE_CONFIG_DIR"):
        cands.append(os.path.expanduser(os.environ["CLAUDE_CONFIG_DIR"]))
    cands += cfg.extra_claude_dirs
    seen, out = set(), []
    for d in cands:
        real = os.path.realpath(d)
        if real in seen or not os.path.isdir(os.path.join(d, "projects")):
            continue
        seen.add(real)
        out.append((os.path.basename(d.rstrip("/")).lstrip("."), d))
    return out


def project_label(folder):
    """Readable project name from Claude Code's folder name: home prefix and Windows drive (`E--`) removed."""
    home = slug(os.path.expanduser("~"))      # /home/me -> -home-me ; C:\Users\me -> C--Users-me
    if folder.lower() == home.lower():
        return "home"
    if folder.lower().startswith(home.lower() + "-"):
        folder = folder[len(home) + 1:]
    folder = re.sub(r"^[A-Za-z]--", "", folder)
    return folder.strip("-") or "home"


def current_account():
    """(email, uuid_short) of the account signed in right now, or (None, None). Used by the hooks."""
    home = os.path.expanduser("~")
    paths = []
    if os.environ.get("CLAUDE_CONFIG_DIR"):
        paths.append(os.path.join(os.path.expanduser(os.environ["CLAUDE_CONFIG_DIR"]), ".claude.json"))
    paths.append(os.path.join(home, ".claude.json"))
    for p in paths:
        try:
            with open(p, encoding="utf-8") as f:
                acct = json.load(f).get("oauthAccount") or {}
        except (OSError, ValueError):
            continue
        if acct.get("emailAddress"):
            return acct["emailAddress"], (acct.get("accountUuid") or "")[:8]
    return None, None


def current_config_label():
    d = os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude"
    return os.path.basename(os.path.expanduser(d).rstrip("/")).lstrip(".") or "claude"


def _usage(u):
    cc = u.get("cache_creation") or {}
    if cc:
        w5, w1 = cc.get("ephemeral_5m_input_tokens") or 0, cc.get("ephemeral_1h_input_tokens") or 0
    else:
        w5, w1 = u.get("cache_creation_input_tokens") or 0, 0
    return (u.get("input_tokens") or 0, w5, w1, u.get("cache_read_input_tokens") or 0,
            u.get("output_tokens") or 0, (u.get("output_tokens_details") or {}).get("thinking_tokens") or 0)


def _signals(name, inp):
    inp = inp if isinstance(inp, dict) else {}
    if name in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        return ["write:" + (inp.get("file_path") or "")]
    if name == "Read":
        return ["read:" + (inp.get("file_path") or "")]
    if name in ("Grep", "Glob"):
        return ["search"]
    if name in ("WebFetch", "WebSearch"):
        return ["web"]
    if name == "Skill":
        return ["skill:" + (inp.get("skill") or "")]
    if name in ("Agent", "Task"):
        return ["spawn:" + (inp.get("subagent_type") or "")]
    if name == "Bash":
        cmd = inp.get("command") or ""
        sig = ["cmd:test" if TEST_CMD.search(cmd) else "cmd:git" if GIT_CMD.search(cmd)
               else "cmd:ops" if OPS_CMD.search(cmd) else "cmd:other"]
        for tok in cmd.replace("'", " ").replace('"', " ").split()[:200]:
            if PATHISH.match(tok) and len(sig) < 9:
                sig.append("ref:" + tok)
        m = BRANCH_CMD.search(cmd)
        if m:
            sig.append("branch:" + m.group(1))
        return sig
    return ["tool:other"]


def parse_file(path, is_sub):
    """Return (calls in first-seen order, custom title, ai title, unparsable-line count, first cwd)."""
    reqs = {}
    custom = ai = cwd = None
    turn = bad = 0
    with open(path, "rb") as f:
        for raw in f:
            line = raw.decode("utf-8", "replace")
            if '"type":"assistant"' in line and '"usage"' in line:
                try:
                    d = json.loads(line)
                except ValueError:
                    bad += 1
                    continue
                msg = d.get("message") or {}
                u = msg.get("usage") or {}
                key = d.get("requestId") or msg.get("id")
                if not key or msg.get("model") == "<synthetic>" or not u:
                    continue
                inp, w5, w1, read, out, think = _usage(u)
                if inp + w5 + w1 + read + out == 0:
                    continue
                cwd = cwd or d.get("cwd")
                sigs, ids = [], []
                content = msg.get("content")
                for b in content if isinstance(content, list) else []:
                    if isinstance(b, dict) and b.get("type") == "tool_use":
                        sigs += _signals(b.get("name"), b.get("input"))
                        ids.append(b.get("id"))
                r = reqs.get(key)
                if r is None:
                    r = reqs[key] = {"request_id": key, "ts": d.get("timestamp") or "", "model": msg.get("model"),
                                     "speed": u.get("speed"), "effort": d.get("effort"), "turn": turn,
                                     "tool_signals": [], "tool_ids": [], "cwd": d.get("cwd") or cwd,
                                     "agent_id": d.get("agentId"), "git_branch": None,
                                     "_u": (inp, w5, w1, read, out, think)}
                r["tool_signals"] += sigs
                r["tool_ids"] += ids
                if d.get("gitBranch"):
                    r["git_branch"] = d["gitBranch"]
                if out > r["_u"][4]:
                    r["_u"] = (inp, w5, w1, read, out, think)
            elif line.startswith('{"type":"custom-title"'):
                try:
                    custom = json.loads(line).get("customTitle") or custom
                except ValueError:
                    bad += 1
            elif line.startswith('{"type":"ai-title"'):
                try:
                    ai = json.loads(line).get("aiTitle") or ai
                except ValueError:
                    bad += 1
            elif not is_sub and '"type":"user"' in line and '"tool_result"' not in line:
                turn += 1
    calls = []
    for r in reqs.values():
        r["input"], r["cache_write_5m"], r["cache_write_1h"], r["cache_read"], r["output"], r["thinking"] = r.pop("_u")
        calls.append(r)
    return calls, custom, ai, bad, cwd


def scan(cfg, seen):
    """Yield one unit per session whose log files changed since the last import."""
    stats_bad = 0
    for label, base in config_dirs(cfg):
        for pdir in sorted(glob.glob(os.path.join(base, "projects", "*"))):
            project = project_label(os.path.basename(pdir))
            sids = {os.path.basename(p)[:-6] for p in glob.glob(os.path.join(pdir, "*.jsonl"))}
            sids |= {os.path.basename(p) for p in glob.glob(os.path.join(pdir, "*")) if os.path.isdir(p)}
            for sid in sorted(sids):
                main = os.path.join(pdir, sid + ".jsonl")
                subs = sorted(glob.glob(os.path.join(pdir, sid, "subagents", "agent-*.jsonl")))
                paths = ([main] if os.path.exists(main) else []) + subs
                if not paths:
                    continue
                files = [(p, os.stat(p).st_size, os.stat(p).st_mtime) for p in paths]
                if all(seen.get(p) == (s, m) for p, s, m in files):
                    continue
                calls, title, ai, cwd = [], None, None, None
                if os.path.exists(main):
                    calls, title, ai, bad, cwd = parse_file(main, False)
                    stats_bad += bad
                metas = {}
                for p in subs:
                    try:
                        with open(p[:-6] + ".meta.json", encoding="utf-8") as f:
                            metas[p] = json.load(f)
                    except (OSError, ValueError):
                        metas[p] = {}
                for p in subs:
                    m = metas[p]
                    sc, _, _, bad, scwd = parse_file(p, True)
                    stats_bad += bad
                    for c in sc:
                        c.update(agent_id=os.path.basename(p)[len("agent-"):-6], agent_type=m.get("agentType"),
                                 spawn_id=m.get("toolUseId"), depth=m.get("spawnDepth") or 1,
                                 cwd=c.get("cwd") or scwd or cwd)
                    calls += sc
                for c in calls:
                    c.update(source=NAME, machine=cfg.machine, config_dir=label, project=project, session_id=sid,
                             session_title=title or ai)
                    c.setdefault("agent_type", None)
                    c.setdefault("spawn_id", None)
                yield {"files": files, "session_id": sid, "project": project, "config_dir": label,
                       "title": title, "title_any": title or ai, "cwd": cwd, "calls": calls, "bad_lines": stats_bad}
                stats_bad = 0
