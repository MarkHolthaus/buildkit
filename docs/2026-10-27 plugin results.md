# Agents, skills and hooks, steps 3 and 4

Oct 27 2026. Steps 3 and 4 of [SPEC.md](../SPEC.md), built as one Claude Code plugin. Branch `plugin-agents-hooks`, pushed, not merged to main. SPEC is now draft 4.

## Result

The plugin works end to end. In a throwaway repo, `/buildkit:idea` created an item, `/buildkit:run` moved it to `building` (kit made the worktree), a `buildkit:coder` helper fixed one line and committed, the test gate wrote the proof, the item reached `ready`, and `/buildkit:ship` merged, pushed and cleaned up. That ran 3 times on 3 install paths with the same result: `--plugin-dir`, a local marketplace folder, and the private GitHub repo as a marketplace. Nothing was run against poker-game.

Proof, from `~/Code/buildkit`:

```
python3 -m unittest discover -s hooks/tests
Ran 48 tests in 27.495s
OK

python3 -m unittest discover -s kit/tests
Ran 80 tests in 47.822s
OK
```

The kit suite was 74. It is 80 because I added two small kit commands (`note`, `inbox`) with 6 tests. The first 74 still pass unchanged.

Coder start cost with Edit, Write and Bash: **8,390 tokens** (3 runs: 8,390, 8,390, 8,399, plus 8,438 on a fourth). Target was under 15k. The default subagent was 46.7k.

## What was built

| Path | What |
|---|---|
| `.claude-plugin/plugin.json`, `marketplace.json` | Plugin `buildkit` 0.1.0 and a one-plugin marketplace `buildkit`, source `./`. `claude plugin validate .` passes (one warning: `CLAUDE.md` at the plugin root is not loaded, expected) |
| `agents/` | planner, coder, coder-big, ui-designer, reviewer, reviewer-risky. Every file: `omitClaudeMd: true`, explicit `tools:`, no MCP, no Skill, no `isolation`. Models: Sonnet, except coder-big and reviewer-risky on Opus. Tools: planner and reviewers `Read, Grep, Glob`; builders add `Edit, Write, Bash`. Each prompt says: the job comes from `kit brief`, return at most 10 lines, details go to the item through kit |
| `skills/` | `idea`, `run`, `queue`, `ship`, `drop`, `change`, `sweep`. Each calls the matching kit command. All are `disable-model-invocation`, so only Mark's own command runs them. `/run` runs `kit status <id> building` first, which makes the worktree |
| `hooks/hooks.json` and `hooks/*.py` | `launch_gate.py`, `path_guard.py`, `test_gate.py`, `cost_note.py`, shared `bkhook.py`. Python, stdlib only, 48 tests in `hooks/tests/test_hooks.py` |
| `kit/kit.py` | Two additions: `kit note <id> <text>` (a helper's detail line in the work log, never a state change) and `kit inbox <text>` (append one idea line). The brief now tells builders the exact `kit note` command |
| `experiments/plugin-smoke.sh` | The end-to-end smoke test. Builds a temp repo with a bare remote and a failing one-line bug, then runs the idea, run and ship steps as headless Sonnet desk sessions |

## Where the evidence changed the SPEC

These are in SPEC draft 4.

1. **The launch gate rewrites the prompt instead of checking it.** A PreToolUse hook can return `permissionDecision: allow` with `updatedInput`, and the helper then gets the new prompt (tested: a hook replaced the prompt and the helper answered the replacement). So the desk launches with `# Item <id>` and the hook swaps in the output of `kit brief` (the planner also gets `kit map`). A helper never gets a prompt the desk wrote, the desk does not copy a long brief into a tool call, and the "thin prompt" failure from step 1 cannot happen. Cost: if hooks fail to load, the helper sees only `# Item 1`. Each agent file tells it to reply `no brief` if its prompt lacks the brief's section.
2. **The cost note moved from PostToolUse to SubagentStop.** The Agent tool returns at launch for a background helper (`status: async_launched`, 10 ms). In this version (2.1.310) a helper launches in the background even when the call does not ask for it. PostToolUse fires before the run exists. SubagentStop fires when the helper ends, in both cases.
3. **The transcript is not finished when SubagentStop fires.** One smoke run recorded 36,927 tokens against 51,680 real, one request short. The cost hook now waits until the helper's final message (`last_assistant_message` in the hook input) is in the transcript tail and the file has stopped growing, up to 8 seconds, and writes a work-log note if it gave up.
4. **Helpers must use `git -C <worktree>`.** `cd <worktree> && git add && git commit` was denied by the permission system in a headless run (the same command without `cd` is fine). Three early smoke runs failed on this, so the item went `failed` through the gate. The agent files now say `git -C`, one-line messages, no `cd ... && git`.
5. **Slash commands carry the plugin name.** `/buildkit:idea`, `/buildkit:run 1`. The `${CLAUDE_PLUGIN_ROOT}` variable is substituted inside skill text, so skills call `python3 "${CLAUDE_PLUGIN_ROOT}/kit/kit.py"`.
6. **ui-designer:** no `skills:` preload and no screenshot tool. The design rules come in the brief (`design_rules` in config), which is project specific and cheaper. A screenshot tool would be an MCP tool, which the spec bans. So the helper takes the screenshot with a headless browser command through Bash and saves it to `build/shots/<id>.png`. The test gate passes that file to `kit proof --screenshot` (kit checks it is newer than the last edit). This path is unit tested only, not run live.
7. **The path guard is stricter than the spec.** Builders may edit only inside their own item's worktree, not the main checkout and not another item's worktree. It also denies `.git`, config `protected`, the item's `no_touch`, ignores case, and covers MultiEdit and NotebookEdit.

## Answers to the open questions

**What does a SubagentStop hook input contain, and what folder does it run in?** Measured with a logging hook (`probe`). Fields: `session_id`, `transcript_path` (the parent's transcript), `cwd`, `scratchpad_dir`, `prompt_id`, `permission_mode`, `effort`, `hook_event_name`, `agent_id`, `agent_type`, `stop_hook_active`, `agent_transcript_path`, `last_assistant_message`, `background_tasks`, `session_crons`. The hook process runs in the project root, the same folder as `cwd` and `CLAUDE_PROJECT_DIR`. It does not run in the worktree, even though the helper edited and ran commands there. So the hooks find the worktree through the item, as the SPEC's fallback said. The helper's transcript is at `agent_transcript_path`, and its first line holds the prompt, which is how a hook learns the item id (`# Item <id>` is the first line of every brief). `CLAUDE_PLUGIN_ROOT` is set for hooks.

**What does `stop_hook_active` mean?** It is `false` on a helper's first stop and `true` on a stop that comes after a stop hook blocked it. Test: a hook exited 2 once with a message on stderr. The helper received the message as its next instruction (it wrote the second file the message asked for), then stopped again, and that second SubagentStop input had `stop_hook_active: true`. It is a flag, not a counter, and it says nothing about how many blocks came before. The test gate does not use it. kit counts failed proofs in the item, so the loop ends at the attempt budget (2 blocks, the third failure sets `failed`). Smoke runs 1 and 2 showed the full path: 2 blocks, then `failed`.

**Can plugin hooks tell a buildkit helper from other subagents?** Yes. `agent_type` is `buildkit:coder` for a plugin agent, `probe:probe` for another plugin's agent, and a bare name like `general-purpose` for a built-in. On the Agent tool the same string is `tool_input.subagent_type`. The hooks require the `buildkit:` prefix, so a user's own agent called `coder` is never mistaken for ours. Hooks do fire for every subagent in every project: a built-in `general-purpose` subagent in a buildkit project triggered all three SubagentStop and PreToolUse hooks, and each exited silently (cost rows unchanged, no output). PreToolUse on Agent has no `agent_id` (it runs in the parent). PreToolUse on Edit and Write inside a helper has `agent_id` and `agent_type`. I did not capture a parent session's Edit, so I infer (from the Agent call lacking them) that the parent's has neither.

**Does a plugin agent honor `omitClaudeMd` and `tools:` through the marketplace?** Yes. The coder's first request was 8,390 tokens through `--plugin-dir`, 8,390 through the local marketplace folder and 8,399 through the GitHub marketplace (the 9 tokens are a longer item title). A default subagent starts at 46.7k, so both keys took effect. I did not list the helper's tools directly in marketplace mode. The start cost and the path guard seeing Edit and Write calls are the evidence.

## Coder start cost

Measured with `experiments/tally.py` and `kit cost`, which agree. The coder has `Read, Grep, Glob, Edit, Write, Bash` and the stripped agent text.

| Run | Install | Start (first request) | Whole run | Tool calls |
|---|---|---|---|---|
| smoke 3 | `--plugin-dir` | 8,390 | 51,680 | 4 |
| smoke 4 | local marketplace | 8,390 | 51,639 | 4 |
| smoke 6 | GitHub marketplace | 8,399 | 65,456 | 4 |
| guard test | `--plugin-dir` | 8,438 | 52,127 | 2 |

Start cost is 8.4k, under the 15k target and 3k above the read-only agent (5.4k). Edit, Write and Bash definitions plus the longer agent text account for the 3k. A one-line change still costs about 50k to 65k processed, mostly cache re-reads, the same shape as step 1. Four runs of one tiny task is a small sample.

## Commands

Load for one session (no install):

```
claude --plugin-dir ~/Code/buildkit
```

Install from a local marketplace folder (reads the folder in place, so edits show up at once):

```
claude plugin marketplace add ~/Code/buildkit
claude plugin install buildkit@buildkit --scope local
```

Run the install line inside the project that should have it. `--scope local` writes only that project's `.claude/settings.local.json`. Use `--scope project` to share it through the repo's settings or `--scope user` for every project.

Install from the private GitHub repo (uses the existing `gh` login). I tested it on a branch:

```
claude plugin marketplace add MarkHolthaus/buildkit
claude plugin install buildkit@buildkit --scope local
claude plugin marketplace update buildkit
```

I removed the marketplace and the test installs afterwards, so Mark's plugin settings are as they were. Each project also needs `build/config.yml` copied from `templates/config.yml` first (there is no `kit init`).

Re-run the smoke test (about 4 minutes, three headless Sonnet sessions, writes only under the folder you give it):

```
~/Code/buildkit/experiments/plugin-smoke.sh <workdir> --plugin-dir ~/Code/buildkit
```

## Review

Reviewer: ChatGPT through `ask_codex`, fresh thread, given the five hook files, the hooks table and the measured facts. 11 findings plus 3 that depend on kit. Over 350 lines of hook code went in, so this was required.

| # | Finding | What I did |
|---|---|---|
| 1 | A hook crash or timeout exits non-zero, which does not block, so a launch would go through with the thin prompt | Fixed: `bkhook.run` turns any crash into a deny once the hook knows it applies. Each kit call has a 15 s limit and the hook limit is 90 s, so a stalled kit call becomes a deny. Test. Also set `onFailure: "block"` on both PreToolUse hooks (added in 2.1.295). Tested live with a probe hook that exits 1: blocked with the key, launched without it |
| 2 | The test gate lets the helper stop when it cannot run | Kept by design, now visible: the item gets a work-log note. No passing proof means the item cannot reach `ready`, and kit still counts attempts. Test |
| 3 | A helper whose transcript could not be read could edit any item's worktree | Fixed: no known item means deny. The transcript is read with a short retry. Test |
| 4 | `BUILD/` and `.GIT` bypass the protected paths on a case-insensitive disk (the macOS default) | Fixed: all path checks ignore case. Test |
| 5 | A symlinked folder (`build` pointing at `data`) hides a protected name | Fixed: the typed path and the resolved path are both checked. Test |
| 6 | Race between the check and the write (swap a folder for a symlink in between) | Not fixable in a hook. Needs a concurrent writer, and Bash is the only one a helper has. See Flaws |
| 7 | Glob entries match files but not the folders under them, and `**/x` misses the top level | Fixed: a glob is tested against the path and each folder above it, and `**/` also matches zero folders. Test |
| 8 | Cost wait can return early, and gives up silently | Fixed: waits for the final message to appear, and writes a note if it gave up. Test |
| 9 | Cost hook timeout budget | Fixed: kit call limited to 40 s, hook limit 90 s |
| 10 | A bad `BUILDKIT_HOOK_LOG` path crashes hooks for non-buildkit agents | Fixed: logging never fails. Test |
| 11 | A planner launches with an empty file map when `kit map` fails | Fixed: deny. Test |
| kit | Parallel updates, repeated charging, stale state between brief, can-start and launch | Parallel updates and double charging: already covered by kit core (one lock, one cost row per transcript file, both tested in step 2). The window between can-start and launch stays, and a state change in it needs Mark or the desk acting at the same moment |
| extra | `Edit\|Write` misses `NotebookEdit` and `MultiEdit` | Fixed in the matcher. Test |

The fixes in `path_guard.py`, `test_gate.py` and `cost_note.py` (about 90 lines) have not been read by anyone fresh. Only the 48 tests cover them.

Two live checks of Claude Code behaviour, beyond the unit tests:
- A launch for an item still in `new` came back to the desk as `PreToolUse:Agent hook error: item 2 is new. Builders run on items in building (kit status 2 building).` The agent never started.
- A `deny` from a plugin hook on `Write` reached the helper as `PreToolUse:Write hook error: <reason>` and no file was written (tested with a probe hook, not `path_guard.py` itself).

## Open items

- **Only the coder was run live.** The planner, coder-big, ui-designer, reviewer and reviewer-risky agent files, the `/run` planner path and the review path are covered by hook and kit unit tests, not by a live helper run. The pilot is the first live run of those.
- **`path_guard.py` has never denied a real helper.** The one live attempt did not happen: the helper obeyed its own rules and skipped the `build/` write, so the guard was never asked. The allow path ran live in every smoke run. The deny path is unit tested, and the deny output format is proven live with a probe hook.
- **Bash is not guarded.** See Flaws.
- **`kit init`** does not exist. A new project copies `templates/config.yml` by hand.
- **The planner's `QUESTION:` line** is parsed by the `/run` skill text only. kit does not know it. If the desk skips that rule, `kit plan` fails on it and the item stays in `planning`.
- **poker-game config:** the pilot config needs both test folders chained with `&&` (from the step 2 results).
- **After the merge:** drop the branch ref from the GitHub install command above.
- **Desk cost is not measured.** Each skill body is read into the desk chat when run. They are short (11 to 22 lines) but I did not count them.

## Flaws

Found and fixed:
- The first launch gate checked that the desk's prompt matched `kit brief`. That fails the moment a desk adds a word and costs the desk a copy of the whole brief. Replaced with the prompt rewrite.
- The first cost hook sat on PostToolUse and never recorded anything for a background helper. I found it only because `kit queue` said `cost: none` after a successful run.
- Cost recording undercounted by one request in one run. Fixed, then reviewed.
- The first smoke runs failed because helpers could not commit (`cd ... && git`). Found by reading the helper transcript, not by trusting the desk's summary.
- Seven fixes from the ChatGPT review, each with a test (table above). Three of them (crash passes, unknown owner, case) were real bypasses.
- A first test asserted only `deny` and would have passed on a crash. I replayed the glob and case cases and read the reasons, which are the intended ones.

Remaining:
- **Bash is open.** A builder can write `build/`, a protected file or the main checkout with a shell command, and can swap a folder for a symlink between the guard's check and the write. kit still refuses a proof from a dirty or moved branch, and `ready` needs a passing proof for the exact head, so a bad write shows up there, but it is not blocked. Fix options: a Bash guard hook (heuristic, will leak) or take Bash away from builders that do not need it (they need it for tests and git).
- **The desk is not covered.** It runs on Mark's chat and can edit code outside an item. Same as the SPEC's earlier flaw.
- **Instructions in a helper's prompt are not all held.** The coder was told "no trailers" and added `Co-Authored-By` anyway. It said an attribution rule in its context caused it. That rule is the desktop app's, which `omitClaudeMd` does not remove. Harmless here, but it shows prose rules still break, even in a stripped helper. Keep the rule-in-code approach.
- **Small sample.** One tiny task, headless desk, Sonnet, 4 to 6 runs. No interactive desk session was tried. No helper has run on a real project.
- **A helper may stop with no proof if the gate cannot run.** Visible in the item's work log, not blocked.
- **The cost total can still be short** if the transcript never gets the final message in 8 seconds. The item says so in the work log.
- **A kit crash during the launch gate denies the launch.** Right for safety, but a broken kit stops all work until fixed, and the desk shows the message.
- **Python 3.9 or newer is needed** (`str.removeprefix`). macOS python3 has it. Not run on Linux.
- **Hook timeouts are guesses** (90 s launch, 2000 s test gate for a 1800 s test timeout).
- **Reviewed by one model, once.** The fixes after the review were read by nobody.
