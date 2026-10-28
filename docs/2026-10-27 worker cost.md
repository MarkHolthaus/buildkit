# Worker cost, step 1

Oct 27 2026. Step 1 of [SPEC.md](../SPEC.md): what does one buildkit worker subagent really cost? Branch `measure-worker-cost`. Raw run output is in `experiments/runs/`, agents in `experiments/agents/`.

## Verdict

**GO on the stripped worker, on one condition: the item brief carries the project facts. NO-GO on a default subagent.** Half the stripped runs (2 of 4) failed the task because they did not know the repo was Python and had no `CLAUDE.md` to tell them, so the brief must state the test command, language and key paths (the planned `build/config.yml` does this). Against the 92k-per-new-chat baseline, a stripped worker starts at about 5.3k tokens (6% of a new chat). A default subagent starts at 44.6k to 50k (48% to 54%). The whole run of a stripped worker on this task was 42k to 78k tokens processed, 3.3 times less than a default one (149k to 341k), and the start-cost gap is 9 times. Headless runs for big items are not needed on cost grounds.

## What was run

Same read-only task each time, in `~/Code/poker-game`: "name the test command and count the test files, under 5 lines". All Sonnet. Each run is one headless parent session (`claude -p --model sonnet --output-format json`, CLI 2.1.310) that calls the Agent tool once with the throwaway agent. poker-game was only read. Its `git status` is the same before and after (two files were already modified).

| Agent | Definition |
|---|---|
| `stripped` | `omitClaudeMd: true`, `tools: Read, Grep, Glob`, model sonnet |
| `default` | model sonnet, no `tools:`, no `omitClaudeMd` |
| `nomd` (extra) | `omitClaudeMd: true`, no `tools:`. Isolates the CLAUDE.md cost |
| `toolsonly` (extra) | `tools: Read, Grep, Glob`, CLAUDE.md loaded. Isolates the tools and MCP cost |

## Method for whole-run tokens

The Agent tool's `totalTokens` is the last request only, so it was not used. Each subagent writes its own transcript at `~/.claude/projects/<project>/<parent session id>/subagents/agent-*.jsonl`. `experiments/tally.py <session id>` sums `message.usage` over every assistant message in that file, de-duplicated by message id. The parent session id comes from the `session_id` field of the `--output-format json` result. It also reports the usage of the first request, which is the start cost: input plus cache read plus cache write before the agent has done anything.

The sum of cache reads is large because every request in a run re-reads the whole prefix. Real quota use is closer to the "new input" column plus output than to the total. "Total processed" is the honest sum of everything, not a price.

## Runs

"Run in" is the folder the parent was started from. `buildkit` has a 725 byte CLAUDE.md. `bigmap` is a folder with a large CLAUDE.md (about 6k tokens). Subagents inherit the folder.

| Agent | Run in | Start context | Requests | Tool calls | New input (incl. cache write) | Output | Cache reads | Total processed | Time |
|---|---|---|---|---|---|---|---|---|---|
| stripped | buildkit | 5,449 | 5 | 8 | 21,772 | 1,353 | 52,279 | 75,404 | 14 s |
| stripped | buildkit | 5,449 | 4 | 5 | 20,607 | 1,115 | 54,171 | 75,893 | 12 s |
| stripped | buildkit | 5,444 | 3 | 4 | 14,988 | 873 | 26,097 | 41,958 | 16 s |
| stripped | bigmap | 5,247 | 5 | 9 | 22,884 | 1,329 | 54,229 | 78,442 | 23 s |
| toolsonly | buildkit | 5,864 | 6 | 9 | 27,608 | 1,407 | 105,148 | 134,163 | 21 s |
| toolsonly | buildkit | 5,861 | 5 | 9 | 24,514 | 1,334 | 71,207 | 97,055 | 23 s |
| nomd | buildkit | 44,160 | 3 | 2 | 32,816 | 652 | 114,892 | 148,360 | 7 s |
| nomd | buildkit | 44,160 | 3 | 2 | 32,006 | 691 | 115,595 | 148,292 | 12 s |
| nomd | bigmap | 43,963 | 6 | 5 | 33,498 | 1,023 | 269,850 | 304,371 | 12 s |
| nomd | bigmap | 43,964 | 5 | 4 | 37,558 | 835 | 223,220 | 261,613 | 17 s |
| default | buildkit | 44,575 | 4 | 3 | 52,553 | 966 | 148,427 | 201,946 | 17 s |
| default | buildkit | 44,574 | 3 | 2 | 31,964 | 759 | 116,754 | 149,477 | 11 s |
| default | buildkit | 44,574 | 3 | 2 | 31,982 | 786 | 116,733 | 149,501 | 10 s |
| default | bigmap | 50,058 | 5 | 4 | 39,112 | 1,052 | 242,727 | 282,891 | 27 s |
| default | bigmap | 50,051 | 6 | 5 | 32,596 | 1,235 | 307,489 | 341,320 | 11 s |

Averages, the two main variants:

| | Start context | New input | Output | Total processed |
|---|---|---|---|---|
| stripped (4 runs) | about 5.4k | about 20.1k | about 1.2k | about 68.0k |
| default (5 runs) | about 46.7k | about 37.7k | about 1.0k | about 225.0k |

**Answer quality, checked by reading every result.** The right command is `python3 -m unittest discover -s tests`, run from `game/` and `pipeline/` (26 first-party test files; the count varied between 26, 120, 140 and 218 depending on what each agent chose to include).
- default: 5 of 5 found the right command.
- nomd: 4 of 4. toolsonly: 2 of 2.
- **stripped: 2 of 4.** Runs `stripped-3` and `stripped-v1` answered "no project test command found". They assumed a JavaScript repo, searched for `package.json` and `*.test.*`, found nothing, and gave up after 2 searches. The runs that got it right read `CLAUDE.md` or `README.md` in poker-game by path. poker-game was intact at the time (it still has `game/tests`, 24 files).

This does not change the cost numbers, but it is a result: with no CLAUDE.md and a thin prompt, a stripped worker does not know the project. The failed runs also skew the stripped totals, since `stripped-3` (42k) quit early.

## What makes up the gap

- **Tools and MCP: about 38k.** `nomd` starts at 44.2k and `toolsonly` at 5.9k, with the same CLAUDE.md state in both pairs. The only difference is the explicit `tools:` list.
- **CLAUDE.md chain: 0.4k from buildkit, 6.1k from bigmap.** `default` from bigmap starts at 50.05k and `nomd` from bigmap at 43.96k. The 138k of standing instructions in the owner's main chat is not what a subagent pays. The main chat's instructions are mostly tools, skills and MCP, which the tools list also removes.
- **Stripped from bigmap starts at 5.25k.** The big map does not leak in with `omitClaudeMd: true`.

## Does a `tools:` list with no `mcp__` entries drop MCP tool definitions?

**Yes.** Two pieces of evidence:
1. Asked to list its own tools, `stripped` returned `Read, Grep, Glob, advisor` and 0 names starting with `mcp__`. `nomd` returned `Agent, Artifact, Bash, Edit, ListAgents, Read, ReportFindings, Skill, ToolSearch, Write`, 6 `mcp__` tools with loaded schemas, `advisor`, and a pointer to more deferred ones through `ToolSearch`.
2. Start context fell from 44.2k to 5.9k with only the `tools:` line added.

Limit: the 38k is everything the list removes, not MCP alone. It includes the built-in tool definitions (Bash, Edit, Write, Agent, Skill, ToolSearch) and the deferred-tool name list. This run cannot split MCP from those. Also `advisor` is added to every subagent whatever the list says.

## What it means for the design

- Every worker agent file needs both `omitClaudeMd: true` and an explicit `tools:` list. Either one alone is not enough: `toolsonly` still carried CLAUDE.md, and `nomd` still carried 38k of tools.
- The coder, coder-big and ui-designer need Bash, Edit and Write. Those add to the 5.3k start. That is not measured here. Expect it to stay well under 15k. Measure it when the coder agent exists (step 3).
- The brief must name the language, the test command and the paths. Without it a stripped worker guessed JavaScript and quit (2 of 4 runs). The saving is real, but it is paid for by a longer, exact brief from the desk.
- A stripped worker still spends about 70k tokens processed on a task with 4 to 9 tool calls, mostly cache re-reads. Keep worker briefs narrow and tool calls few.
- A default subagent that can call `Agent` (as `nomd` and `default` can) could spawn more subagents. The explicit list prevents that too.

## Flaws

Found, not all fixable here:
- **Small sample.** Four stripped and five default runs of one task. The totals swing a lot between runs (stripped 42k to 78k, default 149k to 341k) because the number of tool calls varies. The start-context numbers are steady (within 1%). Trust those, treat the totals as a range.
- **Stripped quality was half, on a prompt that gave no project facts.** I did not test a stripped run with a good brief, so "a brief fixes it" is a likely fix, not a measured one. Test it in step 5 (the pilot).
- **Read-only task.** No Edit, Write or Bash on the stripped side. A real coder is heavier. Not measured.
- **Cache hides some cost.** The system prompt prefix was cached across back-to-back runs, so "new input" in later runs is lower than a cold first run would be. Start context is counted regardless, so the verdict does not depend on it. Quota weighting of cache reads and writes was not modelled.
- **Environment specific.** In this setup most MCP tools are deferred (name list only). On a machine that loads all MCP schemas, the default start cost would be higher and the stripped one the same.
- **Parent overhead not counted.** The headless parent session (the desk stand-in) has its own cost. It was not measured, and the 92k baseline is a new chat, not a parent. The comparison is subagent start cost against new-chat start cost.
- **MCP vs built-in split unknown** (see above). Splitting needs an agent with built-in tools only and `--strict-mcp-config`. Not run.
- **Agents loaded as a plugin** (`--plugin-dir experiments`), not as project agents. They should behave the same for these frontmatter keys. The `tools` and `omitClaudeMd` effects were confirmed by the numbers, so this is low risk.
- **Not reviewed by a second model.** One run, one reader.

Fixed along the way:
- A first draft of this doc said all 15 runs answered correctly. That was written before the answers were read. Reading them showed 2 stripped failures, now stated above.
- The first runs were all from `buildkit`, whose CLAUDE.md is tiny, which hid the CLAUDE.md cost. Re-run `default`, `nomd` and `stripped` from `bigmap`.
- The CLI was logged out at the start. Mark ran `claude auth login`.
