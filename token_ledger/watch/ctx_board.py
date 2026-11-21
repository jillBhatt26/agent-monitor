"""Context dashboard: boards (cards) and small charts built from tools/ctx/monitor.py collect() data."""
import json
import os

from rich.columns import Columns
from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ..board import workspace
from .fmt import human_age, human_ms

OK, WARN, ALERT, INFO, MUTED, FG = "#6fbf73", "#e0b050", "#c96a6a", "#6aa9d8", "#8a8f98", "#e6e6e6"
HEADER_BG = "#5a1f1f"


def _window_from_settings(default=160_000):
    """autoCompactWindow from the workspace .claude/settings.json; the default when it is missing or unreadable."""
    try:
        with open(os.path.join(workspace(), ".claude", "settings.json"), encoding="utf-8") as f:
            v = json.load(f).get("autoCompactWindow")
        return int(v) if isinstance(v, int) and v > 0 else default
    except (OSError, ValueError, AttributeError):
        return default


# The auto-save sits 40k below the compaction window (120k of 160k).
WINDOW = _window_from_settings()
SAVE_AT = max(WINDOW - 40_000, 1)
SPARK = "▁▂▃▄▅▆▇█"
LEVELS = {"ok": ("OK", OK), "warn": ("WARN", WARN), "alert": ("ALERT", ALERT), "info": ("INFO", INFO)}

# which alert strings from monitor.alerts() belong to which card
AREA_KEYS = {"index": ("db at", "integrity", "index older", "no index run"), "guard": ("hook errors", "hook p95"),
             "restore": ("restore",), "secrets": ("redactions",), "growth": ("dirty.list",)}


CARD_KEYS = ("main", "index", "last", "search", "guard", "restore", "growth", "secrets", "root")
CARD_NAMES = {"main": "MAIN context", "index": "Index", "last": "Last index", "search": "Search", "guard": "Guard",
              "restore": "Restore", "growth": "Growth", "secrets": "Secrets", "root": "Index by root"}
DEFAULT_EXPLAIN = "Move with Tab or hover a card to see what it means."

# one dim line shown under each chart
EXPLAIN_SHORT = {
    "main": f"Bar = how full this chat's memory is. At the {SAVE_AT // 1000}k line it auto-saves; at "
            f"{WINDOW // 1000}k it compacts. Small bars below = size at each recent message, oldest left.",
    "index": "Bar = size of the search index vs its size cap; line = 80% warning. Stale = files changed since last index.",
    "last": "When the search index was last rebuilt, how long it took and memory used.",
    "search": "Bars = searches per day. Misses = searches that found nothing.",
    "guard": "Green = commands the safety hook allowed vs denied. Hook errors = times the hook crashed and let the command through.",
    "restore": "Bar = size of the restore file after a context reset vs the 8 KB limit; small bars = recent sizes.",
    "growth": "Files over 200 KB get expensive to read; the biggest is shown.",
    "secrets": "Secrets found and masked before they reached the chat, so masking worked; counts by kind.",
    "root": "How much of each folder is in the search index (chunks = searchable pieces of files).",
}

# longer text for the Explain panel: what it measures, thresholds from monitor.alerts()/this file, what to do
EXPLAIN_LONG = {
    "main": f"Measures the context this MAIN chat carries on every call. OK under 80k, WARN from 80k, ALERT from "
            f"{SAVE_AT // 1000}k (the auto-save point; compaction at {WINDOW // 1000}k).\nIf WARN or ALERT: let the "
            "checkpoint save run, finish the task, and expect the chat to restore from the saved file instead of "
            "re-reading everything.",
    "index": "Measures the search index database against its size cap. ALERT when it is 80% of the cap or more, when the "
             "integrity check fails, or when the last run is over 24 h old; WARN when more than 100 files are stale.\n"
             "If WARN or ALERT: rebuild the index (ctx index build) so searches see current files.",
    "last": "Shows the age of the most recent index rebuild. OK within 24 h; ALERT when older or never run.\n"
            "If ALERT: run the ctx index build; if it keeps failing, check the time and memory shown here.",
    "search": "Counts searches made through the index. This card is information only and never turns WARN or ALERT.\n"
              "Many misses or 0-hit searches mean people search for things that are not indexed or word them oddly: "
              "see the Misses view (m).",
    "guard": "Counts what the safety hook allowed and denied. ALERT when any hook error happened or the slowest 5% of "
             "calls (p95) took over 100 ms.\nIf ALERT: open the Guard view (g), look at the errors and denied files, "
             "and fix the hook before trusting it.",
    "restore": "Measures the file used to restore a chat after a reset. ALERT when the biggest recent one is over 8192 "
               "bytes (8 KB), because a big restore wastes context.\nIf ALERT: trim the saved notes so they stay under the limit.",
    "growth": "Counts files over 200 KB, which cost a lot to read. WARN when more than 10; ALERT when the dirty list "
              "(files waiting to be indexed) is older than 24 h.\nIf WARN or ALERT: split or archive the biggest files; "
              "if the dirty list is old, run the index.",
    "secrets": "Counts secrets found and masked before they reached the chat. Shown as WARN whenever any exist, never "
               "as a red alert: masking worked.\nIf the number jumps, find which kind is leaking into notes or files and "
               "stop writing it there.",
    "root": "Shows each indexed folder: chunks are searchable pieces, files how many files they came from. Information "
            "only, no OK/WARN/ALERT.\nA folder with no or very few chunks is probably not being indexed.",
}


def tok(n):
    n = int(n or 0)
    return f"{n / 1e6:.1f}M" if n >= 1_000_000 else f"{n / 1000:.0f}k" if n >= 1000 else str(n)


def pill(level):
    name, col = LEVELS[level]
    return Text(f" {name} ", style=f"bold #101010 on {col}")


def bar(frac, width, color, marks=()):
    """Block bar; marks = fractions drawn as a thin divider inside the track."""
    width = max(width, 4)
    fill = max(0, min(width, round(frac * width)))
    cells = [("█", color) if i < fill else ("░", "#4a4f57") for i in range(width)]
    for m in marks:
        i = min(width - 1, int(m * width))
        cells[i] = ("│", FG)
    t = Text()
    for ch, col in cells:
        t.append(ch, style=col)
    return t


def spark(vals, width, ceiling=None, color=INFO):
    vals = [v for v in vals if v is not None][-width:]
    if not vals:
        return Text("no history yet", style=MUTED)
    top = ceiling or max(vals) or 1
    t = Text()
    for v in vals:
        t.append(SPARK[max(0, min(7, int(v / top * 7.999)))], style=color)
    return t


def level_for(areas, alerts, area, base="ok"):
    hit = any(k in a for a in alerts for k in AREA_KEYS[area])
    return "alert" if hit else base


def card(title, level, big, label, body, width, key=None):
    head = Table.grid(expand=True)
    head.add_column(ratio=1)
    head.add_column(justify="right")
    head.add_row(Text(title, style=f"bold {FG}"), pill(level))
    big_t = Text()
    big_t.append(str(big), style=f"bold {LEVELS[level][1]}")
    big_t.append("  " + label, style=MUTED)
    expl = [Text(EXPLAIN_SHORT[key], style=MUTED)] if key else []
    return Panel(Group(head, big_t, *body, *expl), width=width, border_style="#3c424b", padding=(0, 1))


def main_card(mains, width):
    main = mains[0] if mains else None
    if main is None:
        return Panel(Text("No MAIN session found yet.", style=MUTED), title="MAIN context", border_style="#3c424b", width=width)
    ctx = int(main.context_tokens or 0)
    level = "alert" if ctx >= SAVE_AT else "warn" if ctx >= 80_000 else "ok"
    col = LEVELS[level][1]
    inner = max(width - 4, 20)
    head = Table.grid(expand=True)
    head.add_column(ratio=1)
    head.add_column(justify="right")
    big = Text()
    big.append(f"{tok(ctx)}", style=f"bold {col}")
    big.append(f" / {tok(WINDOW)}  ({ctx / WINDOW * 100:.0f}% of window)", style=MUTED)
    head.add_row(big, pill(level))
    b = bar(ctx / WINDOW, inner, col, marks=[SAVE_AT / WINDOW])
    pos = int(SAVE_AT / WINDOW * inner)
    scale = Text(" " * max(pos - 8, 0) + f"{SAVE_AT // 1000}k auto-save", style=MUTED)
    scale.append(" " * max(inner - len(scale.plain) - 12, 1) + f"{WINDOW // 1000}k compact", style=MUTED)
    hist = list(getattr(main, "context_history", []) or [])
    hist = [h for h in hist if h]
    body = [head, b, scale]
    if hist:
        body.append(Text("context per call (older to newer)", style=MUTED))
        body.append(spark(hist, inner, WINDOW, col))
    else:
        body.append(Text("per-call history not available for this session yet", style=MUTED))
    body.append(Text(EXPLAIN_SHORT["main"], style=MUTED))
    return Panel(Group(*body), title="MAIN context", title_align="left", border_style="#3c424b", width=width)


def overview_parts(d, mains, alerts, width):
    """Pieces of the Overview tab: head, main panel, [(key, card panel)], root panel, columns."""
    width = max(width - 2, 60)
    ncol = 3 if width >= 108 else 2
    cw = width // ncol
    inner = cw - 4
    A = list(alerts or [])
    i, s, m, g, r = d["index"], d["search"], d["misses"], d.get("guard"), d.get("restore")
    gr, sc = d.get("growth") or {}, d.get("secrets") or {}
    cards = []

    lvl = level_for(0, A, "index", "warn" if i.get("stale", 0) > 100 else "ok")
    cards.append(card("Index", lvl, f"{i['db_bytes'] / 1048576:.0f} MB", f"{i['pct_cap']}% of cap · stale {i['stale']}",
                      [bar(i["pct_cap"] / 100, inner, LEVELS[lvl][1], marks=[0.8]),
                       Text(f"integrity {i['integrity']}", style=MUTED)], cw, "index"))

    lr = i.get("last_run")
    cards.append(card("Last index", "ok" if lr and lr["age_h"] <= 24 else "alert",
                      human_age(lr["age_h"] * 3600) if lr else "none", "ago" if lr else "since last run",
                      [Text(f"took {human_ms(lr['ms'])} · rss {lr['peak_rss_mb']} MB" if lr else "no run recorded", style=MUTED)], cw, "last"))

    pd = list((s.get("per_day") or {}).values())
    cards.append(card("Search", "ok", s["count"], f"calls · avg {s['tokens_avg']} tok",
                      [spark(pd, inner, color=INFO), Text(f"misses {m['count']} (0-hit {m['zero_hit']})", style=MUTED)], cw, "search"))

    if g:
        tot = (g["allowed"] + g["denied"]) or 1
        lvl = level_for(0, A, "guard")
        gb = bar(g["allowed"] / tot, inner, OK)
        cards.append(card("Guard", lvl, g["hook_errors"], f"hook errors · p95 {human_ms(g['hook_ms_p95'])}",
                          [gb, Text(f"allowed {g['allowed']}  denied {g['denied']}", style=MUTED)], cw, "guard"))
    else:
        cards.append(card("Guard", "info", "-", "no data yet", [], cw, "guard"))

    if r:
        lim = max(int(r.get("limit") or 8192), 1)
        lvl = level_for(0, A, "restore")
        cards.append(card("Restore", lvl, f"{r['last_bytes']} B", f"max {r['max_bytes']} · limit {lim}",
                          [bar(r["last_bytes"] / lim, inner, LEVELS[lvl][1]), spark(r.get("series") or [], inner, lim)], cw, "restore"))
    else:
        cards.append(card("Restore", "info", "-", "no data yet", [], cw, "restore"))

    big = gr.get("big_files") or []
    top = f"{os.path.basename(big[0]['path'])} {big[0]['bytes'] / 1048576:.1f} MB" if big else "none"
    cards.append(card("Growth", level_for(0, A, "growth", "warn" if len(big) > 10 else "ok"), len(big), "files > 200 KB",
                      [Text(top, style=MUTED, no_wrap=True, overflow="ellipsis")], cw, "growth"))

    kinds = sorted((sc.get("by_kind") or {}).items(), key=lambda kv: -kv[1])
    mx = max([v for _, v in kinds] or [1])
    rows = [Text.assemble((f"{k[:10]:<11}", MUTED), bar(v / mx, max(inner - 18, 6), INFO), (f" {v}", FG)) for k, v in kinds[:4]]
    slvl = "warn" if sc.get("today", 0) > 0 or sc.get("total", 0) else "ok"
    if level_for(0, A, "secrets") == "alert":
        slvl = "warn"
    cards.append(card("Secrets", slvl, sc.get("total", 0), f"redacted ({sc.get('today', 0)} today) · the filter worked",
                      rows, cw, "secrets"))

    roots = sorted((i.get("roots") or {}).items(), key=lambda kv: -kv[1]["chunks"])
    mx = max([v["chunks"] for _, v in roots] or [1])
    lab = max([len(k) for k, _ in roots] + [4])
    rt = [Text.assemble((f"{k:<{lab}} ", MUTED), bar(v["chunks"] / mx, max(width - lab - 44, 10), INFO),
                        (f" {v['chunks']} chunks · {v['files']} files", FG)) for k, v in roots]
    rt = rt or [Text("none", style=MUTED)]
    rt.append(Text(EXPLAIN_SHORT["root"], style=MUTED))
    root_panel = Panel(Group(*rt), title="Index by root (chunks)", title_align="left",
                       border_style="#3c424b", width=width)

    head = Text()
    head.append("ALL OK" if not A else "ALERTS: " + "; ".join(A), style=f"bold {OK if not A else ALERT}")
    head.append(f"   window {d.get('since_days', '')}d · {str(d.get('generated', ''))[:19]}", style=MUTED)
    keyed = list(zip(CARD_KEYS[1:-1], cards))
    return {"head": head, "main": main_card(mains, width), "cards": keyed, "root": root_panel, "ncol": ncol, "cw": cw}


def overview(d, mains, alerts, width):
    """Rich renderable for the Overview tab (all parts stacked)."""
    p = overview_parts(d, mains, alerts, width)
    return Group(p["head"], p["main"], Columns([c for _, c in p["cards"]], padding=(0, 0), expand=False), p["root"])
