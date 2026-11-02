# Agent Monitor usage guide (`tokens` / `agent-watch`)

For people who are not data people. Short steps. Copy the commands exactly. After each step you will see what
"good" looks like.

---

## 1. What it does, and what it does not

**It does**
- Reads the usage logs Claude Code already keeps on your computer.
- Counts tokens (the units AI models are billed in) and sorts them by **feature**, **kind of work**, **model**,
  **account**, and by **day, week, month, year**.
- Shows the result in the terminal and as a web page on your own computer.

**It does not**
- Read or keep your messages. Only numbers and labels are stored.
- Send anything anywhere. It works fully offline.
- Show your real bill. "Est. cost" is *list price x tokens*. Your plan or the Anthropic console is the truth for money.
- Cover tools without an adapter. Today that is **Claude Code only**. Chats on claude.ai or the desktop app are not in these logs.

---

## 2. Install on this machine

You need Python 3.9 or newer (`python --version` on Windows, `python3 --version` on Linux/macOS).

1. Go to the tool folder and install:
   ```bash
   cd agent-monitor
   python install.py          # Windows (PowerShell, cmd or Git Bash)
   ./install.sh               # Linux / macOS (same thing)
   ```
   You should see `wrote ...tokens.cmd` (Windows) or `linked .../tokens` (Linux/macOS), `created ... config.toml`,
   a block of JSON (the hooks, not yet applied) and `self-check passed`.
2. (Recommended) Switch on the hooks. They keep your history fresh, note which account is signed in, and deliver the
   monitor's stop/restart requests:
   ```bash
   python install.py --apply-hooks
   ```
   You should see `hooks added to ~/.claude/settings.json (backup: ...)`. Run it twice and the second time says
   `hooks already present`. Open a new Claude Code session for the hooks to start working.
3. Import your logs for the first time:
   ```bash
   tokens ingest
   ```
   You should see `ingest: 34543 new calls (...)` with your own numbers. The first run takes several seconds.
4. Check that everything adds up:
   ```bash
   tokens check
   ```
   You should see `OK` three times, `Re-ingest ... added 0 rows OK`, and `ALL CHECKS PASSED`.

If `tokens: command not found`, add `~/.local/bin` (Windows: `%USERPROFILE%\.local\bin`) to your PATH, or run
`python <tool folder>/tokens` instead.

---

## 3. Set up another machine

1. Clone or copy the repository to the new machine (for example to `~/agent-monitor`).
2. `python install.py` there (and `python install.py --apply-hooks` if Claude Code is used there).
3. Give the machine a name, so merged reports can tell machines apart. Open `~/.config/token-ledger/config.toml` and
   remove the `#` in front of the machine line, for example:
   ```toml
   machine = "office-laptop"
   ```
4. Run `tokens ingest`, then `tokens today`. You should see that machine's own numbers.

---

## 4. Several machines together

Each machine keeps its own database. To see everything in one place, move the numbers (not the logs).

1. On machine B, export:
   ```bash
   tokens export > office-laptop.jsonl
   ```
   Only new days: `tokens export --since 2026-10-01 > office-laptop.jsonl`.
2. Copy the file to machine A by any shared folder, USB stick or cloud drive. It holds numbers and labels only.
3. On machine A, import:
   ```bash
   tokens import office-laptop.jsonl
   ```
   You should see `imported N new calls (rows already present were skipped)`.
4. Look per machine: `tokens week --all --machine office-laptop`. Without `--machine` you see all machines together.

**No double counting:** every row is identified by tool + machine + config folder + request id. Importing the same file
twice says `imported 0 new calls`.

**Idea for a schedule:** put step 1 in a daily cron job on machine B, writing into a synced folder, and step 3 in a
daily cron job on machine A. Both commands are safe to repeat.

---

## 5. Several Claude accounts

The logs do not say which account made a call. The tool works it out with the two hooks:
`hook-session-start` and `hook-prompt` write a small mark ("session X is using account Y now") each time a session
starts or the signed-in account changes. A call belongs to the latest mark at or before it.

**One config folder, switching with `/login`** (the usual way)
- Tracked from the day you switched the hooks on.
- Older calls show as `unknown (before tracking)`. That is expected, not an error.
- Accounts are shown by the part before the `@` only.
- See them: `tokens week --all` has a "By account" table. Filter: `tokens week --all --account yourname`.

**Separate config folders** (for example `alias claude-alt='CLAUDE_CONFIG_DIR=~/.claude-alt claude'`)
- Every folder named `~/.claude*` that has a `projects/` folder is read automatically.
- The folder name is kept as "config dir": `tokens week --all --config-dir claude-alt`.
- A folder in another place: list it in `config.toml`, `extra_claude_dirs = ["~/work/.claude-client"]`.
- The hooks must be in that folder's `settings.json` too (`./install.sh --apply-hooks` covers every `~/.claude*`).

---

## 6. Other projects

It works with no setup. Every project folder Claude Code has used is included.

Without rules, a call's **feature** is: the session name (set with `/rename`), else the git branch, else
`<project> (untagged)`. The **kind** comes from agents, skills and tools used.

Optional: add a `.token-ledger.toml` file in the project's root folder. It is found by walking up from where the
session ran. After changing it, re-sort the stored calls with `tokens ingest --reclassify`.

```toml
# Which file you were working on decides the feature.
# This one turns "docs/specs/012-login-flow.md" into the feature "012 login flow".
[[feature]]
path_regex = "docs/specs/(\\d{3})-([a-z0-9-]+?)\\.md"   # "{1}" is the first (...) group, "{2}" the second
label = "{1} {2}"
dashes_to_spaces = true

# Branch names found on lines containing "Branch" in these files map to the same features.
[branches]
plans_glob = "docs/specs/*.md"
branch_line_regex = "[Bb]ranch"

# What an edit means by folder (relative to this file). First match wins.
[[kind]]
path_prefix = "src/"
kind = "Implementation"
[[kind]]
path_prefix = "docs/"
kind = "Docs & wiki"

[defaults]
unassigned_label = "Unassigned"     # instead of "<project> (untagged)"
```

A typical setup for a workspace that keeps the app in `site/` and its notes in `wiki/`: plan files
`wiki/plans/NNN-topic.md` become features, edits under `site/` are Implementation, under `wiki/plans/` Planning, under
`wiki/tests/` Testing. Paths in rules always use `/`, also on Windows.

---

## 7. Other LLM tools

Each tool writes its logs in its own format, so each needs a small **adapter**: a Python file that reads that tool's
logs and hands over plain numbers (time, model, input, output, cache tokens) plus small labels.

- Exists today: **Claude Code** (`token_ledger/sources/claude_code.py`).
- To add one (for example Codex CLI), follow `token_ledger/sources/README.md`. First look at the real log files; do not
  guess their format. When it works, list it in `sources = [...]` in `config.toml`.

---

## 8. Daily use: analysing your usage

By default every report shows the **active project** (the one you used most recently). Add `--all` for every project,
or `--project NAME` for one.

| You want | Command |
|---|---|
| Today | `tokens today` |
| This week (Monday to Sunday) | `tokens week` |
| This month | `tokens month` |
| This year | `tokens year` |
| Every feature with its kinds of work | `tokens features --since 2026-10-01` |
| One feature in detail | `tokens feature 026` |
| Day by day, last 14 days | `tokens daily --days 14` |
| Only one model | `tokens week --model opus` |

Each period report shows: tokens by kind, then tables by model, task kind, feature and account, then a comparison with
the previous period, then the **GRAND TOTAL** line.

**Tags: file new work under the right feature.** Say you start work on feature 026:

1. Tell the tool: `tokens tag "026 meeting report" Implementation`
   You should see `Tagged: 026 meeting report / Implementation`.
2. Do the work in Claude Code. The new calls are filed under that feature and kind.
3. Look: `tokens feature 026`. You should see tables by task kind and model, the sessions, agent runs, and the ten
   costliest calls.
4. Done with it: `tokens tag --clear`. `tokens tag` alone shows the current tag.

The kind is optional (`tokens tag "026 meeting report"`). A tag applies to every session unless you add
`--session SESSION_ID`. It affects calls made after you set it.

**The dashboard**
```bash
tokens html
```
It writes `~/.local/share/token-ledger/dashboard.html` and opens it in your browser. Use `--no-open` to only write it.
Top to bottom:
- **Title and filters:** the active project in big letters; pick another project or account; quick date buttons
  (Today, 7 days, 30 days, This month, This year, All).
- **Four tiles:** today, this week, this month, this year. Each shows tokens, est. cost and the change against the
  previous period. Orange means more than before, green means less.
- **Over time chart:** one bar per day, week or month. Switch the colours between **model** and **task kind**, and the
  height between **est. cost** and **tokens**. Hover a bar for the numbers.
- **Features table:** click a feature to open its kinds of work (planning, testing, ...) with model, token kinds and
  cost. Each feature has a subtotal. The last row is the GRAND TOTAL.
- **Models, task kinds, accounts:** the same numbers cut three other ways, with each one's share.
- **50 costliest calls:** the single most expensive moments.

---

## 8b. Live agent monitor

```bash
agent-watch              # or: tokens watch
tokens agents            # one-shot text snapshot (no full-screen UI); add --json for scripts, --all for finished agents
```

It watches the Claude Code project of the folder you start it in (parent folders count), else the project you used
most recently. `--project-dir` picks one explicitly.

What you see:
- **MAIN pane:** model, effort, run time, tokens, est. cost, and the **context gauge**: green below 120k, amber
  "checkpoint" from 120k, red "COMPACT NOW" from 160k (both in `config.toml` under `[watch]`). Every call re-sends the
  whole context, so a big context makes every reply expensive.
- **Agent panes:** task, model, status word (WORKING / WAITING / STALLED / FINISHED), tools used, tokens, cost, and
  **peak context against the budget for its model** (Opus 80k, Sonnet 60k, Haiku 30k by default). `OVER BUDGET` means
  the agent read far more than its job needed: give it exact files and lines, or split the brief.
- **Bottom line:** agent counts (and how many are over budget), totals, and the **burn rate**: tokens processed per
  minute over the last 10 minutes, with an hourly cost estimate.
- **Alerts:** a bell and a toast, once each, when an agent stalls, goes over budget, or the main context reaches the
  compact line. Switch off with `alerts = false`.

Keys: arrows/Tab select · Enter/`t` details and troubleshooting · `a` analytics · `f` live/all · `d` mark done/undo ·
`r` restart request · `s` stop request · `x` run the agent's brief separately in a new terminal · `?` help · `q` quit.

**Stop/restart requests** are files the monitor writes; the `hook-prompt` hook hands them to that chat with your next
message, and the model stops or restarts the agent. Nothing is killed from the monitor itself. Without the hooks the
monitor tells you so and does nothing.

---

## 9. Reading the numbers

**Four kinds of tokens**
1. **Fresh input:** new text sent to the model.
2. **Cache write:** context the model stores so it need not be sent again.
3. **Cache read:** stored context used again. Every reply re-uses the whole conversation, so this number is
   **huge** (often 95% of all tokens) but each token costs only a tenth or less of a fresh one.
4. **Output:** what the model wrote, including its thinking. Few tokens, but the most expensive per token.

**Total** is the four added up. Because cache read dominates, compare **est. cost**, not only tokens.

**Est. cost** = list price x tokens, from `token_ledger/prices.json` (source URL and date inside). It is an estimate,
not your bill. Models without a price show as unpriced in `tokens check`.

**Strange-looking labels**
- **Unassigned** (or `<project> (untagged)`): no plan file, branch, session name or tag could place the call.
  A tag fixes this for new work.
- **Conversation:** the call only talked (answering, explaining, deciding). Often the biggest kind, because long chats
  re-read a lot of context.
- **unknown (before tracking):** the call is older than the account hooks, so the account is not known.

---

## 10. Saving tokens with what you see

- **Watch Conversation and Cache read.** Long sessions re-read their whole history on every reply. One task per
  session, then `/clear`, keeps this small.
- **Check the model per kind** in `tokens features`. Testing, git and research on a cheaper model (for example
  `test-runner` and `git-scout` agents on Haiku) should show up there as cheap rows; planning and judging are the places
  for the strongest model.
- **Compare periods** (`tokens week`, "Compared with the previous period") after changing routing, to see whether it
  worked.
- **Look at the 10 costliest calls** (`tokens feature <text>`): a few giant contexts usually explain most of a bill.

---

## 11. Troubleshooting

| Problem | What to do |
|---|---|
| Reports are empty | `tokens ingest` and read its line. `0 sessions read` means no logs were found: check that `~/.claude/projects/` exists, or set `extra_claude_dirs` in `config.toml`. |
| "Data range" looks too short | Claude Code deletes old logs. Only what was imported earlier is kept, so import regularly (the hooks do it). |
| Hooks do not seem to run | Open `~/.claude/settings.json` and look for `hook-session-start` and `hook-prompt`. Start a new Claude Code session, then `tokens today --all` should show your account under "By account". Hook errors go to `~/.local/share/token-ledger/tokens.log`. |
| Numbers look wrong after a Claude Code update | Read `~/.local/share/token-ledger/tokens.log`. The ingest line reports `unparsable lines`; a number above 0 means the log format changed and the adapter needs a fix. |
| You changed `.token-ledger.toml` or tags | `tokens ingest --reclassify` re-sorts stored calls. |
| You changed the timezone | Days are re-bucketed on the next run automatically. |
| You want a fresh start without losing the old data | Use a new data folder: `tokens --data-dir ~/token-ledger-new ingest`. The old folder stays untouched, and you can compare both. To make it permanent, change `data_dir` in `config.toml`. |
| Unsure if numbers are right | `tokens check`. |

---

## 12. Uninstall or switch off (read; nothing here is run for you)

- **Stop the hooks:** open each `~/.claude*/settings.json` and remove the two entries whose command ends with
  `hook-session-start` and `hook-prompt` (the installer made a backup next to the file, named
  `settings.json.bak-agent-monitor-<time>`). The history stays.
- **Remove the commands:** delete `~/.local/bin/tokens` and `~/.local/bin/agent-watch` (Windows: the two `.cmd`
  files in `%USERPROFILE%\.local\bin`).
- **Remove the tool:** delete the repository folder.
- **Remove the data:** the folder `~/.local/share/token-ledger/` and the config `~/.config/token-ledger/` hold everything
  it ever stored. Keep them if you might come back.
