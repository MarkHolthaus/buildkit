---
id:            # set by kit new
title:
status: new       # new, planning, waiting, building, failed, review, ready, shipped, dropped (only kit status changes it)
roles: []         # planner, coder, coder-big, ui-designer, reviewer, reviewer-risky
size: small       # quick, small, big
risk: []          # auth, send, public, delete, money, secrets, agent
from: ""          # the inbox line, verbatim
parent: ""        # set by kit plan on child items
children: []      # set by kit plan on a planned parent
files: []         # files to read or change. Required: kit brief fails without it
no_touch: []      # files not to touch, on top of config protected and build/
branch: ""        # bk/<id>, set when a builder starts
worktree: ""      # path under .claude/worktrees/, removed on ship or drop
session: ""       # only if the item ran in a separate chat; archived on ship or drop
shipped: ""       # commit hash, or "pr <url>" in pr mode. Set by kit ship
attempts: 0       # test gate failures, max from config (default 2)
cost: ""          # summary written by kit cost: runs, tokens, max start, Opus runs
created: ""       # set by kit new
---

## Done when
- 

## Brief
What to build, the files involved, what not to touch. Mark's notes from `change` are added here.
Required before any helper starts (`kit brief` fails without them): `files:` in the frontmatter and a done-when above. `no_touch:` adds to the config `protected` list. Language, test command and key paths come from `build/config.yml`, not from this note. A stripped helper has no CLAUDE.md and will guess the stack if the brief leaves it out.

## Work log
One line per step: who, what, result. Written through kit.

## Proof
Written by the test gate, not the worker: exit code, log path, time. UI: screenshot path.

## Review
"skipped: low risk" or the reviewer's findings and what was fixed.

## For Mark
What changed in one or two lines. Questions go to build/decisions.md with a default.

## Flaws
Found and fixed. Remaining and why.
