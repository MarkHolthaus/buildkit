---
name: change
description: Send a ready or failed buildkit item back to its builder with Mark's note. Use for /change <id> <note>.
argument-hint: <item id> <note>
disable-model-invocation: true
allowed-tools: Bash(python3 *kit.py*), Agent, Read, Grep, Glob
---

Arguments: $ARGUMENTS

The first word is the item id. The rest is Mark's note, verbatim. If either is missing, ask Mark and stop.

You are the desk. kit is `python3 "${CLAUDE_PLUGIN_ROOT}/kit/kit.py"`. Run `python3 "${CLAUDE_PLUGIN_ROOT}/kit/kit.py" status <id> building --note '<the note>'` from the project root. Put the note in single quotes, writing each single quote inside it as `'\''`. kit adds the note to the item's brief and resets the attempt count. Then follow steps 2 to 5 of the `/buildkit:run` skill for that item (launch the builder with the prompt `# Item <id>` only, then review or ready). For a planned parent, kit tells you to change a child instead: do that.
