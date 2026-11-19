# buildkit

**I built a Claude Code plugin that sends coding jobs to separate AI helpers on their own git branches, with a test gate before anything counts as done. A helper that I trimmed starts at about 5.4k tokens, against 46.7k for a default helper (15 measured runs).**

Solo project. I design it and direct Claude Code to write and test it. Python, git worktrees, Claude Code hooks and agents. MIT licence.

---

*For engineers: the problem, the decisions, the results and what I learned are below.*

## The problem

An earlier multi-chat build process ran 43 chats in about 23 hours, 10 of them only managing other chats. It used 49% of a week's usage in 1.8 days. Every rule that broke, such as model choice, a cap on parallel chats and unique ids, was written in prose and nothing checked it.

**Business value:** AI coding work gets a predictable cost per task, a written record per item, and a test gate before anything is called done.

## Decisions

| Decision | Why |
|---|---|
| Rules live in code (a `kit` script, hooks, agent files), not in prose | If a rule can be checked, it is checked |
| Each builder gets its own git worktree and may only edit inside it | A bad run cannot touch main |
| A test gate runs on stop and allows 2 retries | "Done" means the tests ran, with the proof written to the item file |
| A reviewer runs only on risky changes or diffs over 150 lines | Review costs tokens. Spend it where it pays |
| Helpers start with a trimmed tool list and a written brief | Measured below. Cheaper by a wide margin, but see "what I learned" |
| Every step writes to the item's file through `kit` | Nothing important lives only in a chat |

## How it works

A Claude Code plugin that turns "I want this feature" into reviewed, tested code on its own branch, while keeping token usage low. You dump an idea. A planner splits big ones. Builders work in separate git worktrees. A reviewer checks risky changes. You decide what ships.

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

## What I learned

- **A rule in prose does not hold. A rule a script checks does.** The 43-chat process broke every written rule. Moving the rules into hooks and a script is the whole design.
- **Measure before you design.** I assumed a helper was cheap. A new chat costs about 92k tokens before it does anything, and a default helper about 46.7k. A trimmed one costs about 5.4k. The design came from that table.
- **The cheap helper fails quietly.** The trimmed helper got the project's test command wrong in 2 of 4 runs, because with no project file it guessed the wrong language. Now every brief carries the language, test command and key paths from `build/config.yml`.
- **AI reviewing AI works if you keep score.** The `kit` script got two fresh-thread reviews from ChatGPT: 21 findings, 18 fixed, 3 kept as design choices. Each fix that could be tested has a test.
- **A passing test proves little by itself.** In a later version, not in this snapshot, kit runs the changed tests against the old code and requires them to fail there. A test that passes both before and after proves nothing.
- **How I would explain it in an interview:** I turned a process that wasted usage into a tool that enforces its own rules, and I measured it instead of guessing.

## Install

```
claude plugin marketplace add MarkHolthaus/buildkit
claude plugin install buildkit@buildkit --scope local
```

Run it inside the project that should have it. Copy `templates/config.yml` to `build/config.yml` first and set your language, test command and key paths. Needs Python 3.9 or newer and git.

## Honest limits

- I design the system and direct Claude Code to write and test it. I did not hand-write the code.
- The tests ran on macOS only. The UI-designer screenshot path is unit tested but not run live.
- `templates/config.yml` still holds the example values from my first project.
- This repo is a snapshot from late October (spec draft 4). A later private version has shipped three small features into a separate private repo. It also added a model and effort picker and the changed-test check mentioned above. Those are not here yet.
- The cost table is 15 runs of one read-only task on Sonnet. It shows the size of the gap, not a precise saving.

## What is next

Publish the later version, add a short recorded demo, and run it on a second project to see whether the config template holds up.

Licence: MIT.
