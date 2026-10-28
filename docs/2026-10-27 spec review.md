# Spec review, draft 1

Oct 27 2026. Reviewer: a fresh Claude subagent (Opus) that read only `SPEC.md`. ChatGPT was the planned reviewer but the Codex CLI is signed out (`refresh_token_invalidated`). Claude Code mechanics checked against the docs by a second agent (hooks, sub-agents, plugins pages).

## Facts from the docs that change the design

- Every hook input inside a subagent carries `agent_type`. PreToolUse on the `Agent` tool sees `subagent_type` and the model override before launch. So caps and model checks can be real blocks.
- Plugin agents support `tools`, `model`, `isolation: worktree`, `skills` and `omitClaudeMd`. They ignore `hooks`, `permissionMode` and `mcpServers`, so buildkit hooks go in the plugin's `hooks/hooks.json` and filter on `agent_type`.
- A blocked SubagentStop keeps the same subagent running in the same context, with the block reason as its next instruction. Input has `stop_hook_active`. Command hooks time out at 600 s by default.
- A subagent loads the full `CLAUDE.md` chain and inherits all MCP tools unless told not to. Measured here: the reviewer read one file in 2 tool calls and used about 79k tokens.
- The Agent tool's `totalTokens` covers only the subagent's last request, not the whole run. Background subagents return no usage.
- A plugin can ship agents, skills (slash commands), and hooks together, and installs from a private GitHub repo as a marketplace using the existing `gh` login.

## Findings and what we do with them

| # | Finding | Verdict | Change for draft 2 |
|---|---|---|---|
| 7 | Usage savings overstated: subagents load CLAUDE.md and every MCP tool | Kept. Most important | Every worker gets `omitClaudeMd: true`, an explicit `tools:` list with no MCP, and no Skill tool. First build step measures one worker's real cost before anything else is built |
| 1 | Test gate can loop: a block keeps the same worker retrying | Kept | Hook blocks at most 2 times per item (counter in the item), respects `stop_hook_active`, then sets status `failed` and the item goes to Mark |
| 4 | Parallel workers share one checkout | Kept. Two chats editing the same file in an earlier process is the same bug | Workers run with `isolation: worktree`. Ship = merge the item's branch. Drop = delete it |
| 3 | "No proof, no Mark" is honor system | Kept | Only `kit status` changes status. The hook writes Proof (test exit code, log path), not the worker |
| 5 | Caps are prose; the desk can launch anyway | Kept | PreToolUse hook on `Agent` runs `kit can-start` and denies the launch |
| 6 | One agent file has one model | Kept | Two files: `coder` (Sonnet) and `coder-big` (Opus). The launch hook checks the item's size matches |
| 2 | `tools:` cannot limit paths or commands | Kept | PreToolUse hooks in `hooks.json` by `agent_type`: path check on Edit/Write. Reviewer gets no Bash; `kit` hands it the diff |
| 11 | Plugin hooks fire for every subagent everywhere | Kept | Hooks exit at once unless `build/config.yml` exists and `agent_type` is a buildkit worker |
| 9 | "Never block on Mark" wastes Opus runs on guesses | Kept | Defaults only for quick and small items. Big or risky items wait for his answer |
| 10 | Inbox text can carry instructions into agents with Bash | Kept | Inbox lines only from `/idea` or Mark's own capture, never clippings or forwarded mail. No bypass permissions in headless runs |
| 8 | Router agent plus rule table is over-built | Kept | The desk routes, writing items through `kit new`. No router agent, no rule table in v1 |
| 12 | Missing: ship/drop meaning, state machine, attempt budget | Kept | States: new, building, failed, review, ready, shipped, dropped. Attempt budget per item |
| cut | Fold ui-designer into coder | Rejected | Mark named it as a role. It is one agent file with the design rules preloaded, no extra cost |
| cut | Skip plugin packaging, use project `.claude/` | Rejected | Reuse across projects is the point, and the docs confirm a private-repo plugin works |
| cut | Drop the "Mark owes more than 5" warning | Rejected | It is one check inside `kit can-start`. 13 to 15 owed items in an earlier process is why it exists |
| cut | Drop push notifications, perf scripts, the headless box path | Kept | Later versions |
| order | Pilot on poker-game, not the second project | For Mark (BT-5) | poker-game is in git with 480 tests. The second project waits on BT-4 |

## Not confirmed

- How to get a whole subagent run's token cost. `totalTokens` is the last request only. Needs a test in step 2.
- Whether `tools:` without any `mcp__` entry fully drops MCP tool definitions from the start cost. The docs say MCP is inherited by default. Measure it.
- The meaning of `stop_hook_active` for SubagentStop. The doc example shows it but does not explain it.

## Flaws

- One reviewer, not two. ChatGPT is still owed a read once `codex login` is done.
- The 79k figure is one run. It includes the reviewer's reasoning on Opus, so start cost alone is lower. Step 2 measures it properly.
