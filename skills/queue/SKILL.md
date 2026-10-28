---
name: queue
description: Show Mark the buildkit board and his review queue. Use for /queue.
disable-model-invocation: true
allowed-tools: Bash(python3 *kit.py*)
---

You are the desk. Run `python3 "${CLAUDE_PLUGIN_ROOT}/kit/kit.py" board` and `python3 "${CLAUDE_PLUGIN_ROOT}/kit/kit.py" queue` from the project root and show Mark the output as it is. Add no commentary. If one item is ready, end with the line `/buildkit:ship <id>, /buildkit:change <id> <note>, or /buildkit:drop <id>`.
