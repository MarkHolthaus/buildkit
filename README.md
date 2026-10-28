# buildkit

A Claude Code plugin that turns "I want this feature" into reviewed, tested code on its own branch, while keeping token usage low. You dump an idea. A planner splits big ones. Builders work in separate git worktrees. A reviewer checks risky changes. You decide what ships.

## Why it exists

An earlier multi-chat build process ran 43 chats in about 23 hours, 10 of them only managing other chats. It used 49% of a week's usage in 1.8 days. Every rule that broke, such as model choice, a cap on parallel chats and unique ids, was written in prose and nothing checked it. buildkit moves those rules into code: a `kit` script, hooks and agent files. If a rule can be checked, it is checked.

**Business value:** AI coding work gets a predictable cost per task, a written record per item, and a test gate before anything is called done.

## How it works

```
/buildkit:idea <text>  -> sized: quick, small or big
  big ideas             -> planner splits into items with a done-when
  builder on a branch   -> own worktree, edits only inside it
  test gate (hook)      -> runs the project's tests, writes the proof, 2 retries
  reviewer              -> only if risky or the diff is over 150 lines
  /buildkit:queue       -> you see what is ready
  /buildkit:ship        -> merge, test, push, clean up the branch and worktree
```

Every step writes to the item's file through `kit`. Nothing important lives only in a chat. Full design: [SPEC.md](SPEC.md).

## What I measured

The worker design depends on one number: what a helper costs before it has done anything. Same read-only task, Sonnet, 15 headless runs ([full table](docs/2026-10-27%20worker%20cost.md)):

| Helper | Start cost (tokens) | Whole run, processed |
|---|---|---|
| Stripped (explicit `tools:` list, `omitClaudeMd`) | about 5.4k | 42k to 78k |
| Default subagent | about 46.7k | 149k to 341k |
| New chat | about 92k | |

A stripped helper starts at about 6% of a new chat. It also failed the task in 2 of 4 runs, because with no project file it guessed the wrong language. So every brief now carries the language, test command and key paths from `build/config.yml`, and a launch hook writes the brief into the prompt itself.

## Proof

```
python3 -m unittest discover -s kit/tests     # 80 tests
python3 -m unittest discover -s hooks/tests   # 48 tests
```

An end-to-end smoke test (`experiments/plugin-smoke.sh`) ran idea, run and ship in a throwaway repo on three install paths with the same result. The kit script got two fresh-thread reviews from ChatGPT: 21 findings, 18 fixed, 3 kept as design choices, each fix that could be tested has a test.

## Install

```
claude plugin marketplace add MarkHolthaus/buildkit
claude plugin install buildkit@buildkit --scope local
```

Run it inside the project that should have it. Copy `templates/config.yml` to `build/config.yml` first and set your language, test command and key paths. Needs Python 3.9 or newer and git.

## Honest limits

- I design the system and direct Claude Code to write and test it. I did not hand-write the code.
- Pilot on a real repo is not done yet. The end-to-end run was in a throwaway repo.
- The tests ran on macOS only. The UI-designer screenshot path is unit tested but not run live.
- `templates/config.yml` still holds the example values from my first project.
- Status: spec draft 4.

Licence: MIT.
