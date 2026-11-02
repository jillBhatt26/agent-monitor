"""Generic rules engine: puts every call under a feature and a task kind.

Inputs are normalized calls whose `tool_signals` are small labels (never text):
  write:<path> read:<path> ref:<path> search web cmd:test cmd:git cmd:ops cmd:other
  skill:<name> spawn:<agent type> branch:<name> tool:other
Project rules come from a `.token-ledger.toml` found by walking up from the session cwd.
"""
import glob
import json
import os
import re

from .compat import posix, same_or_under

try:
    import tomllib
except ImportError:
    tomllib = None

DEFAULT_AGENT_KIND = {
    "test-runner": "Testing", "test-judge": "Judging", "preflight-reviewer": "Judging",
    "builder": "Implementation", "Plan": "Planning",
    "Explore": "Research", "claude-code-guide": "Research", "git-scout": "Research", "general-purpose": "Research",
    "wiki-syncer": "Docs & wiki", "aws-reader": "Ops", "dev-manager": "Ops",
}
DEFAULT_SKILL_KIND = {
    "test-e2e": "Testing", "test-steps": "Testing", "test-manager": "Testing", "review-merge": "Judging",
    "sync": "Git & release", "start": "Git & release", "commit": "Git & release", "commit-plan": "Git & release",
    "pr": "Git & release", "ship": "Git & release", "stage": "Git & release", "release": "Git & release",
    "post-release": "Git & release", "checkpoint": "Docs & wiki", "notify": "Ops", "dev": "Ops",
}
# Generic path rules, tried after a project's own: markdown under plans/ or docs/ is planning / docs, other edits are code.
DEFAULT_PATH_KINDS = [
    (re.compile(r"(^|/)plans?/.*\.md$"), "Planning"),
    (re.compile(r"(^|/)(docs?|wiki)/.*\.md$"), "Docs & wiki"),
    (re.compile(r".*"), "Implementation"),
]
IGNORED_BRANCHES = {"", "HEAD", "main", "master", "develop"}


class Rules:
    def __init__(self, root=None, data=None):
        data = data or {}
        self.root = root
        self.features = []   # (compiled regex, label template, dashes_to_spaces)
        for f in data.get("feature", []):
            self.features.append((re.compile(f["path_regex"]), f["label"], f.get("dashes_to_spaces", False)))
        self.kind_rules = []  # (prefix or None, compiled regex or None, kind)
        for k in data.get("kind", []):
            rx = re.compile(k["path_regex"]) if k.get("path_regex") else None
            self.kind_rules.append((k.get("path_prefix"), rx, k["kind"]))
        kinds = data.get("kinds", {})
        self.agent_kind = dict(DEFAULT_AGENT_KIND, **kinds.get("agent", {}))
        self.skill_kind = dict(DEFAULT_SKILL_KIND, **kinds.get("skill", {}))
        self.inherit_defaults = data.get("inherit_defaults", True)
        self.unassigned = (data.get("defaults") or {}).get("unassigned_label")
        self.branch_map = self._branch_map(data.get("branches") or {})

    def feature_for_path(self, path):
        path = posix(path)
        for rx, label, dashes in self.features:
            m = rx.search(path)
            if m:
                try:
                    out = label.format("", *m.groups(default=""))
                except (IndexError, KeyError):
                    continue
                out = out.strip()
                return out.replace("-", " ") if dashes else out
        return None

    def _branch_map(self, b):
        out = {}
        if not (self.root and b.get("plans_glob")):
            return out
        line_rx = re.compile(b.get("branch_line_regex", "[Bb]ranch"))
        tok_rx = re.compile(b.get("branch_token_regex", r"[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+"))
        for p in sorted(glob.glob(os.path.join(self.root, b["plans_glob"]))):
            label = self.feature_for_path(os.path.relpath(p, self.root))
            if not label:
                continue
            try:
                with open(p, encoding="utf-8", errors="replace") as f:
                    for line in f:
                        if line_rx.search(line):
                            for tok in tok_rx.findall(line):
                                out.setdefault(tok.strip("./"), label)
            except OSError:
                pass
        return out

    def kind_for_path(self, path):
        rel = os.path.relpath(path, self.root) if self.root and same_or_under(path, self.root) else path
        rel = posix(rel)
        for prefix, rx, kind in self.kind_rules:
            if (prefix and rel.startswith(prefix)) or (rx and rx.search(rel)):
                return kind
        if self.inherit_defaults or not self.kind_rules:
            for rx, kind in DEFAULT_PATH_KINDS:
                if rx.search(rel):
                    return kind
        return None


class RulesFinder:
    def __init__(self):
        self._by_dir = {}

    def for_cwd(self, cwd):
        key = cwd or ""
        if key not in self._by_dir:
            self._by_dir[key] = self._find(key)
        return self._by_dir[key]

    @staticmethod
    def _find(d):
        while d:
            path = os.path.join(d, ".token-ledger.toml")
            if tomllib and os.path.isfile(path):
                try:
                    with open(path, "rb") as f:
                        return Rules(d, tomllib.load(f))
                except (OSError, ValueError, KeyError, re.error):
                    pass
            parent = os.path.dirname(d)
            if parent == d:
                break
            d = parent
        return _GENERIC


_GENERIC = Rules()


def load_tags(path):
    tags = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    tags.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    tags.sort(key=lambda t: t.get("ts", ""))
    return tags


def tag_at(tags, session_id, ts):
    """Latest tag at or before ts; a session-specific tag beats a '*' tag."""
    best_s = best_all = None
    for t in tags:
        if t.get("ts", "") > ts:
            break
        if t.get("session_id") == session_id:
            best_s = t
        elif t.get("session_id") == "*":
            best_all = t
    return best_s or best_all


def signal_kind(sig, rules):
    """Kind a single signal points to; 'Research' for read-only; None when uninformative."""
    head, _, val = sig.partition(":")
    if head == "spawn":
        return rules.agent_kind.get(val, "Research")
    if head == "skill":
        return rules.skill_kind.get(val.split(":")[-1])
    if head == "write":
        return rules.kind_for_path(val)
    if head == "cmd":
        return {"test": "Testing", "git": "Git & release", "ops": "Ops"}.get(val)
    if head in ("read", "search", "web"):
        return "Research"
    return None


def call_kind(signals, carry, rules):
    """Kind from the call's own tool signals; carry = latest tool kind in the same user turn."""
    if not signals:
        return carry or "Conversation"
    kinds = [signal_kind(s, rules) for s in signals]
    informative = [k for k in kinds if k and k != "Research"]
    if informative:
        return informative[0]
    return "Research" if "Research" in kinds else "Conversation"


def classify_unit(calls, project, title, tags, finder):
    """Assign call['feature'] and call['kind'] for one session's calls (main calls first, then agent calls)."""
    cur_feat = cur_branch = None
    carry, cur_turn = None, None
    timeline, tool_feat = [], {}
    main = [c for c in calls if not c.get("agent_id")]
    agents = [c for c in calls if c.get("agent_id")]

    def fallback(rules, branch):
        if title:
            return title
        if branch and branch not in IGNORED_BRANCHES:
            return branch
        return rules.unassigned or f"{project} (untagged)"

    for c in main:
        rules = finder.for_cwd(c.get("cwd"))
        if c["turn"] != cur_turn:
            cur_turn, carry = c["turn"], None
        gb = c.get("git_branch")
        if gb and gb not in IGNORED_BRANCHES:
            cur_branch = gb
        for s in c["tool_signals"]:
            head, _, val = s.partition(":")
            if head in ("write", "read", "ref"):
                f = rules.feature_for_path(val)
                if f:
                    cur_feat = f
            elif head == "branch":
                cur_branch = val
        tag = tag_at(tags, c["session_id"], c["ts"])
        if tag and tag.get("feature"):
            feat = tag["feature"]
        elif cur_feat:
            feat = cur_feat
        elif cur_branch and rules.branch_map.get(cur_branch):
            feat = rules.branch_map[cur_branch]
        else:
            feat = fallback(rules, cur_branch)
        kind = (tag or {}).get("kind") if tag and tag.get("feature") else None
        kind = kind or call_kind(c["tool_signals"], carry, rules)
        if c["tool_signals"]:
            ck = call_kind(c["tool_signals"], carry, rules)
            if ck != "Conversation":
                carry = ck
        c["feature"], c["kind"] = feat, kind
        timeline.append((c["ts"], feat))
        for tid in c.get("tool_ids", []):
            tool_feat[tid] = feat
    timeline.sort()
    for c in sorted(agents, key=lambda x: x.get("depth", 1)):
        rules = finder.for_cwd(c.get("cwd"))
        feat = tool_feat.get(c.get("spawn_id")) or next((f for t, f in reversed(timeline) if t <= c["ts"]), None)
        c["feature"] = feat or fallback(rules, None)
        c["kind"] = rules.agent_kind.get(c.get("agent_type"), "Research")
        for tid in c.get("tool_ids", []):
            tool_feat[tid] = c["feature"]
