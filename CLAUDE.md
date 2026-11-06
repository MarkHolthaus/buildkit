# buildkit

A Claude Code plugin that routes ideas to worker subagents (router, coder, ui-designer, reviewer) and returns results to Mark. Read SPEC.md first.

- PUBLIC repo (github.com/MarkHolthaus/buildkit). Anyone can read it. Push, tag, release or open a PR here only when Mark names that exact action in his message. Everything lands in the private `~/Code/buildkit` (remote buildkit-dev) first. Never commit anything from Fred, the vault or Mark's machines.

- Project-agnostic. Never hard-code project names, note-vault paths or app-specific files. Anything per project goes in that project's `build/config.yml`.
- Rules live in code (`kit` script, hooks, agent files), not prose. If a rule can be checked, check it.
- Tests before "done". Every handed-over change ends with a `## Flaws` section.
- Keep usage low: Sonnet by default, Opus only for big code or risky review. Subagents return at most 10 lines.
