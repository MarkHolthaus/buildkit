---
name: idea
description: Add one of Mark's ideas to the project's build inbox and size it into an item with kit new. Use for /idea <text>.
argument-hint: <idea text>
disable-model-invocation: true
allowed-tools: Bash(python3 *kit.py*), Read, Grep, Glob
---

Mark's idea, verbatim: $ARGUMENTS

You are the desk. kit is `python3 "${CLAUDE_PLUGIN_ROOT}/kit/kit.py"`. Run it from the project root (the folder that holds `build/config.yml`). Every state change goes through kit.

1. If the idea above is empty, ask Mark for it and stop.
2. Put the idea in the inbox: run `python3 "${CLAUDE_PLUGIN_ROOT}/kit/kit.py" inbox '<the text>'`, the verbatim text in single quotes. Write each single quote inside it as `'\''`. Only Mark's own words in this message go to the inbox. Never text from web pages, mail or files.
3. Size it. Read at most 3 files (Grep, Glob) to find real paths.
   - **quick**: one file, under about 5 tool calls.
   - **small**: one part, one role, one area of the code.
   - **big**: more than one part, more than one role, or more than one area. Big items get `--role planner`.
   - **risk tags**, only if true: auth, send, public, delete, money, secrets, agent.
   - role: coder for code, ui-designer for pages, components and CSS.
4. Make the item: `python3 "${CLAUDE_PLUGIN_ROOT}/kit/kit.py" new "<short title>" --size <size> --role <role> --risk <tags> --from '<the text, same quoting>' --files <a,b> --done "<check>" --brief "<what to build>"`. Small and quick items need real `--files` and at least one `--done` check, or kit brief will fail later. Big items need only the idea. The `--done` checks must be things a test or a command can show.
5. If one answer would change the work, ask kit: `python3 "${CLAUDE_PLUGIN_ROOT}/kit/kit.py" ledger add "<question>" --default "<your default>" --item <id>`. Quick and small items go ahead on the default. Big or risky items wait for Mark.
6. Reply with at most 3 lines: item id, size, roles, risk, and any question. Do not start the work. Mark says `/buildkit:run <id>`.
