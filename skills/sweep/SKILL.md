---
name: sweep
description: List buildkit worktrees and branches that no open item owns. Deletes them only when Mark says yes. Use for /sweep, then /sweep yes.
argument-hint: [yes]
disable-model-invocation: true
allowed-tools: Bash(python3 *kit.py*)
---

Arguments: $ARGUMENTS

You are the desk. Run from the project root.
- No argument: run `python3 "${CLAUDE_PLUGIN_ROOT}/kit/kit.py" sweep`, show the list, and end with: `/buildkit:sweep yes` deletes exactly this list.
- Argument `yes`: run `python3 "${CLAUDE_PLUGIN_ROOT}/kit/kit.py" sweep` first and show it, then `python3 "${CLAUDE_PLUGIN_ROOT}/kit/kit.py" sweep --yes`. Show the output.
Never pass `--yes` for any other argument. kit never touches worktrees or branches it did not make.
