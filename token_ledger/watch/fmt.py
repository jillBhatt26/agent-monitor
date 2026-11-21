"""Whole-unit duration text for the watch UI (no decimals)."""


def human_age(seconds):
    """45 s, 42 min, 3 h 12 min, 2 d 5 h, 3 mo, 1 y 2 mo."""
    s = max(0, int(seconds or 0))
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min"
    if s < 86400:
        h, m = divmod(s // 60, 60)
        return f"{h} h {m} min" if m else f"{h} h"
    d = s // 86400
    if d < 30:
        h = (s % 86400) // 3600
        return f"{d} d {h} h" if h else f"{d} d"
    mo = d // 30
    if mo < 12:
        return f"{mo} mo"
    y, mo = divmod(mo, 12)
    return f"{y} y {mo} mo" if mo else f"{y} y"


def human_ms(ms):
    """Under a second stays in ms; longer uses human_age."""
    ms = max(0, int(ms or 0))
    return f"{ms} ms" if ms < 1000 else human_age(ms // 1000)
