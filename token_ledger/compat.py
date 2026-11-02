"""Cross-platform helpers (Linux, macOS, Windows). Standard library only."""
import os
import re
import subprocess
import sys
import tempfile
import webbrowser

IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"


def utf8_stdio():
    """Windows consoles default to a legacy code page and crash on ✓, ⚠ and box characters; force UTF-8."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def slug(path):
    """Claude Code's folder name for a working directory: every character that is not a letter or digit becomes '-'.
    /home/me/proj -> -home-me-proj ; E:\\My Projects\\site -> E--My-Projects-site"""
    return re.sub(r"[^A-Za-z0-9]", "-", os.path.abspath(path))


def posix(path):
    """Forward slashes, so path rules written as `site/` or `wiki/plans/` match Windows paths too."""
    return (path or "").replace("\\", "/")


def same_or_under(path, root):
    """True when `path` is `root` or inside it; case-insensitive on Windows."""
    if not path or not root:
        return False
    p = os.path.normcase(os.path.normpath(path))
    r = os.path.normcase(os.path.normpath(root))
    return p == r or p.startswith(r.rstrip(os.sep) + os.sep)


def detached_kwargs():
    """Popen arguments that let a child outlive this process without a console window of its own."""
    if IS_WIN:
        flags = getattr(subprocess, "DETACHED_PROCESS", 0x8) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)
        flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        return {"creationflags": flags}
    return {"start_new_session": True}


def open_file(path):
    """Open a local file (the HTML dashboard) in the default browser."""
    try:
        if IS_WIN:
            os.startfile(path)  # noqa: S606 - local file the tool just wrote
            return True
        return webbrowser.open("file://" + os.path.abspath(path))
    except (OSError, webbrowser.Error):
        return False


VENV_NAME = ".venv-win" if IS_WIN else ".venv-watch"


def venv_python(root, name=None):
    """Interpreter inside the tool's virtual environment, or None. Windows and POSIX use separate folders so a
    checkout shared between both (or copied from one to the other) never mixes them."""
    sub = ("Scripts", "python.exe") if IS_WIN else ("bin", "python")
    p = os.path.join(root, name or VENV_NAME, *sub)
    return p if os.path.exists(p) else None


def temp_dir(*parts):
    return os.path.join(tempfile.gettempdir(), "agent-monitor", *parts)
