---
name: run
description: Run one buildkit item. Moves it to building (kit makes the branch and worktree), launches the right helper, then moves it to review or ready. Use for /run <id>.
argument-hint: <item id>
disable-model-invocation: true
allowed-tools: Bash(python3 *kit.py*), Agent, Read, Grep, Glob, Edit, Write
---

Item: $ARGUMENTS

You are the desk. kit is `python3 "${CLAUDE_PLUGIN_ROOT}/kit/kit.py"`. Run it from the project root. Only kit changes state. Never run `kit ship` here: shipping is Mark's `/buildkit:ship`.

1. `python3 "${CLAUDE_PLUGIN_ROOT}/kit/kit.py" status <id>` and `python3 "${CLAUDE_PLUGIN_ROOT}/kit/kit.py" brief <id>`. If the brief fails, tell Mark what is missing in one line and stop.
2. Pick the path from the item's size and roles:
   - **big, no plan yet** (status new): `python3 "${CLAUDE_PLUGIN_ROOT}/kit/kit.py" status <id> planning`. Launch the Agent tool with `subagent_type: "buildkit:planner"` and the prompt `# Item <id>` only. A launch hook writes the real brief and file map into it. If the reply starts with `QUESTION:`, run `python3 "${CLAUDE_PLUGIN_ROOT}/kit/kit.py" ledger add` with it and stop. Otherwise pipe the reply to `python3 "${CLAUDE_PLUGIN_ROOT}/kit/kit.py" plan <id>` (reply on stdin). Then `python3 "${CLAUDE_PLUGIN_ROOT}/kit/kit.py" queue`. Mark answers go, and the desk runs `python3 "${CLAUDE_PLUGIN_ROOT}/kit/kit.py" status <id> building` on the parent. Then `/buildkit:run <child>` for each child, in order.
   - **quick**: `python3 "${CLAUDE_PLUGIN_ROOT}/kit/kit.py" status <id> building`. Do the change yourself inside the worktree path kit printed in the brief, commit on its branch, then `python3 "${CLAUDE_PLUGIN_ROOT}/kit/kit.py" proof <id>`. If proof passes go to step 4.
   - **small or a child item**: `python3 "${CLAUDE_PLUGIN_ROOT}/kit/kit.py" status <id> building`. Launch the Agent tool with the prompt `# Item <id>` only and `subagent_type`: `buildkit:coder` (small), `buildkit:coder-big` (big child), or `buildkit:ui-designer` (item has the ui-designer role). Never set `model` or `isolation`. If a hook denies the launch, say why in one line and stop.
3. The helper may run in the background. Wait for its completion notice and do not poll. It returns at most 10 lines. A stop hook ran the tests, so look at the item: `python3 "${CLAUDE_PLUGIN_ROOT}/kit/kit.py" status <id>`. If it is `failed`, report that and stop: it goes to Mark. If the helper wrote `no brief`, stop and report.
4. Next state. Try `python3 "${CLAUDE_PLUGIN_ROOT}/kit/kit.py" status <id> ready`. If kit says it needs review (risk tag, or a big diff), run `python3 "${CLAUDE_PLUGIN_ROOT}/kit/kit.py" status <id> review`, then launch the Agent tool with the prompt `# Item <id>` only and `subagent_type` `buildkit:reviewer-risky` when the item has a risk tag from auth, money, send, delete, public, secrets, else `buildkit:reviewer`. Then `python3 "${CLAUDE_PLUGIN_ROOT}/kit/kit.py" status <id> ready --note "<the reviewer reply, at most 10 lines>"`. If the reviewer found real problems, do not mark ready: send it back with `python3 "${CLAUDE_PLUGIN_ROOT}/kit/kit.py" status <id> building --note "<findings>"` and report to Mark.
5. Reply to Mark in at most 5 lines: item id, what changed, proof line, review result, cost. Get them from `python3 "${CLAUDE_PLUGIN_ROOT}/kit/kit.py" queue`.
