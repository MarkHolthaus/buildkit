# kit core, step 2

Oct 27 2026. Step 2 of [SPEC.md](../SPEC.md). Branch `kit-core`, not merged to main.

## Result

`kit/kit.py` is built: one Python 3 script, stdlib only, all 16 commands. 74 tests pass. ChatGPT reviewed it twice in fresh threads: 15 findings on the first pass (13 fixed, 2 kept as design choices), then 6 on the fixes (5 fixed, 1 kept). Every fix that could be tested has a test.

Proof, from `~/Code/buildkit`:

```
python3 -m unittest discover -s kit/tests
Ran 74 tests in 44.9s
OK
```

`python3 kit/kit.py --help` lists: new, plan, brief, map, status, can-start, diff, proof, cost, board, queue, ship, drop, sweep, ledger (with `ledger add` and `ledger answer`).

## What was built

| File | What |
|---|---|
| `kit/kit.py` | The tool. 1,637 lines |
| `kit/tests/test_kit.py` | 74 unittest cases. Each one makes a temp git repo with a bare repo as `origin`. No network |
| `templates/config.yml` | Project config with the required keys and the limits |
| `templates/item.md` | Added `files:`, `no_touch:` and `created:` fields. `shipped:` notes the PR form |

How each rule from the task is held, in code:

| Rule | How |
|---|---|
| Unique ids with two writers | Every write holds an `fcntl` lock on `build/.lock`. Ids come from a counter file and never reuse a number. Tested with 12 `kit new` processes started at once: ids 1 to 12, no repeats |
| Only kit changes state, only along the machine | One `transition()` function. `TRANSITIONS` is a constant. A test tries every disallowed pair (over 50) and each one is refused. `kit status` cannot set `shipped` or `dropped`; only `ship` and `drop` can. Every change is logged to `build/status.log`; a status edited by hand is caught and kit refuses to act on the item |
| `brief` fails on gaps | Config `language`, `test_command`, `key_paths`; item done-when and `files:`. Each gap has its own test. `kit status <id> building` also fails when the brief cannot be built |
| `can-start` enforces the launch gate | State fits the agent, agent fits size and risk, big-building cap, attempt budget, brief builds, open questions on big or risky items, Mark owes over the limit for big items (his quick ones listed first). Exit 1 with `DENY: <reason>` on stderr |
| ship and drop only touch what kit made | Both refuse unless the branch is `bk/<id>` and the worktree is under `.claude/worktrees/` and registered on that branch. Tests point an item at Mark's own branch and at a worktree outside `.claude/worktrees/`; both survive |
| ship tests before push | Merge and tests run in a temporary worktree, so Mark's checkout and `main` are never touched by a failed ship. Push only if tests pass. Tests check the bare remote's `main` did not move after failing tests and after a merge conflict |
| `ship: pr` | Pushes the tested commit to `bk/<id>` and runs `gh pr create`. `shipped:` holds `pr <url>`. Tests use a fake `gh` (`KIT_GH`) |
| sweep never deletes without `--yes` | Lists orphan `bk/` worktrees and branches (local and remote). Mark's worktrees and non-`bk/` branches survive `--yes` |
| cost sums whole runs | `tally()` is the loop from `experiments/tally.py`. A test runs both on the same transcript and compares every number |

## Design calls I made

Mark accepted 1 and 2 on Oct 27 (BT-7, BT-8): "Accept both design calls: build/ stays out of git, and kit makes the worktrees so step 3 agents drop isolation: worktree."

1. **`build/` is kept out of git.** kit adds `/build/` and `/.claude/worktrees/` to `.git/info/exclude`, so state writes never dirty Mark's checkout or conflict on merge. Cost: `build/` is backed up only by Time Machine, not GitHub.
2. **The desk moves an item to `building` before launching.** That transition creates `bk/<id>` and the worktree at `.claude/worktrees/bk-<id>`. `can-start` then requires `building` for builders, `planning` for the planner, `review` for reviewers. The hook cannot change state, so this is the only way that fits "only `kit status` changes a state". It also settles the spec's open question about where worktrees land: kit makes them, so the agents in step 3 should not use `isolation: worktree`.
3. **`proof` records, it does not change state.** Exit 0 pass, 2 fail with retries left (block), 3 budget spent (the hook then runs `kit status <id> failed`). With `attempts: 2` that is 2 blocks and a third fail goes to Mark, as in the spec.
4. **Ready means tested at the exact branch head.** `building -> ready` and `ship` both refuse if the last proof is not for the current `bk/<id>` head, or the worktree has uncommitted files. A commit after review voids the proof.
5. **Extra transitions the spec did not name:** `failed -> building` (needs Mark's note), `ready -> failed` (ship's tests failed), and `dropped` from any open state.
6. **A planned parent follows its children.** It goes `ready` when all children are ready, back to `building` if one reopens. Shipping it ships the ready children in plan order.
7. **Item files are structured.** `files:` and `no_touch:` are frontmatter lists, not prose under `## Brief`.

## Review

Reviewer: ChatGPT through `ask_codex` (`codex login` works now), fresh thread, given only `SPEC.md` and `kit/kit.py`.

| # | Finding | What I did |
|---|---|---|
| 1 | Ship could rewind local `main` if Mark committed during the tests | Fixed: fast-forward only from the tip the merge was built on; skip if `main` is checked out elsewhere. Test |
| 2 | Ship deleted remote `bk/<id>` even with unmerged commits on it | Fixed: delete only if the remote tip is in the merge, with `--force-with-lease`. Test |
| 3 | An existing `bk/<id>` kit did not make was adopted, then could be dropped | Fixed: refused. Test |
| 4 | Sweep treated any `kit-ship-*` worktree as its own, including a live ship | Fixed: only in the temp dir, and not while listed in `build/shipping/`. Test |
| 5 | A commit after review could ship unreviewed | Fixed: ship checks proof is for the exact head. Test |
| 6 | PR mode pushed the branch name, not the tested commit | Fixed: pushes and merges the captured SHA |
| 7 | An empty `test_command` would pass and ship untested | Fixed: refused at ship and proof. Test |
| 8 | Proof recorded the head after the run, not the one tested | Fixed: captured before, flagged if it moved. Test |
| 9 | Sweep ran a repo-wide `git worktree prune` even when only listing | Fixed: kit never prunes. Mark's CLAUDE.md warns that worktrees look prunable from a sandbox. Test |
| 10 | The planner brief skips the done-when and files checks | Kept. The planner gets a raw idea; its job is to produce files and done-when for the children. The spec gives it only the idea, config and `kit map` |
| 11 | A question added after an item started did not block its relaunch | Fixed: `can-start` rechecks. Test |
| 12 | The big-building cap did not apply to planner or reviewer launches | Kept. The cap is on big items building at once (Opus builders), per the launch gate row. Planner and reviewers do not build |
| 13 | Proof allowed a third try, can-start denied it | Fixed: one budget function used by both. Test |
| 14 | A ready parent could not take a change note | Clearer error: change a child with `kit status <child> building --note` |
| 15 | Recording the same transcript twice doubled the cost | Fixed: one row per transcript file. Test |

I also added a lease to the remote delete in `sweep --yes`.

Second pass, fresh ChatGPT thread, given only the rewritten functions and what each fix was meant to do:

| # | Finding | What I did |
|---|---|---|
| 1 | Tests that change HEAD or tracked files in the ship worktree would ship a tree that was not the one tested | Fixed: merge SHA captured before tests; ship fails if HEAD moved or tracked files changed. Test |
| 2 | `sweep --yes` and `drop` delete remote branches whose commits were never shipped | Kept. That is what they are for, and both run only on Mark's word. The lease stops a delete of anything pushed after the listing |
| 3 | Sweep trusted any `kit-ship-*` name in the temp dir | Fixed: kit logs every temp worktree it makes in `build/ship-temps.log`; sweep only takes those. Test |
| 4 | Two ships of the same item at once shared one marker | Fixed: a second ship is refused while the first's worktree exists. Test |
| 5 | Proof trusted the worktree's checkout | Fixed: proof fails unless the worktree is on `bk/<id>` at its tip with no uncommitted changes. Test |
| 6 | `--exit 0` recorded a pass with an empty test command | Fixed: refused. Test |

## Open items

- **Step 3 agent files must not set `isolation: worktree`** (BT-8). The brief already names the worktree path.
- **No `kit init`.** A project gets `build/config.yml` by copying `templates/config.yml`. Step 4 or the plugin skill can add a setup command.
- **The desk edits Brief prose by hand.** There is no `kit set` for fields after `kit new`. Hand edits to `status:` are caught; other fields are not.
- **poker-game's test command** runs from two folders (`game/`, `pipeline/`). The template shows one. The pilot config needs both, chained with `&&`.
- **`cost --week` checks two targets**: helper start under 15k and at most one Opus run per item. Desk context and chats per day are not visible to kit.
- **Not run on a real project yet.** Everything is proven on temp repos only.

## Flaws

Found and fixed:
- The 15 review findings above, 13 fixed in code.
- My first queue test checked for text the code never printed. Fixed the test, not the code.
- The first `can-start` let a relaunch through after two fails while proof still allowed a retry. Unified in `budget_spent()`.

Remaining:
- **The lock is advisory.** Git commands run by a builder do not take `build/.lock`. Ship and proof work on captured SHAs to cover this, but a builder writing to `build/` directly is only stopped by the path guard hook in step 4.
- **Hand edits other than status are trusted.** Someone who edits `branch:` to a `bk/` name kit did not make could still point ship at it. The guards check the name and the worktree location, not who made the branch.
- **`kit ship` on a parent stops at the first failing child.** Children shipped before it stay shipped. The parent goes back to `building`. No rollback, by design, but it is a partial ship.
- **`update_local_main` warns and skips** if Mark's checkout is not on `main` or `main` moved. The push already happened; Mark runs `git pull`. Correct but noisy.
- **`sweep` sees remote branches only through local `refs/remotes/`**, which can be stale until `git fetch`. It does not delete anything it cannot see.
- **The decisions file is parsed by line labels.** An answer that contains a line like `## D5 open` would split the entry.
- **Tests run on macOS only.** `fcntl` means no Windows; Linux has it, but the tests were not run there.
- **One reviewer model.** ChatGPT read the code twice. The second pass covered only the rewritten functions, and its 5 fixes (about 40 lines) have not been read by anyone fresh. No second model.
- **kit.py is large for one file** (1,637 lines). Fine for stdlib-only, but step 3 should not grow it much.
