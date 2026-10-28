---
name: ship
description: Mark's go. Merges the item's branch, runs the tests, pushes (or opens a PR), and cleans up the worktree and branch. Use for /ship <id>.
argument-hint: <item id>
disable-model-invocation: true
allowed-tools: Bash(python3 *kit.py*)
---

Item: $ARGUMENTS

Mark typing this command is the push approval. Run `python3 "${CLAUDE_PLUGIN_ROOT}/kit/kit.py" ship <id>` from the project root and show Mark the output. If the id is missing, run `python3 "${CLAUDE_PLUGIN_ROOT}/kit/kit.py" queue` and ask which one. If kit refuses or the tests fail, nothing was pushed: say so in one line and show the reason. If the item has a `session` field, say its chat can be archived. Do nothing else.
