"""Parse wiki plans into Plan records (board block, checklist, status log). Stdlib only."""
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Union

from . import workspace

_WS = workspace()
DEFAULT_PLANS_DIR = os.path.join(_WS, "wiki", "plans")
DEFAULT_EXTRAS = os.path.join(_WS, "tools", "state", "board-cards.json")

Tick = Union[bool, str]  # True, False or "void"

BOARD_RE = re.compile(r"<!--\s*board\b(.*?)-->", re.S)
ROW_RE = re.compile(r"^\s*\|(.+)\|\s*$")
BOX_RE = re.compile(r"^\s*[-*]\s*\[( |x|X)\]\s*(.+)$")


@dataclass
class ChecklistItem:
    n: str
    text: str
    local: Tick = False
    stage: Tick = False


@dataclass
class Plan:
    plan: str
    title: str = ""
    phase: str = "plan"
    state: str = "active"
    waiting_on: str = ""
    next: str = ""
    updated: Optional[datetime] = None
    path: str = ""
    guessed: bool = False
    source: str = ""
    checklist: List[ChecklistItem] = field(default_factory=list)
    status_log_tail: List[str] = field(default_factory=list)

    @property
    def local_pct(self) -> Optional[int]:
        return _pct(self.checklist, "local")

    @property
    def stage_pct(self) -> Optional[int]:
        return _pct(self.checklist, "stage")


def _pct(items, attr):
    counted = [getattr(i, attr) for i in items if getattr(i, attr) != "void"]
    if not counted:
        return None
    return round(100 * sum(1 for v in counted if v is True) / len(counted))


def _tick(cell: str) -> Tick:
    c = cell.strip().lower()
    if c.startswith("void"):
        return "void"
    return c.startswith("[x]")


def _parse_dt(s: str) -> Optional[datetime]:
    s = (s or "").strip()
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    return None


def parse_block(text: str) -> Optional[dict]:
    m = BOARD_RE.search(text)
    if not m:
        return None
    out = {}
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, _, val = line.partition(":")
        val = val.strip()
        if val[:1] in ('"', "'"):
            q = val[0]
            end = val.find(q, 1)
            val = val[1:end] if end > 0 else val[1:]
        else:
            val = re.sub(r"\s+#.*$", "", val).strip()
        out[key.strip()] = val
    return out


def _section(text: str, heading_re: str) -> List[str]:
    lines = text.splitlines()
    start = None
    level = 0
    for i, ln in enumerate(lines):
        m = re.match(r"^(#{1,6})\s+(.*)$", ln)
        if not m:
            continue
        if start is None:
            if re.search(heading_re, m.group(2), re.I):
                start, level = i + 1, len(m.group(1))
        elif len(m.group(1)) <= level:
            return lines[start:i]
    return lines[start:] if start is not None else []


def parse_checklist(text: str) -> List[ChecklistItem]:
    items: List[ChecklistItem] = []
    for ln in _section(text, r"^checklist\b"):
        m = ROW_RE.match(ln)
        if not m:
            continue
        cells = [c.strip() for c in m.group(1).split("|")]
        if len(cells) < 4 or cells[0] in ("#", "") or set(cells[0]) <= set("-: "):
            continue
        items.append(ChecklistItem(cells[0], cells[1], _tick(cells[2]), _tick(cells[3])))
    if items:
        return items
    n = 0
    for ln in _section(text, r"acceptance criteria"):
        m = BOX_RE.match(ln)
        if m:
            n += 1
            items.append(ChecklistItem(str(n), m.group(2).strip(), m.group(1).lower() == "x", False))
    return items


def _guess(text: str) -> tuple:
    m = re.search(r"^\*\*Status[^\n]*", text, re.M)
    s = m.group(0) if m else ""
    up = s.upper()
    for key, phase, state in (
        ("LIVE", "done", "active"),
        ("DEPLOYED", "done", "active"),
        ("PAUSED", "build", "paused"),
        ("BUILDING", "build", "active"),
        ("BUILT", "done", "active"),
        ("PLANNED", "plan", "active"),
    ):
        if key in up:
            return phase, state
    return "plan", "active"


def _title(text: str, fallback: str) -> str:
    m = re.match(r"^#\s+(.*)$", text.lstrip().splitlines()[0] if text.strip() else "")
    return m.group(1).strip() if m else fallback


def parse_plan(path: str) -> Plan:
    with open(path, encoding="utf-8") as f:
        text = f.read()
    base = os.path.basename(path)
    num = base.split("-", 1)[0]
    block = parse_block(text)
    p = Plan(plan=num, title=_title(text, base), path=path)
    if block:
        p.plan = block.get("plan", num) or num
        p.title = block.get("title") or p.title
        p.phase = block.get("phase", "plan")
        p.state = block.get("state", "active")
        p.waiting_on = block.get("waiting_on", "")
        p.next = block.get("next", "")
        p.updated = _parse_dt(block.get("updated", ""))
    else:
        p.phase, p.state = _guess(text)
        p.guessed = True
        try:
            p.updated = datetime.fromtimestamp(os.path.getmtime(path))
        except OSError:
            pass
    p.checklist = parse_checklist(text)
    log = [ln for ln in _section(text, r"^status log\b") if ln.strip()]
    p.status_log_tail = log[-5:]
    return p


def load_extras(extras: str) -> List[Plan]:
    try:
        with open(extras, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    out = []
    for d in data:
        p = Plan(
            plan=str(d.get("plan", "")),
            title=d.get("title", ""),
            phase=d.get("phase", "plan"),
            state=d.get("state", "active"),
            waiting_on=d.get("waiting_on", ""),
            next=d.get("next", ""),
            updated=_parse_dt(d.get("updated", "")),
            path=d.get("source", ""),
            source=d.get("source", ""),
        )
        for i, it in enumerate(d.get("checklist", []), 1):
            p.checklist.append(ChecklistItem(str(it.get("n", i)), it.get("text", ""),
                                             it.get("local", False), it.get("stage", False)))
        out.append(p)
    return out


def load_plans(plans_dir: str = DEFAULT_PLANS_DIR, extras: str = DEFAULT_EXTRAS) -> List[Plan]:
    plans = []
    if os.path.isdir(plans_dir):
        for name in sorted(os.listdir(plans_dir)):
            if re.match(r"^\d{3}-.*\.md$", name):
                try:
                    plans.append(parse_plan(os.path.join(plans_dir, name)))
                except (OSError, ValueError):  # unreadable or non-UTF-8 plan: skip it, keep the board alive
                    continue
    if extras:
        plans.extend(load_extras(extras))
    return plans


def phase_column(plan: Plan) -> str:
    """Board group for a plan: In execution / Planned / Paused / Done / System."""
    if plan.phase == "system":
        return "System"
    if plan.phase == "done":
        return "Done"
    if plan.state in ("paused", "waiting-owner"):
        return "Paused"
    if plan.phase in ("plan", "waiting-go"):
        return "Planned"
    return "In execution"


def _fmt_pct(v: Optional[int]) -> str:
    return "-" if v is None else f"{v}%"


def main() -> None:
    for p in load_plans():
        print(f"{p.plan:<16} {phase_column(p):<13} {p.phase:<10} {p.state:<14} "
              f"L={_fmt_pct(p.local_pct):<5} S={_fmt_pct(p.stage_pct):<5} {p.next[:60]}"
              f"{' (guessed)' if p.guessed else ''}")


if __name__ == "__main__":
    main()
