"""Source adapter registry. An adapter module exposes NAME and scan(cfg, seen); see README.md."""
import importlib

_KNOWN = {"claude-code": "claude_code"}


def get(name):
    mod = _KNOWN.get(name)
    if mod is None:
        raise KeyError(f"unknown source '{name}' (known: {', '.join(sorted(_KNOWN))})")
    return importlib.import_module(f"{__name__}.{mod}")


def enabled(cfg):
    out = []
    for name in cfg.sources:
        try:
            out.append(get(name))
        except KeyError:
            continue
    return out
