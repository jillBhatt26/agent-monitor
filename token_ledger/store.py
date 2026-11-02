"""SQLite store: schema, inserts, account resolution, export/import."""
import datetime as dt
import json
import os
import sqlite3

from . import prices as pricing

UNKNOWN_ACCOUNT = "unknown (before tracking)"

SCHEMA = """
CREATE TABLE IF NOT EXISTS calls(
  source TEXT, machine TEXT, config_dir TEXT, account TEXT, project TEXT, session_id TEXT,
  agent_id TEXT, agent_type TEXT, request_id TEXT, ts TEXT, day TEXT,
  model TEXT, speed TEXT, effort TEXT,
  input INTEGER, cache_write_5m INTEGER, cache_write_1h INTEGER, cache_read INTEGER,
  output INTEGER, thinking INTEGER, feature TEXT, kind TEXT, est_cost_usd REAL,
  PRIMARY KEY(source, machine, config_dir, request_id)
);
CREATE INDEX IF NOT EXISTS calls_day ON calls(day);
CREATE INDEX IF NOT EXISTS calls_session ON calls(session_id);
CREATE TABLE IF NOT EXISTS ingested_files(path TEXT PRIMARY KEY, size INTEGER, mtime REAL, lines_read INTEGER);
CREATE TABLE IF NOT EXISTS session_accounts(session_id TEXT, account TEXT, email TEXT, first_seen TEXT);
CREATE TABLE IF NOT EXISTS account_marks(session_id TEXT, ts TEXT, account_email TEXT, account_uuid_short TEXT);
CREATE INDEX IF NOT EXISTS marks_session ON account_marks(session_id, ts);
CREATE TABLE IF NOT EXISTS sessions(source TEXT, machine TEXT, config_dir TEXT, session_id TEXT, project TEXT,
  title TEXT, first_ts TEXT, last_ts TEXT, PRIMARY KEY(source, machine, config_dir, session_id));
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
"""

COLS = ["source", "machine", "config_dir", "account", "project", "session_id", "agent_id", "agent_type",
        "request_id", "ts", "day", "model", "speed", "effort", "input", "cache_write_5m", "cache_write_1h",
        "cache_read", "output", "thinking", "feature", "kind", "est_cost_usd"]


def connect(cfg):
    os.makedirs(cfg.data_dir, exist_ok=True)
    conn = sqlite3.connect(cfg.db_path, timeout=30)
    conn.executescript(SCHEMA)
    return conn


def log(cfg, msg):
    try:
        os.makedirs(cfg.data_dir, exist_ok=True)
        with open(cfg.log_path, "a", encoding="utf-8") as f:
            f.write(f"{dt.datetime.now(dt.timezone.utc).isoformat()} {msg}\n")
    except OSError:
        pass


def now_ts():
    n = dt.datetime.now(dt.timezone.utc)
    return n.strftime("%Y-%m-%dT%H:%M:%S.") + f"{n.microsecond // 1000:03d}Z"


def day_of(cfg, ts):
    t = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return t.astimezone(cfg.tz).date().isoformat()


def account_label(email):
    return email.split("@")[0] if email else UNKNOWN_ACCOUNT


def upsert_call(conn, cfg, prices, c, reclassify=False):
    """Insert one normalized, classified call. A later line with more output tokens updates the tokens only."""
    cost = pricing.cost_usd(prices, c["model"], c.get("speed"), c["input"], c["cache_write_5m"],
                            c["cache_write_1h"], c["cache_read"], c["output"])
    conn.execute(
        "INSERT INTO calls(source, machine, config_dir, account, project, session_id, agent_id, agent_type, request_id,"
        " ts, day, model, speed, effort, input, cache_write_5m, cache_write_1h, cache_read, output, thinking,"
        " feature, kind, est_cost_usd) VALUES(?,?,?,NULL,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT(source, machine, config_dir, request_id) DO UPDATE SET output=excluded.output,"
        " thinking=excluded.thinking, est_cost_usd=excluded.est_cost_usd WHERE excluded.output > calls.output",
        (c["source"], c["machine"], c["config_dir"], c["project"], c["session_id"], c.get("agent_id"),
         c.get("agent_type"), c["request_id"], c["ts"], day_of(cfg, c["ts"]) if c["ts"] else "", c["model"],
         c.get("speed"), c.get("effort"), c["input"], c["cache_write_5m"], c["cache_write_1h"], c["cache_read"],
         c["output"], c.get("thinking") or 0, c["feature"], c["kind"], cost))
    if reclassify:
        conn.execute("UPDATE calls SET feature=?, kind=? WHERE source=? AND machine=? AND config_dir=? AND request_id=?",
                     (c["feature"], c["kind"], c["source"], c["machine"], c["config_dir"], c["request_id"]))


def record_mark(conn, session_id, email, uuid_short):
    """Insert an account mark only when it differs from the session's latest mark."""
    if not email or not session_id:
        return False
    row = conn.execute("SELECT account_email, account_uuid_short FROM account_marks WHERE session_id=? "
                       "ORDER BY ts DESC LIMIT 1", (session_id,)).fetchone()
    if row and row[0] == email and row[1] == uuid_short:
        return False
    conn.execute("INSERT INTO account_marks(session_id, ts, account_email, account_uuid_short) VALUES(?,?,?,?)",
                 (session_id, now_ts(), email, uuid_short))
    conn.commit()
    return True


def resolve_accounts(conn, cfg, force=False):
    """Account of a local call = latest mark at or before it, else the session's first mark, else unknown.
    Imported calls from other machines keep the account they were exported with."""
    sig = "%s|%s" % (conn.execute("SELECT count(*) FROM account_marks").fetchone()[0],
                     conn.execute("SELECT count(*) FROM calls WHERE machine=?", (cfg.machine,)).fetchone()[0])
    old = conn.execute("SELECT v FROM meta WHERE k='account_sig'").fetchone()
    if not force and old and old[0] == sig:
        return
    marks = {}
    for sid, ts, email in conn.execute("SELECT session_id, ts, account_email FROM account_marks ORDER BY ts"):
        marks.setdefault(sid, []).append((ts, account_label(email)))
    conn.execute("UPDATE calls SET account=? WHERE machine=?", (UNKNOWN_ACCOUNT, cfg.machine))
    for sid, ms in marks.items():
        for i, (ts, label) in enumerate(ms):
            lo = "" if i == 0 else ts
            hi = ms[i + 1][0] if i + 1 < len(ms) else "￿"
            conn.execute("UPDATE calls SET account=? WHERE machine=? AND session_id=? AND ts>=? AND ts<?",
                         (label, cfg.machine, sid, lo, hi))
    conn.execute("INSERT OR REPLACE INTO meta(k, v) VALUES('account_sig', ?)", (sig,))
    conn.commit()


def refresh_days(conn, cfg):
    """Re-bucket days when the configured timezone changed since the last run."""
    old = conn.execute("SELECT v FROM meta WHERE k='timezone'").fetchone()
    if old and old[0] == cfg.timezone:
        return
    if old:
        rows = conn.execute("SELECT rowid, ts FROM calls").fetchall()
        conn.executemany("UPDATE calls SET day=? WHERE rowid=?", [(day_of(cfg, ts), rid) for rid, ts in rows if ts])
    conn.execute("INSERT OR REPLACE INTO meta(k, v) VALUES('timezone', ?)", (cfg.timezone,))
    conn.commit()


def export_rows(conn, since=None):
    q = "SELECT " + ", ".join(COLS) + " FROM calls" + (" WHERE day>=?" if since else "") + " ORDER BY ts"
    for r in conn.execute(q, (since,) if since else ()):
        yield dict(zip(COLS, r))


def import_rows(conn, lines):
    n = 0
    ph = ",".join("?" * len(COLS))
    for line in lines:
        line = line.strip()
        if not line:
            continue
        d = json.loads(line)
        cur = conn.execute(f"INSERT OR IGNORE INTO calls({', '.join(COLS)}) VALUES({ph})", [d.get(c) for c in COLS])
        n += cur.rowcount
    conn.commit()
    return n
