# Source adapters

A source adapter reads one LLM tool's local usage logs and hands token-ledger normalized call records.
It never reads or stores message text: only numbers and small labels.

## Contract

A module in this folder, registered in `__init__.py` (`_KNOWN`), with:

```python
NAME = "my-tool"

def scan(cfg, seen):
    """Yield one unit per session whose files changed.
    cfg  = token_ledger.config.Config (cfg.machine, cfg.extra_claude_dirs, ...)
    seen = {path: (size, mtime)} of files already imported; skip a unit when all its files match."""
    yield {
        "files": [(path, size, mtime), ...],   # every file the unit was built from
        "session_id": "...", "project": "...", "config_dir": "...",
        "title": "session name or None",
        "calls": [call, ...],                  # main-session calls first, then agent calls
    }
```

Each `call` is a dict:

| Key | Meaning |
|---|---|
| source, machine, config_dir | `NAME`, `cfg.machine`, the tool's config folder label |
| project, session_id | labels |
| agent_id, agent_type | `None` for the main conversation, else the sub-agent's id and type |
| request_id | unique per model call; one call may be logged on several lines, merge them and keep the largest output |
| ts | UTC ISO time (`2026-10-16T08:01:02.345Z`) |
| model, speed, effort | model id (matched to prices.json by substring), `standard`/`fast`, effort label |
| input, cache_write_5m, cache_write_1h, cache_read, output, thinking | token counts (thinking is part of output) |
| tool_signals | ordered small labels describing what the call did (below) |
| session_title | name of the session if the tool records one |
| turn | integer, increases at every real user prompt (used to carry the last task kind through text-only replies) |
| tool_ids, spawn_id, depth | ids of tool calls this call made; for an agent call the id of the tool call that spawned it, and its nesting depth |
| git_branch, cwd | branch and working folder at the time (cwd is where `.token-ledger.toml` rules are looked up) |

`tool_signals` vocabulary (the rules engine in `classify.py` only understands these):
`write:<path>`, `read:<path>`, `ref:<path>` (a path mentioned in a shell command), `search`, `web`,
`cmd:test`, `cmd:git`, `cmd:ops`, `cmd:other`, `skill:<name>`, `spawn:<agent type>`, `branch:<name>`, `tool:other`.

## Adding another tool (for example Codex CLI)

1. Look at that tool's real log files first and write down where tokens and timestamps are. Do not guess the format.
2. Copy `claude_code.py`, replace the file discovery and the line parsing, keep the output contract above.
3. Register it in `__init__.py` and list it under `sources` in `~/.config/token-ledger/config.toml`.
4. Check: `tokens ingest`, then `tokens check` (the three totals must agree).
