#!/usr/bin/env python3
"""Install Agent Monitor (`tokens`, `agent-watch`) for the current user on Linux, macOS or Windows. Safe to run twice.

    python install.py                 venv + Textual, commands on PATH, config file; prints the hook snippet
    python install.py --apply-hooks   also add the Claude Code hooks (settings are backed up first)
    python install.py --no-watch      ledger only: skip the virtual environment and Textual

Nothing is deleted: an existing config, venv or hook entry is left as it is.
"""
import glob
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
from token_ledger.compat import IS_WIN, VENV_NAME, venv_python  # noqa: E402

BIN = os.path.join(os.path.expanduser("~"), ".local", "bin")
CONF = os.path.expanduser(os.environ.get("TOKEN_LEDGER_CONFIG_DIR") or "~/.config/token-ledger")
DATA = os.path.expanduser(os.environ.get("TOKEN_LEDGER_DATA_DIR") or "~/.local/share/token-ledger")
TOKENS = os.path.join(HERE, "tokens")
WATCH = os.path.join(HERE, "agent-watch")
PACKAGES = ["textual"] + (["tzdata"] if IS_WIN else [])

CONFIG_TEMPLATE = """# Agent Monitor / token-ledger settings. Every line is optional.
data_dir = "{data}"
# machine = "my-laptop"            # name shown in reports; default is the computer's host name
sources = ["claude-code"]
# extra_claude_dirs = ["~/work/.claude-client"]   # more Claude config folders (~/.claude* is found automatically)
# price_file = "~/.config/token-ledger/prices.json"  # your own copy of prices.json
# timezone = "Asia/Kolkata"        # calendar-day bucketing; default is this computer's time zone

[watch]
# main_save_tokens = 120000        # MAIN context gauge turns amber: time to checkpoint
# main_compact_tokens = 160000     # turns red: compact now
# burn_window_minutes = 10
# alerts = true                    # bell + toast when an agent stalls or goes over budget
# plans_dir = "~/my-project/wiki/plans"   # default: <project>/wiki/plans when it exists
# workspace = "~/my-project"       # folder `x` (run separately) starts claude in; default: the project's folder

[watch.agent_budget_tokens]        # peak context per agent run, by model family
# opus = 80000
# sonnet = 60000
# haiku = 30000
# default = 60000
"""


def step(msg):
    print(f"- {msg}")


def check_python():
    if sys.version_info < (3, 9):
        sys.exit("Python 3.9 or newer is needed, found %d.%d" % sys.version_info[:2])


def make_venv():
    py = venv_python(HERE)
    if not py:
        subprocess.check_call([sys.executable, "-m", "venv", os.path.join(HERE, VENV_NAME)])
        py = venv_python(HERE)
        step(f"created {VENV_NAME}")
    missing = [p for p in PACKAGES if subprocess.call([py, "-c", f"import {p}"], stderr=subprocess.DEVNULL) != 0]
    if missing:
        subprocess.check_call([py, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", *missing])
        step(f"installed {', '.join(missing)} into {VENV_NAME}")
    else:
        step(f"{', '.join(PACKAGES)} already in {VENV_NAME}")
    return py


def link_commands(py):
    os.makedirs(BIN, exist_ok=True)
    if IS_WIN:
        runner = py or sys.executable
        for name, script in (("tokens", TOKENS), ("agent-watch", WATCH)):
            path = os.path.join(BIN, name + ".cmd")
            with open(path, "w", encoding="utf-8", newline="\r\n") as f:
                f.write(f'@echo off\n"{runner}" "{script}" %*\n')
            step(f"wrote {path}")
    else:
        for name, script in (("tokens", TOKENS), ("agent-watch", WATCH)):
            os.chmod(script, 0o755)
            link = os.path.join(BIN, name)
            if os.path.lexists(link) and os.path.realpath(link) != os.path.realpath(script):
                step(f"{link} points elsewhere; left as it is")
                continue
            if not os.path.lexists(link):
                os.symlink(script, link)
            step(f"linked {link} -> {script}")
    on_path = any(os.path.normcase(os.path.normpath(p)) == os.path.normcase(os.path.normpath(BIN))
                  for p in os.environ.get("PATH", "").split(os.pathsep) if p)
    if not on_path:
        print(f"  NOTE: add {BIN} to your PATH to run 'tokens' and 'agent-watch' from any folder.")


def write_config():
    os.makedirs(CONF, exist_ok=True)
    os.makedirs(DATA, exist_ok=True)
    toml, js = os.path.join(CONF, "config.toml"), os.path.join(CONF, "config.json")
    if os.path.exists(toml) or os.path.exists(js):
        step(f"config already exists in {CONF} (left as it is)")
        return
    with open(toml, "w", encoding="utf-8") as f:
        f.write(CONFIG_TEMPLATE.format(data=DATA.replace("\\", "/")))
    step(f"created {toml}")


def hook_command(py, sub):
    """Quoted paths: an unquoted path with a space breaks the hook (and a failing hook can block every tool call)."""
    if IS_WIN:  # forward slashes: Claude Code may run hooks through Git Bash, where backslashes are fragile
        return f'"{(py or sys.executable).replace(os.sep, "/")}" "{TOKENS.replace(os.sep, "/")}" {sub}'
    return f'"{TOKENS}" {sub}'


def apply_hooks(py):
    want = {"SessionStart": hook_command(py, "hook-session-start"), "UserPromptSubmit": hook_command(py, "hook-prompt")}
    files = sorted(glob.glob(os.path.join(os.path.expanduser("~"), ".claude*", "settings.json")))
    if not files:
        step("no ~/.claude*/settings.json found; hooks not added")
    for path in files:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        hooks = data.setdefault("hooks", {})
        changed = False
        for event, cmd in want.items():
            entries = hooks.setdefault(event, [])
            sub = cmd.split()[-1]
            if any(sub in h.get("command", "") and "tokens" in h.get("command", "")
                   for e in entries for h in e.get("hooks", [])):
                continue
            entries.append({"hooks": [{"type": "command", "command": cmd, "timeout": 15}]})
            changed = True
        if changed:
            backup = f"{path}.bak-agent-monitor-{time.strftime('%Y%m%d%H%M%S')}"
            shutil.copy2(path, backup)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
                f.write("\n")
            step(f"hooks added to {path} (backup: {backup})")
        else:
            step(f"hooks already present in {path}")


def self_check(py):
    runner = py or sys.executable
    for script in (TOKENS, WATCH):
        r = subprocess.run([runner, script, "--help"], capture_output=True, text=True)
        if r.returncode != 0:
            sys.exit(f"self-check failed: {os.path.basename(script)} --help exited {r.returncode}\n{r.stderr[-800:]}")
    step("self-check passed (tokens --help, agent-watch --help)")


def main(argv):
    check_python()
    watch = "--no-watch" not in argv
    print(f"Installing Agent Monitor from {HERE}")
    py = make_venv() if watch else None
    link_commands(py)
    write_config()
    if "--apply-hooks" in argv:
        apply_hooks(py)
    else:
        snippet = {"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": hook_command(py, "hook-session-start")}]}],
                             "UserPromptSubmit": [{"hooks": [{"type": "command", "command": hook_command(py, "hook-prompt")}]}]}}
        print("\nHooks are NOT applied. They track accounts, keep history fresh and deliver stop/restart requests.\n"
              "Run `python install.py --apply-hooks`, or merge this into ~/.claude/settings.json:")
        print(json.dumps(snippet, indent=2))
    self_check(py)
    print("\nDone. Try:  tokens ingest   then   tokens today   and   agent-watch")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
