"""Configuration: ~/.config/token-ledger/config.toml (or config.json when tomllib is missing)."""
import datetime as dt
import json
import os
import socket

try:
    import tomllib
except ImportError:  # Python < 3.11
    tomllib = None

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # pragma: no cover
    ZoneInfo, ZoneInfoNotFoundError = None, Exception

DEFAULTS = {
    "data_dir": "~/.local/share/token-ledger",
    "machine": None,
    "sources": ["claude-code"],
    "extra_claude_dirs": [],
    "price_file": None,
    "timezone": None,          # None = the computer's own time zone
    "watch": {},
}

# Context budgets for the live monitor. Main session: warn at `save` (time to checkpoint), alarm at `compact`.
# Agents: per model family; a run that goes over is flagged so its brief can be tightened.
WATCH_DEFAULTS = {
    "workspace": None,         # folder whose hook delivers restart/stop requests (None = any project with the hook)
    "request_dir": None,       # default: <data_dir>/agent-requests
    "plans_dir": None,         # default: <project folder>/wiki/plans when it exists
    "main_save_tokens": 120000,
    "main_compact_tokens": 160000,
    "agent_budget_tokens": {"opus": 80000, "sonnet": 60000, "haiku": 30000, "default": 60000},
    "burn_window_minutes": 10,
    "alerts": True,            # terminal bell + toast when an agent stalls or goes over budget
}


def _local_tz():
    return dt.datetime.now().astimezone().tzinfo


def resolve_tz(name):
    """(tzinfo, label). Windows has no system time-zone database: without the `tzdata` package an IANA name cannot be
    loaded, so fall back to the computer's own offset instead of crashing."""
    if not name:
        tz = _local_tz()
        return tz, str(tz)
    if ZoneInfo:
        try:
            return ZoneInfo(name), name
        except (ZoneInfoNotFoundError, ValueError, OSError):
            pass
    tz = _local_tz()
    return tz, f"{tz} (local; '{name}' not found - pip install tzdata)"


class Config:
    def __init__(self, data=None, data_dir=None):
        d = dict(DEFAULTS)
        d.update(data or {})
        self.data_dir = os.path.expanduser(data_dir or os.environ.get("TOKEN_LEDGER_DATA_DIR") or d["data_dir"])
        self.machine = d["machine"] or socket.gethostname()
        self.sources = d["sources"]
        self.extra_claude_dirs = [os.path.expanduser(p) for p in d["extra_claude_dirs"]]
        self.price_file = os.path.expanduser(d["price_file"]) if d["price_file"] else None
        self.tz, self.timezone = resolve_tz(d["timezone"])
        self.db_path = os.path.join(self.data_dir, "tokens.sqlite")
        self.tags_path = os.path.join(self.data_dir, "tags.jsonl")
        self.log_path = os.path.join(self.data_dir, "tokens.log")
        self.html_path = os.path.join(self.data_dir, "dashboard.html")
        w = dict(WATCH_DEFAULTS)
        w.update(d.get("watch") or {})
        budgets = dict(WATCH_DEFAULTS["agent_budget_tokens"])
        budgets.update((d.get("watch") or {}).get("agent_budget_tokens") or {})
        w["agent_budget_tokens"] = budgets
        for k in ("workspace", "request_dir", "plans_dir"):
            if w.get(k):
                w[k] = os.path.expanduser(w[k])
        w["request_dir"] = w["request_dir"] or os.path.join(self.data_dir, "agent-requests")
        self.watch = w


def config_dir():
    return os.path.expanduser(os.environ.get("TOKEN_LEDGER_CONFIG_DIR") or "~/.config/token-ledger")


def load(data_dir=None):
    data = {}
    base = config_dir()
    toml_path, json_path = os.path.join(base, "config.toml"), os.path.join(base, "config.json")
    try:
        if os.path.exists(toml_path) and tomllib:
            with open(toml_path, "rb") as f:
                data = tomllib.load(f)
        elif os.path.exists(json_path):
            with open(json_path, encoding="utf-8") as f:
                data = json.load(f)
    except (OSError, ValueError) as e:
        print(f"token-ledger: could not read config ({e}); using defaults")
    return Config(data, data_dir)
