"""Speed-based ETA: remaining work units divided by measured units per active hour. Stdlib only."""
import statistics
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

from .events import agent_spans, load_events, parse_ts, record_changes  # noqa: F401

WEIGHTS = {"plan": 1.0, "waiting-go": 0.0, "build": 4.0, "local-test": 2.0, "stage": 1.0,
           "stage-test": 1.0, "release": 1.0, "prod-check": 1.0}
ORDER = ["plan", "waiting-go", "build", "local-test", "stage", "stage-test", "release", "prod-check"]
LOCAL_PHASES = ("build", "local-test")
STAGE_PHASES = ("stage", "stage-test")

PRIOR_SPEED = 1.0          # units per active hour until finished plans give a median
WINDOW_H = 6.0             # look-back for the speed average
HALF_LIFE_H = 2.0          # recent events count more
PRIOR_HOURS = 1.0          # prior counts as this many active hours of data
ACTIVE_PAD_S = 30 * 60     # an event keeps the plan "active" for this long
LIVE_S = 120               # agent transcript written this recently = live agent
GRACE_S = 30 * 60          # idle time before the speed starts to decay
DECAY_PER_H = 0.5
STUCK_S = 2 * 3600
MAX_DECAY_H = 6.0          # decay stops growing after this much idle time
LOW_F, HIGH_F = 1.3, 0.7


@dataclass
class Eta:
    low_h: Optional[float]
    high_h: Optional[float]
    stuck: bool
    waiting_owner: bool
    speed: float
    label: str


def _fraction_left(plan) -> float:
    if plan.phase in LOCAL_PHASES:
        pct = plan.local_pct
    elif plan.phase in STAGE_PHASES:
        pct = plan.stage_pct
    else:
        pct = None
    return 1.0 if pct is None else max(0.0, 1.0 - pct / 100.0)


def remaining_units(plan) -> float:
    if plan.phase not in ORDER:
        return 0.0
    i = ORDER.index(plan.phase)
    rest = sum(WEIGHTS[p] for p in ORDER[i + 1:])
    return rest + WEIGHTS[plan.phase] * _fraction_left(plan)


def _event_units(e: dict, plan) -> float:
    kind = e.get("kind")
    if kind == "phase":
        return WEIGHTS.get(e.get("from"), 0.0)
    if kind in ("tick_local", "tick_stage", "untick"):
        lane = e.get("from") if kind == "untick" else kind[5:]
        phase = e.get("phase_hint") or plan.phase
        w = WEIGHTS.get(phase, 1.0) or 1.0
        n = max(1, sum(1 for i in plan.checklist if getattr(i, lane, None) != "void")) if plan else 4
        return (-w if kind == "untick" else w) / n
    return 0.0


def _union_hours(spans: List[tuple], lo: float, hi: float, now: float, tau: float) -> float:
    """Recency-weighted hours covered by the union of spans inside [lo, hi]."""
    clipped = sorted((max(a, lo), min(b, hi)) for a, b in spans if b > lo and a < hi)
    total, cur_a, cur_b = 0.0, None, None
    merged = []
    for a, b in clipped:
        if cur_b is None or a > cur_b:
            if cur_b is not None:
                merged.append((cur_a, cur_b))
            cur_a, cur_b = a, b
        else:
            cur_b = max(cur_b, b)
    if cur_b is not None:
        merged.append((cur_a, cur_b))
    for a, b in merged:
        mid = (a + b) / 2
        total += (b - a) / 3600.0 * 0.5 ** ((now - mid) / 3600.0 / tau)
    return total


def _activity_spans(plan_events: List[dict], agents: List[dict], now: float) -> List[tuple]:
    sp = [(e["t"] - ACTIVE_PAD_S, e["t"]) for e in plan_events]
    for a in agents:
        end = a["end"] + (ACTIVE_PAD_S if now - a["end"] <= LIVE_S else 0)
        sp.append((a["start"], min(end, now)))
    return sp


def measured_speed(plan, events: List[dict], agents: List[dict], now: float, prior: float):
    """(speed, active_hours_in_window). Units and active hours share the same recency weights."""
    plan_events = [e for e in events if e.get("plan") == plan.plan]
    lo = now - WINDOW_H * 3600
    units = sum(_event_units(e, plan) * 0.5 ** ((now - e["t"]) / 3600.0 / HALF_LIFE_H)
                for e in plan_events if lo <= e["t"] <= now)
    hours = _union_hours(_activity_spans(plan_events, agents, now), lo, now, now, HALF_LIFE_H)
    speed = (max(units, 0.0) + prior * PRIOR_HOURS) / (hours + PRIOR_HOURS)
    return speed, hours


def prior_speed(plans, events: List[dict], spans: Dict[str, List[dict]], now: float) -> Optional[float]:
    """Median units per active hour across finished plans that have event history."""
    speeds = []
    for p in plans:
        if p.phase != "done":
            continue
        ev = [e for e in events if e.get("plan") == p.plan]
        if not ev:
            continue
        units = sum(_event_units(e, p) for e in ev)
        hours = _union_hours(_activity_spans(ev, spans.get(p.plan, []), now), 0, now, now, 1e9)
        if units > 0 and hours > 0:
            speeds.append(units / hours)
    return statistics.median(speeds) if speeds else None


def fmt_range(low_h: float, high_h: float) -> str:
    def part(h, unit):
        v = h * {"min": 60, "h": 1, "d": 1 / 24}[unit]
        return str(max(1, round(v)))
    if high_h < 1:
        unit = "min"
    elif high_h < 24:
        unit = "h"
    else:
        unit = "d"
    a, b = part(low_h, unit), part(high_h, unit)
    return f"{a} {unit}" if a == b else f"{a}–{b} {unit}"


def estimate(plan, now: Optional[float] = None, events: Optional[List[dict]] = None,
             agents: Optional[List[dict]] = None, prior: Optional[float] = None) -> Eta:
    now = time.time() if now is None else now
    if plan.phase == "done":
        return Eta(0.0, 0.0, False, False, 0.0, "done")
    if plan.state == "paused":
        return Eta(None, None, False, False, 0.0, "paused")
    if plan.phase == "system":
        return Eta(None, None, False, False, 0.0, "")
    if getattr(plan, "guessed", False) and not (events or agents):
        return Eta(None, None, False, False, 0.0, "no board data")
    events = events or []
    agents = agents or []
    rough = prior is None
    speed, _ = measured_speed(plan, events, agents, now, PRIOR_SPEED if rough else prior)
    waiting = plan.state == "waiting-owner"

    last = [e["t"] for e in events if e.get("plan") == plan.plan] + [a["end"] for a in agents]
    if not last and plan.updated is not None:
        last.append(plan.updated.timestamp())
    idle = now - max(last) if last else 0.0
    stuck = False
    if not waiting and plan.state == "active" and idle > GRACE_S:
        speed *= DECAY_PER_H ** min((idle - GRACE_S) / 3600.0, MAX_DECAY_H)
        stuck = idle >= STUCK_S
    speed = max(speed, 1e-3)
    rem = remaining_units(plan)
    low, high = rem / (speed * LOW_F), rem / (speed * HIGH_F)
    text = fmt_range(low, high)
    if waiting:
        label = "⏸ waiting on you"
    else:
        label = text + (" rough" if rough else "")
        if stuck:
            label += " ⚠ stuck"
    return Eta(low, high, stuck, waiting, speed, label)


def load_context(plans, now: Optional[float] = None):
    """(events, spans, prior) as the estimate needs them; read-only."""
    now = time.time() if now is None else now
    events = load_events()
    spans = agent_spans()
    return events, spans, prior_speed(plans, events, spans, now)


def estimate_with_context(plan, ctx, now: Optional[float] = None) -> Eta:
    events, spans, prior = ctx
    return estimate(plan, now, events, spans.get(plan.plan, []), prior)


def main() -> None:
    from .plans import load_plans
    now = time.time()
    plans = load_plans()
    record_changes(plans, now)
    ctx = load_context(plans, now)
    for p in plans:
        e = estimate_with_context(p, ctx, now)
        print(f"{p.plan:<16} {p.phase:<10} {p.state:<14} {e.label}")


if __name__ == "__main__":
    main()
