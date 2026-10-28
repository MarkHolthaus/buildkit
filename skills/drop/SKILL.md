---
name: drop
description: Drop a buildkit item. Deletes its bk branch and worktree and marks it dropped. Use for /drop <id>.
argument-hint: <item id>
disable-model-invocation: true
allowed-tools: Bash(python3 *kit.py*)
---

Item: $ARGUMENTS

Mark typing this command is the approval to delete that item's own branch and worktree. Run `python3 "${CLAUDE_PLUGIN_ROOT}/kit/kit.py" drop <id>` from the project root and show the output. kit only touches `bk/<id>` branches and worktrees it made. If the id is missing, run `python3 "${CLAUDE_PLUGIN_ROOT}/kit/kit.py" queue` and ask which one. Do nothing else.
