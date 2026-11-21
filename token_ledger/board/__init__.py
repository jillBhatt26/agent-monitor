"""Plan board data: reads the board block and checklist of each plan file."""
import os
import re


def workspace(start: str = "") -> str:
    """Folder holding .claude/repos.toml, found by walking up from `start` (default: cwd).

    TOKEN_LEDGER_WS overrides the walk so the board works from any folder.
    """
    env = os.environ.get("TOKEN_LEDGER_WS")
    if env:
        return os.path.abspath(env)
    d = os.path.abspath(start or os.getcwd())
    while True:
        if os.path.isfile(os.path.join(d, ".claude", "repos.toml")):
            return d
        up = os.path.dirname(d)
        if up == d:
            return os.path.abspath(start or os.getcwd())
        d = up


def project_slug(path: str) -> str:
    """Claude Code names a project's transcript folder after its path, every non-alphanumeric char as '-'."""
    return re.sub(r"[^A-Za-z0-9]", "-", os.path.abspath(path))
