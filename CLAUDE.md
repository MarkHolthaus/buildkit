# buildkit

A Claude Code plugin that routes ideas to worker subagents (router, coder, ui-designer, reviewer) and returns results to Mark. Read SPEC.md first.

- Project-agnostic. Never hard-code project names, note-vault paths or app-specific files. Anything per project goes in that project's `build/config.yml`.
- Rules live in code (`kit` script, hooks, agent files), not prose. If a rule can be checked, check it.
- Tests before "done". Every handed-over change ends with a `## Flaws` section.
- Keep usage low: Sonnet by default, Opus only for big code or risky review. Subagents return at most 10 lines.
