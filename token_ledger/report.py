"""Terminal reports."""
import datetime as dt
import os
import random

from . import store
from .store import UNKNOWN_ACCOUNT

NOTE = "cache read = context re-used (cheap); est. cost = list price x tokens, not your bill"
TOK = "input + cache_write_5m + cache_write_1h + cache_read + output"
SUMS = ("SUM(input), SUM(cache_write_5m + cache_write_1h), SUM(cache_read), SUM(output), "
        "SUM(est_cost_usd), COUNT(*), SUM(est_cost_usd IS NULL)")


def fmt_tok(n):
    n = n or 0
    for lim, suf in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if abs(n) >= lim:
            v = n / lim
            return f"{v:,.1f}{suf}" if v < 100 else f"{v:,.0f}{suf}"
    return f"{int(n):,}"


def fmt_cost(c):
    if c is None or abs(c) < 0.005:
        c = 0
    return f"${c:,.2f}"


def short_model(m):
    return (m or "?").replace("claude-", "")


class Flt:
    def __init__(self, args, conn):
        self.project = None if getattr(args, "all", False) else getattr(args, "project", None)
        self.account = getattr(args, "account", None)
        self.model = getattr(args, "model", None)
        self.machine = getattr(args, "machine", None)
        self.source = getattr(args, "source", None)
        self.config_dir = getattr(args, "config_dir", None)
        self.explicit_project = bool(self.project) or getattr(args, "all", False)
        self.active = active_project(conn)
        if not self.explicit_project:
            self.project = self.active[0] if self.active else None

    def where(self, extra=None, params=None):
        w, p = ["1=1"], []
        for col, val in (("project", self.project), ("account", self.account), ("machine", self.machine),
                         ("source", self.source), ("config_dir", self.config_dir)):
            if val:
                w.append(f"{col}=?")
                p.append(val)
        if self.model:
            w.append("model LIKE ?")
            p.append(f"%{self.model}%")
        if extra:
            w.append(extra)
            p += params or []
        return " AND ".join(w), p

    def label(self):
        bits = [f"project={self.project or 'ALL'}"]
        for n in ("account", "model", "machine", "source", "config_dir"):
            if getattr(self, n):
                bits.append(f"{n}={getattr(self, n)}")
        return ", ".join(bits)


def active_project(conn):
    r = conn.execute("SELECT project, ts FROM calls ORDER BY ts DESC LIMIT 1").fetchone()
    return r if r else None


def table(headers, rows, left=1):
    cols = list(zip(*([headers] + rows))) if rows else [[h] for h in headers]
    widths = [max(len(str(x)) for x in c) for c in cols]
    def line(r):
        return "  ".join((str(v).ljust(w) if i < left else str(v).rjust(w)) for i, (v, w) in enumerate(zip(r, widths)))
    out = [line(headers), "  ".join("-" * w for w in widths)]
    out += [line(r) for r in rows]
    return "\n".join(out)


def total(r):
    return (r[0] or 0) + (r[1] or 0) + (r[2] or 0) + (r[3] or 0)


def grouped(conn, flt, col, extra=None, params=None, order="tot DESC"):
    w, p = flt.where(extra, params)
    q = (f"SELECT {col}, {SUMS}, SUM({TOK}) AS tot FROM calls WHERE {w} GROUP BY {col} ORDER BY {order}")
    return conn.execute(q, p).fetchall()


def sum_row(conn, flt, extra=None, params=None):
    w, p = flt.where(extra, params)
    r = conn.execute(f"SELECT {SUMS} FROM calls WHERE {w}", p).fetchone()
    return tuple(x or 0 for x in r)


def breakdown_table(title, rows, label_hdr, limit=None):
    out = [f"\n{title}"]
    body = []
    shown = rows if limit is None else rows[:limit]
    for r in shown:
        t = total(r[1:5])
        body.append([r[0] or "?", fmt_tok(r[1]), fmt_tok(r[2]), fmt_tok(r[3]), fmt_tok(r[4]), fmt_tok(t),
                     f"{r[6]:,}", fmt_cost(r[5])])
    if limit is not None and len(rows) > limit:
        rest = rows[limit:]
        vals = [sum((x[i] or 0) for x in rest) for i in range(1, 7)]
        body.append([f"({len(rest)} more)", fmt_tok(vals[0]), fmt_tok(vals[1]), fmt_tok(vals[2]), fmt_tok(vals[3]),
                     fmt_tok(sum(vals[:4])), f"{vals[5]:,}", fmt_cost(vals[4])])
    out.append(table([label_hdr, "Fresh input", "Cache write", "Cache read", "Output", "Total", "Calls", "Est. cost"], body))
    return "\n".join(out)


def period_bounds(cfg, name):
    today = dt.datetime.now(cfg.tz).date()
    if name == "today":
        a, b = today, today
        pa, pb = today - dt.timedelta(days=1), today - dt.timedelta(days=1)
        lab = f"Today ({today})"
    elif name == "week":
        a = today - dt.timedelta(days=today.weekday())
        b = a + dt.timedelta(days=6)
        pa, pb = a - dt.timedelta(days=7), a - dt.timedelta(days=1)
        lab = f"This week ({a} to {b}, Mon-Sun)"
    elif name == "month":
        a = today.replace(day=1)
        b = (a + dt.timedelta(days=32)).replace(day=1) - dt.timedelta(days=1)
        pb = a - dt.timedelta(days=1)
        pa = pb.replace(day=1)
        lab = f"This month ({a:%B %Y})"
    else:
        a, b = today.replace(month=1, day=1), today.replace(month=12, day=31)
        pa, pb = a.replace(year=a.year - 1), b.replace(year=b.year - 1)
        lab = f"This year ({a.year})"
    return lab, a.isoformat(), b.isoformat(), pa.isoformat(), pb.isoformat()


def header(conn, cfg, flt, title):
    rng = conn.execute("SELECT MIN(day), MAX(day) FROM calls").fetchone()
    act = flt.active
    when = ""
    if act:
        t = dt.datetime.fromisoformat(act[1].replace("Z", "+00:00")).astimezone(cfg.tz)
        when = f" (last used {t:%Y-%m-%d %H:%M})"
    lines = [f"TOKEN USE - {title}", f"Data range: {rng[0]} to {rng[1]} ({cfg.timezone})   Filter: {flt.label()}",
             f"Active project: {act[0] if act else '-'}{when}"]
    if not flt.explicit_project:
        lines.append("(use --all for every project)")
    return "\n".join(lines)


def cmd_period(conn, cfg, flt, name):
    lab, a, b, pa, pb = period_bounds(cfg, name)
    ex, pr = "day BETWEEN ? AND ?", [a, b]
    print(header(conn, cfg, flt, lab))
    cur = sum_row(conn, flt, ex, pr)
    t = total(cur)
    print("\nTotals by token kind")
    names = ["Fresh input (new text sent in)", "Cache write (context stored)", "Cache read (context re-used)",
             "Output (incl. thinking)"]
    rows = [[n, fmt_tok(cur[i]), f"{(cur[i] / t * 100 if t else 0):.1f}%"] for i, n in enumerate(names)]
    rows.append(["Total", fmt_tok(t), "100%"])
    print(table(["Token kind", "Tokens", "Share"], rows))
    print(f"Estimated cost: {fmt_cost(cur[4])}   Calls: {cur[5]:,}")
    mrows = grouped(conn, flt, "model", ex, pr)
    print(breakdown_table("By model", [(short_model(r[0]),) + r[1:] for r in mrows], "Model"))
    print(breakdown_table("By task kind", grouped(conn, flt, "kind", ex, pr), "Task kind"))
    print(breakdown_table("By feature (top 15)", grouped(conn, flt, "feature", ex, pr), "Feature", 15))
    print(breakdown_table("By account", grouped(conn, flt, "account", ex, pr), "Account"))
    prev = sum_row(conn, flt, ex, [pa, pb])
    pt = total(prev)
    def pct(x, y):
        return "new" if not y else f"{(x - y) / y * 100:+.0f}%"
    print("\nCompared with the previous period")
    print(table(["", "Period", "Total tokens", "Est. cost"],
                [["This", f"{a} to {b}", fmt_tok(t), fmt_cost(cur[4])],
                 ["Previous", f"{pa} to {pb}", fmt_tok(pt), fmt_cost(prev[4])],
                 ["Change", "", pct(t, pt), pct(cur[4], prev[4])]]))
    unp = cur[6]
    print(f"\nGRAND TOTAL: {fmt_tok(t)} tokens, est. cost {fmt_cost(cur[4])}, {cur[5]:,} calls"
          + (f"   ({unp} calls on models without a price are not in the cost)" if unp else ""))
    print(NOTE)


def cmd_features(conn, cfg, flt, since):
    ex, pr = (("day >= ?", [since]) if since else (None, None))
    w, p = flt.where(ex, pr)
    rows = conn.execute(
        f"SELECT feature, kind, group_concat(DISTINCT model), {SUMS}, SUM({TOK}) FROM calls WHERE {w} "
        f"GROUP BY feature, kind", p).fetchall()
    feats = {}
    for r in rows:
        feats.setdefault(r[0], []).append(r)
    order = sorted(feats, key=lambda f: -sum(x[-1] or 0 for x in feats[f]))
    print(header(conn, cfg, flt, "Features" + (f" since {since}" if since else "")))
    body, grand = [], [0] * 6
    for f in order:
        krows = sorted(feats[f], key=lambda x: -(x[-1] or 0))
        sub = [0] * 6
        body.append([f"{f}", "", "", "", "", "", "", "", ""])
        for r in krows:
            vals = [r[3] or 0, r[4] or 0, r[5] or 0, r[6] or 0, r[7] or 0, r[8] or 0]
            models = ", ".join(sorted(short_model(m) for m in (r[2] or "").split(",")))
            body.append([f"   {r[1]}", models[:34], f"{vals[5]:,}", fmt_tok(vals[0]), fmt_tok(vals[1]), fmt_tok(vals[2]),
                         fmt_tok(vals[3]), fmt_tok(sum(vals[:4])), fmt_cost(vals[4])])
            sub = [a + b for a, b in zip(sub, vals)]
        body.append(["   Subtotal", "", f"{sub[5]:,}", fmt_tok(sub[0]), fmt_tok(sub[1]), fmt_tok(sub[2]), fmt_tok(sub[3]),
                     fmt_tok(sum(sub[:4])), fmt_cost(sub[4])])
        grand = [a + b for a, b in zip(grand, sub)]
    body.append(["GRAND TOTAL", "", f"{grand[5]:,}", fmt_tok(grand[0]), fmt_tok(grand[1]), fmt_tok(grand[2]),
                 fmt_tok(grand[3]), fmt_tok(sum(grand[:4])), fmt_cost(grand[4])])
    print()
    print(table(["Feature / task kind", "Model(s)", "Calls", "Fresh input", "Cache write", "Cache read", "Output",
                 "Total", "Est. cost"], body, left=2))
    print(f"\n{NOTE}")


def cmd_feature(conn, cfg, flt, text):
    ex, pr = "feature LIKE ?", [f"%{text}%"]
    names = [r[0] for r in conn.execute(f"SELECT DISTINCT feature FROM calls WHERE {flt.where(ex, pr)[0]}", flt.where(ex, pr)[1])]
    print(header(conn, cfg, flt, f"Feature matching '{text}': {', '.join(names) or 'none'}"))
    if not names:
        return
    print(breakdown_table("By task kind", grouped(conn, flt, "kind", ex, pr), "Task kind"))
    print(breakdown_table("By model", [(short_model(r[0]),) + r[1:] for r in grouped(conn, flt, "model", ex, pr)], "Model"))
    w, p = flt.where(ex, pr)
    print("\nSessions")
    rows = conn.execute(
        f"SELECT session_id, MIN(day), MAX(day), COUNT(*), SUM(est_cost_usd) FROM calls WHERE {w} "
        f"GROUP BY session_id ORDER BY SUM(est_cost_usd) DESC LIMIT 25", p).fetchall()
    titles = dict(conn.execute("SELECT session_id, title FROM sessions"))
    print(table(["Session", "From", "To", "Calls", "Est. cost", "Title"],
                [[r[0][:8], r[1], r[2], f"{r[3]:,}", fmt_cost(r[4]), (titles.get(r[0]) or "")[:50]] for r in rows], left=3))
    print("\nAgent runs")
    rows = conn.execute(
        f"SELECT agent_type, COUNT(DISTINCT agent_id), COUNT(*), SUM({TOK}), SUM(est_cost_usd) FROM calls "
        f"WHERE {w} AND agent_id IS NOT NULL GROUP BY agent_type ORDER BY SUM(est_cost_usd) DESC", p).fetchall()
    print(table(["Agent type", "Runs", "Calls", "Total tokens", "Est. cost"],
                [[r[0] or "?", r[1], f"{r[2]:,}", fmt_tok(r[3]), fmt_cost(r[4])] for r in rows]))
    print("\nTop 10 costliest calls")
    rows = conn.execute(
        f"SELECT ts, model, kind, {TOK}, est_cost_usd FROM calls WHERE {w} ORDER BY est_cost_usd DESC LIMIT 10", p).fetchall()
    out = []
    for r in rows:
        t = dt.datetime.fromisoformat(r[0].replace("Z", "+00:00")).astimezone(cfg.tz)
        out.append([f"{t:%Y-%m-%d %H:%M}", short_model(r[1]), r[2], fmt_tok(r[3]), fmt_cost(r[4])])
    print(table(["Time", "Model", "Task kind", "Tokens", "Est. cost"], out, left=3))
    print(f"\n{NOTE}")


def cmd_daily(conn, cfg, flt, days):
    since = (dt.datetime.now(cfg.tz).date() - dt.timedelta(days=days - 1)).isoformat()
    ex, pr = "day >= ?", [since]
    print(header(conn, cfg, flt, f"Daily, last {days} days"))
    w, p = flt.where(ex, pr)
    top = [r[0] for r in conn.execute(f"SELECT model FROM calls WHERE {w} GROUP BY model ORDER BY SUM(est_cost_usd) DESC LIMIT 4", p)]
    per = {}
    for d, m, tot, cost in conn.execute(f"SELECT day, model, SUM({TOK}), SUM(est_cost_usd) FROM calls WHERE {w} GROUP BY day, model", p):
        e = per.setdefault(d, {"tot": 0, "cost": 0.0, "m": {}})
        e["tot"] += tot or 0
        e["cost"] += cost or 0
        e["m"][m] = e["m"].get(m, 0) + (cost or 0)
    body = []
    for d in sorted(per, reverse=True):
        e = per[d]
        other = e["cost"] - sum(e["m"].get(m, 0) for m in top)
        body.append([d, fmt_tok(e["tot"]), fmt_cost(e["cost"])] + [fmt_cost(e["m"].get(m, 0)) for m in top] + [fmt_cost(other)])
    gt = sum(e["tot"] for e in per.values())
    gc = sum(e["cost"] for e in per.values())
    body.append(["GRAND TOTAL", fmt_tok(gt), fmt_cost(gc)] + [""] * (len(top) + 1))
    print()
    print(table(["Day", "Total tokens", "Est. cost"] + [f"{short_model(m)} $" for m in top] + ["other $"], body))
    print(f"\nGRAND TOTAL: {fmt_tok(gt)} tokens, est. cost {fmt_cost(gc)}\n{NOTE}")


def cmd_check(conn, cfg, flt_all, run_ingest):
    ok = True
    w, p = flt_all.where()
    q = lambda col: [r for r in conn.execute(f"SELECT {col}, SUM({TOK}) FROM calls WHERE {w} GROUP BY {col}", p)]
    grand = conn.execute(f"SELECT SUM({TOK}), COUNT(*) FROM calls WHERE {w}", p).fetchone()
    by = {c: sum(r[1] or 0 for r in q(c)) for c in ("feature", "model", "day")}
    print(f"Grand total tokens:  {grand[0]:,}   calls: {grand[1]:,}")
    for c, v in by.items():
        good = v == grand[0]
        ok &= good
        print(f"  sum by {c:<8} {v:,}  {'OK' if good else 'MISMATCH'}")
    unp = conn.execute("SELECT model, COUNT(*) FROM calls WHERE est_cost_usd IS NULL GROUP BY model").fetchall()
    print(f"Unpriced rows: {sum(r[1] for r in unp)}" + (" -> " + ", ".join(f"{m}={n}" for m, n in unp) if unp else ""))
    n_before = grand[1]
    added = run_ingest()
    n_after = conn.execute("SELECT count(*) FROM calls").fetchone()[0]
    good = (n_after - n_before) == 0
    ok &= good
    print(f"Re-ingest (full re-read) added {n_after - n_before} rows  {'OK' if good else 'FAIL'}")
    # no message text: take prompts from the logs, look for them in the database file
    texts = _sample_prompts(cfg, 3)
    with open(cfg.db_path, "rb") as f:
        blob = f.read()
    hits = sum(1 for t in texts if t.encode() in blob)
    ok &= hits == 0 and bool(texts)
    print(f"Message-text check: {len(texts)} prompt strings sampled from the logs, {hits} found in the database  "
          f"{'OK' if hits == 0 and texts else 'FAIL'}")
    print("ALL CHECKS PASSED" if ok else "SOME CHECKS FAILED")
    return 0 if ok else 1


def _sample_prompts(cfg, n):
    import glob
    import json
    from .sources import claude_code
    files = []
    for _, base in claude_code.config_dirs(cfg):
        files += glob.glob(os.path.join(base, "projects", "*", "*.jsonl"))
    random.shuffle(files)
    out = []
    for fpath in files[:40]:
        try:
            with open(fpath, "rb") as f:
                for raw in f:
                    if b'"type":"user"' in raw[:400] or b'"type":"user"' in raw[-600:]:
                        try:
                            d = json.loads(raw)
                        except ValueError:
                            continue
                        c = (d.get("message") or {}).get("content")
                        if isinstance(c, str) and len(c) >= 40 and "<" not in c[:2]:
                            out.append(c[:60])
                            break
        except OSError:
            continue
        if len(out) >= n:
            break
    return out
