# buildkit spec

Status: draft 4, Oct 27 2026. Steps 1 to 4 built (measurement, kit, agents and skills, hooks). Pilot next.
- Draft 1 review and verdicts: [docs/2026-10-27 spec review.md](docs/2026-10-27%20spec%20review.md)
- Worker cost measured (step 1): [docs/2026-10-27 worker cost.md](docs/2026-10-27%20worker%20cost.md)
- kit core (step 2): [docs/2026-10-27 kit core results.md](docs/2026-10-27%20kit%20core%20results.md)
- Agents, skills and hooks (steps 3 and 4), with the evidence behind draft 4: [docs/2026-10-27 plugin results.md](docs/2026-10-27%20plugin%20results.md)

## What it is

Mark's own software team. He says what he wants. A planner breaks big ideas into pieces. Builders each work on their own branch in their own worktree. A reviewer checks risky work. Mark decides the push. When a piece ships or is dropped, its worktree, branch and any chat are cleaned up. Ideas and features are dumped in and routed to a coder, a UI designer and reviewers, with usage and context kept low.

Works for any project. First pilot: a Python game repo with 480 tests.

Why: an earlier multi-chat build process ran 43 chats in about 23 hours, 10 of them managers, used 49% of a week's usage in 1.8 days, and every rule that broke was prose nothing checked.

## The flow

```
Mark: /idea <text>
  -> desk sizes it          quick, small or big (kit new)
  -> planner                big ideas only: splits into child items with done-when
  -> wait or go             big or risky item with an open question waits for Mark
  -> builder on a branch    coder, coder-big or ui-designer, own worktree
  -> test gate              hook runs tests, writes Proof, 2 retries max
  -> reviewer               only if risky or the diff is over 150 lines
  -> ready                  /queue shows it to Mark
  -> Mark                   ship (merge, test, push, clean up)
                            change <note> (back to the builder)
                            drop (delete branch and worktree, clean up)
```

Every step writes to the item's file through `kit`. Nothing important lives only in a chat.

## What step 1 measured

Same read-only task in poker-game, Sonnet, 15 runs. Full table in the worker cost doc.

| Helper | Start cost | Whole run, processed | Found the right answer |
|---|---|---|---|
| stripped (`omitClaudeMd` plus an explicit `tools:` list) | about 5.4k | 42k to 78k | 2 of 4 |
| default subagent | about 46.7k | 149k to 341k | 5 of 5 |
| new chat (baseline from an earlier process) | about 92k | | |

What it means:
- **Stripped helpers are the design.** They start at about 6% of a new chat. The explicit `tools:` list removes about 38k (built-in and MCP tool definitions). `omitClaudeMd` removes 0.4k to 6k. Every agent file needs both.
- **The brief must carry the project facts.** The 2 failed stripped runs guessed a JavaScript repo and quit, because they had no `CLAUDE.md`. So `kit brief` builds every prompt with the language, test command and key paths from `build/config.yml`. Step 3 made this literal: the launch hook writes the brief into the prompt itself. In all 5 smoke runs the coder used the test command from the brief and none guessed the stack. Whether it fixes the misses on a real repo is still the pilot's question.
- **Separate chats are not needed for cost.** Big items run as helpers too. A separate chat is only for an item Mark wants to watch or steer himself.
- **Keep tool calls few.** Most of a run's tokens are cache re-reads, which grow with every call. Narrow briefs, exact file paths.

## Item states

`new` -> `planning` (big only) -> `waiting` (open question, big or risky only) -> `building` -> `failed` or `review` -> `ready` -> `shipped` or `dropped`.
- `change <note>` sends a `ready` item back to `building` with the note in its brief.
- A planned parent item has no branch of its own. It is `ready` when all its children are `ready`, and shipping it ships them in order.
- Only `kit status` changes a state. A hook blocks any helper edit to `build/`.

## Parts

### 1. Inbox
- `build/inbox.md`. One idea per line, any wording.
- Lines come in only through `/idea` or Mark's own phone capture. Never web clippings, forwarded mail or other outside text, because inbox text becomes instructions for agents with Bash.

### 2. Desk routing
- The desk chat reads new inbox lines and runs `kit new` for each: title, size, risk tags, the inbox line verbatim.
- **Quick** (one file, under about 5 tool calls): the desk does it, still through an item.
- **Small**: straight to a builder.
- **Big** (more than one part, more than one role, or more than one area of the code): to the planner first.
- Unclear idea: one question in `build/decisions.md` with a default. Quick and small items go ahead on the default. Big or risky items wait.

### 3. Agents (plugin agents)

Every agent file has `omitClaudeMd: true` and an explicit `tools:` list with no MCP tools and no Skill tool. Every prompt comes from `kit brief <id>`: the desk launches a helper with the prompt `# Item <id>` and the launch hook replaces it with the brief (the planner also gets the `kit map` list). The desk never writes a helper's prompt. In hook input and in `subagent_type`, plugin agents are named `buildkit:<agent>`.

No agent sets `isolation: worktree`. `kit status <id> building` makes the branch `bk/<id>` and the worktree `.claude/worktrees/bk-<id>`, and the brief names that path (BT-8). Each project's `build/` stays out of git through `.git/info/exclude` (BT-7).

| Agent | For | Model | Tools | Worktree |
|---|---|---|---|---|
| planner | big ideas: split into child items | Sonnet | Read, Grep, Glob | no |
| coder | small code items | Sonnet | Read, Grep, Glob, Edit, Write, Bash | yes |
| coder-big | big code items | Opus | same as coder | yes |
| ui-designer | pages, components, CSS. Design rules come in the brief (`design_rules` in config), not a preloaded skill | Sonnet | same as coder. The screenshot is a headless-browser command run through Bash, saved to `build/shots/<id>.png` (no MCP tool) | yes |
| reviewer | security and performance pass | Sonnet | Read, Grep, Glob | no |
| reviewer-risky | items tagged auth, money, send, delete, public, secrets | Opus | same as reviewer | no |

**Planner.**
- Gets the idea, `build/config.yml` and a file list from `kit map`. It reads only what it needs.
- Returns at most 8 child items, one line each in a fixed format: title, role, size, risk, files, done-when. Ordered by dependency.
- The desk runs `kit plan <parent> < reply`, which writes the children and links them to the parent.
- Mark sees the plan in `/queue` before any builder starts, when the parent is big or risky. He answers "go", edits a line, or drops a child.

Builders and reviewers return at most 10 lines. Details go into the item through `kit note <id> <text>` (builders run it, the brief gives the exact command) or, for the read-only agents, through the desk. The reviewer gets the diff in its prompt from `kit diff <id>`, so it needs no Bash.

### 4. Hooks (plugin `hooks/hooks.json`)

Plugin agents ignore hooks in their own frontmatter, so all hooks live here. Hooks fire for every subagent in every project (a built-in `general-purpose` agent triggers them too, measured), so each one exits silently unless the project has `build/config.yml` and `agent_type` (or `subagent_type` on the Agent tool) starts with `buildkit:`. A crash in a PreToolUse guard that has decided it applies becomes a deny. Hooks run in the project root, never in the worktree.

| Hook | Matcher | Does |
|---|---|---|
| launch gate | PreToolUse on `Agent` | Reads the item id from the prompt (`# Item <id>`), runs `kit can-start <id> <agent>`, and when it allows, replaces the prompt with `kit brief <id>`. Also denies a `model` override, an `isolation` setting, an unknown agent and a planner when `kit map` fails. Denies the launch if: the item is not in a startable state; the agent does not match the item's size and risk; 2 big items are already building; the item's attempt budget is spent; `kit brief` cannot build a full brief (config lacks language, test command or key paths, or the item has no done-when or file list); or Mark owes more than 5 and this is a big item (it lists his quick ones first) |
| path guard | PreToolUse on Edit, Write, MultiEdit, NotebookEdit | For buildkit agents. Builders may edit only inside their own item's worktree (`.claude/worktrees/bk-<id>`, item taken from the helper's transcript). Denies `build/`, `.git`, config `protected` entries and the item's `no_touch`, ignoring case, checking both the typed and the resolved path. Planners and reviewers may not edit at all. Bash is not covered (see Flaws) |
| test gate | SubagentStop, coder, coder-big, ui-designer | Runs `kit proof <id>` (the project's test command in the helper's worktree). kit writes Proof: exit code, head, log path, time. UI items also need `build/shots/<id>.png` newer than the last edit. Exit 2 blocks with the test tail as the reason, at most 2 times per item. kit counts the attempts in the item, so the hook ignores `stop_hook_active`. Third fail: `kit status <id> failed`, goes to Mark. If the gate cannot run, the helper may stop and the item gets a work-log note; with no passing proof the item cannot reach `ready` |
| cost note | SubagentStop, all buildkit agents | Runs `kit cost <id> --transcript <helper transcript>`: whole-run tokens, start cost, tool count, model, start and end time. Not PostToolUse: the Agent tool returns at launch for a background helper, before the run exists. Waits until the helper's final message is in the transcript, because the file is still being written when the hook starts |

### 5. Cleanup

buildkit only touches what it made. Every worktree it creates sits under `.claude/worktrees/` on a branch named `bk/<item id>`, and the item records both. Anything else (Mark's own worktrees, other branches) is never touched.

| Event | What `kit` does |
|---|---|
| ship | Merge `bk/<id>` into the main branch. Run tests on the result. If they pass: push, delete the worktree and the branch (local and remote), mark `shipped` with the commit hash. If they fail: stop, item back to `failed`, nothing pushed |
| drop | Delete the worktree and branch, mark `dropped`. The inbox line stays linked |
| either | If the item ran in a separate chat, its session id is in the item. `kit` prints it and the desk archives that chat |
| `kit sweep` | Lists `bk/` worktrees and branches with no open item. Deletes them only after Mark says yes |

Helpers themselves need no cleanup. A subagent ends when its job ends.

### 6. Push

Mark's "ship" is the push approval. Nothing reaches the remote before it. `config.yml` sets `ship: push` (merge to the main branch and push, the default for solo repos) or `ship: pr` (open a pull request instead, for repos where he wants GitHub review).

### 7. kit (one Python script, stdlib only)

`new`, `plan`, `brief`, `map`, `status`, `can-start`, `diff`, `proof`, `cost`, `board`, `queue`, `ship`, `drop`, `sweep`, `ledger add`, `ledger answer`, `note` (a helper's detail line in the work log, never a state change), `inbox` (appends one idea line).
- Ids come from `kit`, so two chats never take the same one.
- `kit board` rebuilds `build/board.md` from the items, one line each.
- `kit brief <id>`: the full prompt for a helper. Project facts from config, the item's done-when and files, and Mark's change notes.
  A brief always has: language, test command, key paths and protected paths from `build/config.yml`; the done-when; the files to read and the files not to touch; Mark's change notes. A helper never gets a bare idea. If a part is missing, `kit brief` fails and the launch gate denies the launch. Why: step 1, 2 of 4 stripped runs with a thin prompt guessed a JavaScript repo and quit ([worker cost](docs/2026-10-27%20worker%20cost.md)).
- `kit queue`: Mark's review list. One line per `ready` or `failed` item (what changed, proof, risk, cost), each plan waiting for his go, and each open question with its default.

### 8. The desk (where Mark talks)
- One chat per project, on Sonnet. It sizes ideas, launches the planner and helpers, reads `kit` output and talks to Mark.
- On start it reads only `build/board.md` and `kit queue`. A new desk chat is caught up after those two reads.
- Slash commands (plugin skills, typed with the plugin name: `/buildkit:idea`, `/buildkit:run <id>`, `/buildkit:queue`, `/buildkit:ship <id>`, `/buildkit:drop <id>`, `/buildkit:change <id> <note>`, `/buildkit:sweep`). Every skill is `disable-model-invocation`, so only Mark triggers ship, drop and sweep. `/run` moves the item to `building` through kit, which makes the worktree, then launches the helper.
- Helpers launch in the background by default in this Claude Code version (2.1.310). The desk waits for the completion notice.
- No push notifications in v1. Mark checks `/queue` from any device.

## Files in each project

```
build/
  inbox.md        raw ideas
  items/          one file per item (templates/item.md)
  board.md        generated by kit, never hand-edited
  decisions.md    questions with defaults, answers verbatim, ids from kit
  config.yml      language, test command, key paths, protected paths, design rules, limits, ship mode
```

`config.yml` limits, with defaults: `big_running: 2`, `mark_owes: 5`, `attempts: 2`, `review_lines: 150`, `plan_max_items: 8`.

## Repo layout

```
.claude-plugin/plugin.json and marketplace.json
agents/       planner, coder, coder-big, ui-designer, reviewer, reviewer-risky
skills/       idea, run, queue, ship, drop, change, sweep
hooks/        hooks.json
kit/          kit.py and tests
templates/    item.md, config.yml
docs/         findings, reviews, measurements
```

Installed from the private GitHub repo as a marketplace, using the existing `gh` login.

## Usage targets

- Helper start cost under 15k with Edit, Write and Bash included. Measured: coder 8.4k (5.4k read-only).
- One desk chat per project per day. Desk context under 25% at end of day.
- At most one Opus run per item (coder-big or reviewer-risky).
- Every item records its cost. `kit cost --week` checks these targets.

## Order of work

1. ~~Measure worker cost.~~ Done: go on stripped helpers.
2. ~~kit core~~ Done, merged.
3. ~~Agents and skills~~ Done on branch `plugin-agents-hooks`. Coder start cost with Bash, Edit and Write: 8.4k.
4. ~~Hooks~~ Done on the same branch.
5. **Pilot on poker-game**: 5 real items end to end, one of them big (through the planner), one dropped. Record cost and whether `kit brief` fixes the stripped-helper misses.
6. A second project, once its code is in git (BT-4).
7. Later: push notifications, performance scripts, a second reviewer opinion from ChatGPT.

## Open

Settled in step 4, with evidence in the plugin results doc:
- `stop_hook_active` is false on a helper's first stop and true on a stop that follows a block. kit's own attempt counter ends the loop, so the gate does not depend on it.
- SubagentStop runs in the project root (the parent's folder), not the worktree. The hook gets the worktree from the item (kit knows it).
- Hooks tell a buildkit helper by `agent_type` starting with `buildkit:`.
- A plugin agent honors `omitClaudeMd` and `tools:` when installed from a marketplace (local folder and GitHub), not only with `--plugin-dir`: start cost 8.4k in all three.

Still open:
- **BT-1** (this chat's role) and **BT-4** (second project's code into git).
- Bash is not guarded. A helper can still write `build/` or a protected file with a shell command. See Flaws.

## Flaws

Found and fixed:
- Draft 2 had no planner, so big ideas went straight to a builder or loaded the desk. Added, for big items only, with Mark's go before building.
- Draft 2 cleaned up only on drop. Cleanup now runs on ship and drop, and only on what buildkit made (`bk/` branches), per Mark's rule never to assume a worktree is abandoned.
- Draft 2 kept "big items run headless" as a cost fallback. Step 1 showed it is not needed. Removed.
- Ship did not push. Now ship is the push approval, with a PR option.

Remaining:
- The brief-fixes-the-misses claim is not measured. The pilot tests it.
- A helper has Bash, and no hook inspects Bash commands. The path guard stops Edit and Write only. The coder's brief and the item state still hold (kit refuses a proof from a dirty or moved branch), but a shell redirect into `build/` is not stopped. Add a Bash guard or take Bash from builders that do not need it.
- The desk itself is not a subagent, so helper hooks do not cover it. It could edit code directly outside an item. A desk-level guard is a later item.
- The "Mark owes" count: open decisions plus `ready` and `failed` items plus plans waiting for his go. To confirm in the pilot.
- Ship merges straight to the main branch by default. A bad merge that passes tests still lands. The PR mode is the safer choice for bigger repos.
- The 150-line review threshold, the risk tags and the 8-item plan cap are guesses. Tune after 20 items.
- Only one reviewer has read any draft. ChatGPT is still owed a read once `codex login` is done.
