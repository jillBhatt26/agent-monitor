# Agent Monitor

**A live, local monitor for Claude Code sessions and their sub-agents, plus a token ledger that tells you where your
tokens and money go.**

Agent Monitor reads the transcripts Claude Code already writes on your machine. It shows, in real time, what the main
chat and each sub-agent are doing, how big their context is, which ones are stalled or over budget, and how fast you
are burning tokens. Its ledger half stores the numbers (never the messages) in a local SQLite file and reports them by
**feature**, **kind of work**, **model**, **account** and **day / week / month / year**, in the terminal and as a
self-contained HTML dashboard.

- **Local and private.** No network calls, no telemetry. Only numbers and short labels are stored; message text is
  never read into the database (`tokens check` proves it).
- **Cross-platform.** Windows, macOS and Linux. Python 3.9+; the ledger uses only the standard library, the live
  monitor uses [Textual](https://textual.textualize.io/).
- **Zero setup for projects.** Every project Claude Code has used is picked up automatically; optional per-project
  rules make the reports sharper.

> Costs are **estimates** (list price × tokens from `token_ledger/prices.json`, source URL and date inside), not your
> bill.

---

## Contents

- [What you get](#what-you-get)
- [Install](#install)
- [Quick start](#quick-start)
- [The live monitor](#the-live-monitor-agent-watch)
- [The ledger: reports and dashboard](#the-ledger-reports-and-dashboard)
- [Configuration](#configuration)
- [Project rules (`.token-ledger.toml`)](#project-rules-token-ledgertoml)
- [Several accounts and machines](#several-accounts-and-machines)
- [How it works](#how-it-works)
- [Privacy](#privacy)
- [Development](#development)
- [Troubleshooting](#troubleshooting)
- [Uninstall](#uninstall)
- [Author](#author)
- [License](#license)

---

## What you get

| | |
|---|---|
| **Live agent panes** | One pane per sub-agent: task, model, status (WORKING / WAITING / STALLED / FINISHED), tools used, tokens, cost, last action in plain words. |
| **Context gauge** | The main session's context against your checkpoint (120k) and compact (160k) lines: green → amber "checkpoint" → red "COMPACT NOW". |
| **Agent context budgets** | Each agent's *peak* context against a budget for its model family (Opus 80k, Sonnet 60k, Haiku 30k by default). Over budget = its brief made it read too much. |
| **Burn rate** | Tokens processed per minute over the last 10 minutes, with an hourly cost estimate. |
| **Alerts** | Bell + toast, once each, when an agent stalls, goes over budget, or the main context hits the compact line. |
| **Troubleshooting** | Detail screen with status, last actions, pending tool call, repeated-call (loop) detection, last error, brief and timeline. |
| **Stop / restart requests** | Ask the main chat to stop or restart an agent from the monitor; delivered by a hook with your next message. |
| **Run separately** | Re-run an agent's brief as a detached `claude -p` in a new terminal (Windows Terminal, PowerShell, macOS Terminal, GNOME Terminal, Konsole). |
| **Analytics** | Per-session success rate, tokens, output, peak context, run time and commands — by agent, agent type and model. |
| **Ledger reports** | `today / week / month / year`, features, one feature in detail, daily table, period-over-period change. |
| **HTML dashboard** | One self-contained file: tiles, time chart by model or task kind, feature tree, costliest calls. |
| **Snapshot command** | `tokens agents [--json]` — the monitor's view as text or JSON, for scripts or for a model checking on its own agents. |

---

## Install

You need **Python 3.9 or newer** and **git**.

```bash
git clone https://github.com/jillBhatt26/agent-monitor.git
cd agent-monitor
```

**Windows** (PowerShell, cmd or Git Bash):

```powershell
python install.py
```

**Linux / macOS:**

```bash
./install.sh            # same as: python3 install.py
```

The installer is safe to run twice and never deletes anything. It:

1. creates a private virtual environment (`.venv-win` on Windows, `.venv-watch` elsewhere) with Textual
   (and `tzdata` on Windows, which has no built-in time-zone database);
2. puts the `tokens` and `agent-watch` commands in `~/.local/bin` (`.cmd` launchers on Windows, symlinks elsewhere)
   and tells you if that folder is not on your `PATH`;
3. writes a commented `~/.config/token-ledger/config.toml` if you have none;
4. runs a self-check (`tokens --help`, `agent-watch --help`).

### Hooks (recommended)

Two Claude Code hooks keep the ledger fresh, record which account is signed in, and deliver the monitor's
stop/restart requests:

```bash
python install.py --apply-hooks
```

This adds `hook-session-start` (SessionStart) and `hook-prompt` (UserPromptSubmit) to every `~/.claude*/settings.json`,
**after copying each file to `settings.json.bak-agent-monitor-<time>`**. Paths in the hook commands are quoted, and
the hooks never fail a session (any error is logged and ignored). Open a new Claude Code session afterwards.
Without `--apply-hooks` the installer only prints the JSON snippet so you can add it yourself.

---

## Quick start

```bash
tokens ingest           # import your Claude Code history (first run takes a few seconds)
tokens check            # totals agree three ways, re-import adds nothing, no message text stored
tokens today            # today's tokens and estimated cost
agent-watch             # live monitor for the project of the current folder
tokens html             # write and open the dashboard
```

---

## The live monitor (`agent-watch`)

```bash
agent-watch                                  # same as: tokens watch
agent-watch --project-dir ~/.claude/projects/<project-folder>
agent-watch --stalled-after 300 --since-hours 8
tokens agents                                # one-shot text snapshot, no full-screen UI
tokens agents --all --json                   # include finished agents, machine-readable
```

**Which project?** The Claude Code project of the folder you start it in (parent folders count). If none matches,
the project you used most recently. The bottom line names the watched project.

### Layout

- **MAIN** (top): model, effort, run time, tokens, cost, the **context gauge**, peak context, today's output share,
  and the last thing the assistant said (secrets and e-mail addresses masked).
  One row per open session: a session you replaced with `/clear` is hidden (its tokens still count in the totals).
- **Agent panes** (grid): titled with the agent's task; a spinner in the border moves only while it is working.
- **Bottom line:** agents (live / stalled / done / over budget), total tokens and cost, **burn rate**, project, filter.

### Status word

| Word | Colour | Meaning |
|---|---|---|
| `● WORKING` | green | wrote to its transcript under 60 s ago (shows the running tool) |
| `◐ WAITING` | amber | quiet between 60 s and `--stalled-after` (default 180 s) |
| `⚠ STALLED` | red | quiet longer than that and not finished — often a permission prompt or a long command |
| `✓ FINISHED` | grey | done, with how we know: `meta finished`, `ended turn`, `dismissed`, or `done? (quiet >60 min)` |

The run-time colour (green < 5 min, amber 5–10, red > 10) is only duration, not health.

### Keys

| Key | Action |
|---|---|
| ↑ ↓ ← → / Tab | select an agent |
| Enter / `t` | detail + troubleshooting screen |
| `a` | analytics screen (`t` / `m` sort by tokens / time, Esc back) |
| `f` | live only ↔ all agents |
| `d` | mark done / undo (for a finished agent still shown as stalled; saved locally, transcripts untouched) |
| `r` / `s` | restart / stop **request** (needs the hooks; nothing is killed by the monitor) |
| `x` | run the agent's brief separately in a new terminal (never adds `--dangerously-skip-permissions`) |
| `?` | help |
| `q` | quit |

### Stop / restart requests

`r` or `s` writes a small JSON request into the request folder (default `~/.local/share/token-ledger/agent-requests/`).
With your **next message** in that chat, the `hook-prompt` hook hands it to the model as context ("stop agent X with
TaskStop" / "restart it with the same brief") and marks it delivered, so it is sent once. If the hooks are not
installed, the monitor says so and writes nothing.

---

## The ledger: reports and dashboard

| Command | What it does |
|---|---|
| `tokens ingest [--quiet] [--force] [--reclassify]` | import new log lines (every report does this first) |
| `tokens today` / `week` / `month` / `year` | totals by token kind, model, task kind, feature, account; change vs previous period |
| `tokens features [--since YYYY-MM-DD]` | each feature with its task kinds, models, tokens and cost |
| `tokens feature <text>` | one feature in detail: sessions, agent runs, costliest calls |
| `tokens daily [--days N]` | per-day table with the model split |
| `tokens tag <feature> [kind] [--session ID]` · `tokens tag --clear` · `tokens tag` | label upcoming work |
| `tokens html [--no-open]` | write (and open) the dashboard |
| `tokens check` | self-checks |
| `tokens export [--since D] > f.jsonl` · `tokens import f.jsonl` | merge several machines |
| `tokens agents [--all] [--json]` | live snapshot without the UI |
| `tokens watch` | the live monitor (= `agent-watch`) |

Report filters: `--project P`, `--all`, `--account A`, `--model M`, `--machine M`, `--source S`, `--config-dir D`.
Reports show the **active project** (most recently used) unless you pass `--all` or `--project`.
`--data-dir DIR` on any command uses another data folder.

**Reading the numbers:** *cache read* is context re-used on every reply — usually 95%+ of all tokens but cheap per
token; *output* is few tokens but the most expensive. Compare **est. cost**, not only tokens. A step-by-step guide for
non-technical readers is in [docs/USAGE-GUIDE.md](docs/USAGE-GUIDE.md).

---

## Configuration

`~/.config/token-ledger/config.toml` (or `config.json` on Python < 3.11). Every line is optional.

```toml
data_dir = "~/.local/share/token-ledger"   # tokens.sqlite, tags.jsonl, tokens.log, dashboard.html
machine = "my-laptop"                       # default: host name
sources = ["claude-code"]
extra_claude_dirs = []                      # ~/.claude and ~/.claude-* are found automatically
price_file = ""                             # your own prices.json
timezone = "Asia/Kolkata"                   # calendar days; default: this computer's time zone

[watch]
main_save_tokens = 120000                   # MAIN gauge amber: time to checkpoint
main_compact_tokens = 160000                # MAIN gauge red: compact now
burn_window_minutes = 10
alerts = true
plans_dir = "~/proj/wiki/plans"             # plan numbers in agent tasks become titles; default <project>/wiki/plans
workspace = "~/proj"                        # folder `x` starts claude in; default: the project's own folder
request_dir = "~/.local/share/token-ledger/agent-requests"

[watch.agent_budget_tokens]                 # peak context per agent run, by model family
opus = 80000
sonnet = 60000
haiku = 30000
default = 60000
```

Environment variables: `TOKEN_LEDGER_CONFIG_DIR`, `TOKEN_LEDGER_DATA_DIR`, `TOKENS_WATCH_PLANS_DIR`,
`CLAUDE_CONFIG_DIR`.

---

## Project rules: `.token-ledger.toml`

Put this file in a project's root to say how its work maps to features and kinds; it is found by walking up from the
folder each session ran in. Without it: feature = session name, else git branch, else `<project> (untagged)`; kinds
come from agent types, skills and tools. Paths use `/` on every platform.

```toml
[[feature]]                                   # wiki/plans/005-agent-setup.md  ->  feature "005 agent setup"
path_regex = "wiki/(plans|tests)/(\\d{3})-([a-z0-9-]+?)(-tests)?\\.md"
label = "{2} {3}"
dashes_to_spaces = true

[branches]                                    # branch names on "Branch" lines in plans map to the same feature
plans_glob = "wiki/plans/*.md"
branch_line_regex = "[Bb]ranch"

[[kind]]
path_prefix = "site/"
kind = "Implementation"
[[kind]]
path_prefix = "wiki/plans/"
kind = "Planning"

[defaults]
unassigned_label = "Unassigned"
```

After changing rules or tags: `tokens ingest --reclassify`.

---

## Several accounts and machines

- **Accounts:** Claude Code logs carry no account. The hooks leave a small mark (account name before the `@`, never
  the full address) when a session starts or the signed-in account changes; calls are matched to the marks. Older
  calls show as `unknown (before tracking)`. Every `~/.claude*` config folder is read, labelled by its name.
- **Machines:** on each machine `tokens export > laptop.jsonl`; on the main one `tokens import laptop.jsonl`. Rows are
  de-duplicated on source + machine + config dir + request id, so importing twice is safe.

---

## How it works

```
~/.claude/projects/<project>/<session>.jsonl              main chat transcript
~/.claude/projects/<project>/<session>/subagents/agent-*.jsonl   sub-agent transcripts (+ .meta.json)
          │
          ├── watch/reader.py   incremental tail reader (byte offsets) → live state every second
          │      └── watch/app.py   Textual UI · watch/actions.py   status, gauges, requests, launch
          │
          └── sources/claude_code.py → classify.py (feature + kind rules) → store.py (SQLite)
                 └── report.py (terminal) · html.py (dashboard)
```

```
tokens / agent-watch      entry points          install.py   cross-platform installer (install.sh wraps it)
token_ledger/             cli, config, compat, store, classify, ingest, report, html, prices.json, sources/, watch/
docs/USAGE-GUIDE.md       plain-English guide   tests/       unittest suite
```

Adding another tool (for example Codex CLI) means writing one source adapter; see
[token_ledger/sources/README.md](token_ledger/sources/README.md).

---

## Privacy

- Stored: numbers, times, model names and derived labels (feature, kind, project, account name before `@`,
  session title). Never message text, never full e-mail addresses.
- The monitor shows a masked one-line summary of the latest text and tool call: `KEY=…`, `TOKEN=…`, e-mail
  addresses and long mixed letter/digit strings are replaced by `***`.
- Nothing leaves your machine.

---

## Development

```bash
python install.py                                     # creates the venv
.venv-win/Scripts/python -m unittest discover -s tests -t .     # Windows
.venv-watch/bin/python -m unittest discover -s tests -t .       # Linux/macOS
```

The suite (82 tests) uses temporary folders and synthetic transcripts only. `tests/test_stall_watch.py` covers an
optional companion hook kept outside this repo; it is skipped unless `AGENT_STALL_WATCH` points to that script.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `tokens: command not found` | Add `~/.local/bin` (Windows: `%USERPROFILE%\.local\bin`) to `PATH`, or run `python <repo>/tokens`. |
| Reports are empty | `tokens ingest` and read its line; `0 sessions read` means no logs in `~/.claude/projects/` (or set `extra_claude_dirs`). |
| Monitor shows the wrong project | Start it from the project folder, or pass `--project-dir`. |
| `Textual is missing` | Run `python install.py` once. |
| Time zone warning in reports | `pip install tzdata` into the venv (the installer does this on Windows) or set `timezone` in config. |
| `r` / `s` say the hook is missing | `python install.py --apply-hooks`, then open a new Claude Code session. |
| Numbers look wrong after a Claude Code update | Check `unparsable lines` in `~/.local/share/token-ledger/tokens.log`; the log format may have changed. |
| Unsure the numbers are right | `tokens check`. |

---

## Uninstall

Nothing here runs by itself.

- **Hooks:** remove the `hook-session-start` and `hook-prompt` entries from each `~/.claude*/settings.json`
  (backups sit next to it as `settings.json.bak-agent-monitor-<time>`).
- **Commands:** delete `tokens` and `agent-watch` (`.cmd` on Windows) from `~/.local/bin`.
- **Data and config:** `~/.local/share/token-ledger/` and `~/.config/token-ledger/` hold everything it stored.
- **Tool:** delete the repository folder.

---

## Author

**Jill Bhatt** — [github.com/jillBhatt26](https://github.com/jillBhatt26)

Built while running Claude Code agent fleets daily, to see what every agent is doing and what it costs, without
sending a byte of it anywhere.

## License

[MIT](LICENSE) © 2026 Jill Bhatt
