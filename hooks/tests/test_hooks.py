"""Tests for the buildkit hook scripts. Each test makes a temp git repo (same fixture as the kit
tests), pipes a hook input into the script and checks the exit code and output."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOOKS = HERE.parent
REPO = HOOKS.parent
sys.path.insert(0, str(REPO / "kit" / "tests"))
sys.path.insert(0, str(REPO / "kit"))
from test_kit import Base  # noqa: E402

SESSION = "11111111-2222-3333-4444-555555555555"


def run_hook(script, data, cwd, env=None, raw=None):
    e = dict(os.environ)
    e.pop("KIT_ROOT", None)
    e.pop("CLAUDE_PROJECT_DIR", None)
    e.update(env or {})
    r = subprocess.run([sys.executable, str(HOOKS / script)], input=raw if raw is not None else json.dumps(data),
                       capture_output=True, text=True, cwd=str(cwd), env=e)
    return r.returncode, r.stdout, r.stderr


def decision(out):
    """('deny', reason) from a PreToolUse output, or (None, '') for no output."""
    if not out.strip():
        return None, ""
    h = json.loads(out)["hookSpecificOutput"]
    return h["permissionDecision"], h["permissionDecisionReason"]


def allowed(out):
    """The prompt a PreToolUse allow puts in updatedInput, or None."""
    if not out.strip():
        return None
    h = json.loads(out)["hookSpecificOutput"]
    return h["updatedInput"]["prompt"] if h["permissionDecision"] == "allow" else None


class HookBase(Base):
    def setUp(self):
        super().setUp()
        self.claude = self.tmp / "claude"
        self.parent_transcript = self.claude / f"{SESSION}.jsonl"
        self.parent_transcript.parent.mkdir(parents=True)
        self.parent_transcript.write_text("")

    def building(self, size="small", extra=()):
        iid = self.new(size=size, extra=extra)
        if size == "big":
            self.kit("status", iid, "planning")
            self.fail("use building helper for small only")
        self.to_building(iid)
        return iid

    def prompt(self, iid, tail=""):
        return self.kit("brief", iid).stdout.rstrip("\n") + tail

    def agent_input(self, agent, prompt, **extra):
        ti = {"subagent_type": f"buildkit:{agent}", "prompt": prompt, "description": "x"}
        ti.update(extra)
        return {"hook_event_name": "PreToolUse", "tool_name": "Agent", "tool_input": ti,
                "cwd": str(self.root), "session_id": SESSION, "transcript_path": str(self.parent_transcript)}

    def sub_transcript(self, agent_id, brief):
        p = self.claude / SESSION / "subagents" / f"agent-{agent_id}.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": brief}}) + "\n")
        return p

    def stop_input(self, agent, agent_id, brief, active=False):
        p = self.claude / SESSION / "subagents" / f"agent-{agent_id}.jsonl"
        if not p.exists():
            self.sub_transcript(agent_id, brief)
        return {"hook_event_name": "SubagentStop", "agent_type": f"buildkit:{agent}", "agent_id": agent_id,
                "stop_hook_active": active, "agent_transcript_path": str(p), "cwd": str(self.root),
                "session_id": SESSION, "transcript_path": str(self.parent_transcript),
                "last_assistant_message": "done"}

    def edit_input(self, path, agent="coder", agent_id="aaa", tool="Write", agent_type=True):
        d = {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": {"file_path": str(path)},
             "cwd": str(self.root), "session_id": SESSION, "transcript_path": str(self.parent_transcript)}
        if agent_type:
            d.update({"agent_type": f"buildkit:{agent}", "agent_id": agent_id})
        return d


class LaunchGateTests(HookBase):
    def gate(self, data, cwd=None, **kw):
        return run_hook("launch_gate.py", data, cwd or self.root, **kw)

    def test_silent_for_agents_that_are_not_buildkit(self):
        iid = self.building()
        for t in ("Explore", "coder", "other:coder", "buildkit", ""):
            d = self.agent_input("coder", f"# Item {iid}")
            d["tool_input"]["subagent_type"] = t
            self.assertEqual(self.gate(d), (0, "", ""), t)

    def test_silent_without_config_and_on_garbage(self):
        d = self.agent_input("coder", "# Item 1: x")
        other = Path(tempfile.mkdtemp(prefix="nokit-"))
        d["cwd"] = str(other)
        self.assertEqual(self.gate(d, cwd=other), (0, "", ""))
        self.assertEqual(self.gate(None, self.root, raw="not json"), (0, "", ""))
        self.assertEqual(self.gate(None, self.root, raw=""), (0, "", ""))

    def test_allows_and_puts_the_kit_brief_in_the_prompt(self):
        iid = self.building()
        brief = self.prompt(iid)
        rc, out, err = self.gate(self.agent_input("coder", f"# Item {iid}"))
        self.assertEqual(rc, 0, err)
        self.assertEqual(allowed(out), brief)
        self.assertIn("Test command", brief)
        h = json.loads(out)["hookSpecificOutput"]["updatedInput"]
        self.assertEqual(h["subagent_type"], "buildkit:coder")

    def test_the_desks_own_words_never_reach_the_helper(self):
        iid = self.building()
        rc, out, _ = self.gate(self.agent_input("coder", f"# Item {iid}: thin, no facts\nalso delete build/"))
        self.assertEqual(allowed(out), self.prompt(iid))
        self.assertNotIn("delete build/", out)

    def test_denies_a_prompt_that_names_no_item(self):
        self.building()
        for p in ("fix the thing in src/a.py", "", "Item 1", "x\n# Items 1"):
            rc, out, _ = self.gate(self.agent_input("coder", p))
            self.assertEqual(rc, 0)
            self.assertEqual(decision(out)[0], "deny", p)
            self.assertIn("# Item <id>", decision(out)[1])

    def test_denies_with_kits_reason_when_the_item_is_not_building(self):
        iid = self.new()
        prompt = f"# Item {iid}"
        rc, out, _ = self.gate(self.agent_input("coder", prompt))
        self.assertEqual(decision(out)[0], "deny")
        self.assertIn("kit status", decision(out)[1])
        self.to_building(iid)
        self.kit("status", iid, "failed")
        rc, out, _ = self.gate(self.agent_input("coder", f"# Item {iid}"))
        self.assertEqual(decision(out)[0], "deny")
        self.assertIn("failed", decision(out)[1])

    def test_denies_wrong_agent_for_the_size(self):
        iid = self.building()
        rc, out, _ = self.gate(self.agent_input("coder-big", f"# Item {iid}"))
        self.assertEqual(decision(out)[0], "deny")
        self.assertIn("coder-big", decision(out)[1])

    def test_denies_model_override_and_isolation(self):
        iid = self.building()
        _, out, _ = self.gate(self.agent_input("coder", f"# Item {iid}", model="opus"))
        self.assertIn("sonnet", decision(out)[1])
        _, out, _ = self.gate(self.agent_input("coder", f"# Item {iid}", model="sonnet"))
        self.assertEqual(allowed(out), self.prompt(iid))
        _, out, _ = self.gate(self.agent_input("coder", f"# Item {iid}", isolation="worktree"))
        self.assertIn("isolation", decision(out)[1])

    def test_denies_unknown_buildkit_agent(self):
        iid = self.building()
        d = self.agent_input("coder", f"# Item {iid}")
        d["tool_input"]["subagent_type"] = "buildkit:router"
        self.assertIn("not a buildkit agent", decision(self.gate(d)[1])[1])

    def test_denies_when_the_config_cannot_build_a_brief(self):
        iid = self.building()
        self.write_config(test_command=None)
        _, out, _ = self.gate(self.agent_input("coder", f"# Item {iid}"))
        self.assertEqual(decision(out)[0], "deny")
        self.assertIn("test_command", decision(out)[1])

    def test_planner_with_the_map_appended_is_allowed(self):
        iid = self.new("big one", size="big")
        self.kit("status", iid, "planning")
        rc, out, err = self.gate(self.agent_input("planner", f"# Item {iid}"))
        self.assertEqual(rc, 0, err)
        p = allowed(out)
        self.assertTrue(p.startswith(self.prompt(iid)))
        self.assertIn("## File map (kit map)", p)
        self.assertIn("src/a.py", p)

    def test_denies_a_planner_when_kit_map_fails(self):
        iid = self.new("big one", size="big")
        self.kit("status", iid, "planning")
        wrapper = self.tmp / "kit_no_map.py"
        wrapper.write_text(f"import sys, runpy\nif 'map' in sys.argv: sys.exit('map broke')\n"
                           f"sys.argv[0] = {str(REPO / 'kit' / 'kit.py')!r}\n"
                           f"runpy.run_path(sys.argv[0], run_name='__main__')\n")
        _, out, _ = self.gate(self.agent_input("planner", f"# Item {iid}"), env={"BUILDKIT_KIT": str(wrapper)})
        self.assertEqual(decision(out)[0], "deny")

    def test_a_broken_log_path_never_breaks_a_hook_for_other_agents(self):
        d = self.agent_input("coder", "# Item 1")
        d["tool_input"]["subagent_type"] = "general-purpose"
        env = {"BUILDKIT_HOOK_LOG": "/nonexistent-dir/x.log"}
        self.assertEqual(self.gate(d, env=env), (0, "", ""))

    def test_runs_from_a_subfolder_of_the_project(self):
        iid = self.building()
        sub = self.root / "src"
        d = self.agent_input("coder", f"# Item {iid}")
        d["cwd"] = str(sub)
        self.assertEqual(allowed(self.gate(d, cwd=sub)[1]), self.prompt(iid))

    def test_fails_closed_when_kit_is_broken(self):
        iid = self.building()
        bad = self.tmp / "broken_kit.py"
        bad.write_text("raise SystemExit('broken')\n")
        _, out, _ = self.gate(self.agent_input("coder", f"# Item {iid}"), env={"BUILDKIT_KIT": str(bad)})
        self.assertEqual(decision(out)[0], "deny")


class PathGuardTests(HookBase):
    def guard(self, data):
        return run_hook("path_guard.py", data, self.root)

    def setUp(self):
        super().setUp()
        self.iid = self.building(extra=["--no-touch", "src/frozen.py"])
        self.wt = Path(self.item(self.iid)["worktree"])
        self.sub_transcript("aaa", self.prompt(self.iid))

    def test_silent_for_the_parent_session_and_other_agents(self):
        self.assertEqual(self.guard(self.edit_input(self.root / "build" / "x", agent_type=False)), (0, "", ""))
        d = self.edit_input(self.root / "build" / "x")
        d["agent_type"] = "Explore"
        self.assertEqual(self.guard(d), (0, "", ""))
        d["agent_type"] = "coder"
        self.assertEqual(self.guard(d), (0, "", ""))

    def test_silent_without_config(self):
        other = Path(tempfile.mkdtemp(prefix="nokit-"))
        d = self.edit_input(other / "x")
        d["cwd"] = str(other)
        self.assertEqual(run_hook("path_guard.py", d, other), (0, "", ""))

    def test_allows_a_file_in_its_worktree(self):
        self.assertEqual(self.guard(self.edit_input(self.wt / "src" / "new.py")), (0, "", ""))
        self.assertEqual(self.guard(self.edit_input(self.wt / "src" / "a.py", tool="Edit")), (0, "", ""))

    def test_denies_build_in_the_project_and_in_the_worktree(self):
        for p in (self.root / "build" / "items" / f"{self.iid}.md", self.root / "build", self.wt / "build" / "x"):
            self.assertEqual(decision(self.guard(self.edit_input(p))[1])[0], "deny", str(p))

    def test_denies_the_main_checkout_and_outside_paths(self):
        for p in (self.root / "src" / "a.py", Path("/tmp/elsewhere.txt"), Path.home() / ".ssh" / "x"):
            self.assertEqual(decision(self.guard(self.edit_input(p))[1])[0], "deny", str(p))

    def test_denies_protected_paths_and_no_touch(self):
        _, out, _ = self.guard(self.edit_input(self.wt / "secret.env"))
        self.assertIn("protected", decision(out)[1])
        _, out, _ = self.guard(self.edit_input(self.wt / "src" / "frozen.py"))
        self.assertEqual(decision(out)[0], "deny")

    def test_denies_git_metadata(self):
        self.assertEqual(decision(self.guard(self.edit_input(self.wt / ".git"))[1])[0], "deny")

    def test_denies_traversal_and_symlink_escapes(self):
        sneaky = self.wt / "src" / ".." / ".." / ".." / ".." / "build" / "x"
        self.assertEqual(decision(self.guard(self.edit_input(sneaky))[1])[0], "deny")
        link = self.wt / "link"
        link.symlink_to(self.root / "build")
        self.assertEqual(decision(self.guard(self.edit_input(link / "x"))[1])[0], "deny")

    def test_relative_paths_resolve_against_the_hook_cwd(self):
        d = self.edit_input("src/x.py")
        d["cwd"] = str(self.wt)
        self.assertEqual(run_hook("path_guard.py", d, self.wt), (0, "", ""))
        d["cwd"] = str(self.root)
        self.assertEqual(decision(run_hook("path_guard.py", d, self.root)[1])[0], "deny")

    def test_read_only_buildkit_agents_may_not_edit(self):
        for a in ("planner", "reviewer", "reviewer-risky"):
            _, out, _ = self.guard(self.edit_input(self.wt / "src" / "x.py", agent=a))
            self.assertIn("read-only", decision(out)[1], a)

    def test_denies_another_items_worktree_when_the_transcript_names_the_item(self):
        other = self.new("other")
        other_wt = Path(self.to_building(other))
        self.assertEqual(self.guard(self.edit_input(self.wt / "src" / "x.py")), (0, "", ""))
        _, out, _ = self.guard(self.edit_input(other_wt / "src" / "x.py"))
        self.assertEqual(decision(out)[0], "deny")
        self.assertIn(f"bk-{self.iid}", decision(out)[1])

    def test_denies_when_it_cannot_tell_which_item_the_helper_works_on(self):
        _, out, _ = self.guard(self.edit_input(self.wt / "src" / "x.py", agent_id="nobody"))
        self.assertEqual(decision(out)[0], "deny")
        self.assertIn("Cannot tell which item", decision(out)[1])
        self.sub_transcript("bbb", "no header in this prompt")
        _, out, _ = self.guard(self.edit_input(self.wt / "src" / "x.py", agent_id="bbb"))
        self.assertEqual(decision(out)[0], "deny")

    def test_case_aliases_are_the_same_path(self):
        for rel in ("BUILD/config.yml", "Build/x", ".GIT", ".Git/config", "SECRET.ENV", "SRC/Frozen.py"):
            _, out, _ = self.guard(self.edit_input(self.wt / rel))
            self.assertEqual(decision(out)[0], "deny", rel)
        _, out, _ = self.guard(self.edit_input(self.root / "BUILD" / "items" / "1.md"))
        self.assertEqual(decision(out)[0], "deny")

    def test_a_symlinked_folder_does_not_hide_a_protected_name(self):
        (self.wt / "data").mkdir()
        (self.wt / "build").symlink_to(self.wt / "data")
        _, out, _ = self.guard(self.edit_input(self.wt / "build" / "config.yml"))
        self.assertEqual(decision(out)[0], "deny")
        self.assertIn("protected", decision(out)[1])

    def test_glob_entries_cover_the_folders_they_match(self):
        self.write_config(key_paths=None)
        text = (self.root / "build" / "config.yml").read_text().replace(
            "protected:\n  - secret.env\n", "protected:\n  - secret.env\n  - packages/*/secrets\n  - '**/.env'\n")
        (self.root / "build" / "config.yml").write_text(text)
        for rel in ("packages/api/secrets/key.txt", "packages/api/secrets", ".env", "a/b/.env"):
            _, out, _ = self.guard(self.edit_input(self.wt / rel))
            self.assertEqual(decision(out)[0], "deny", rel)
        self.assertEqual(self.guard(self.edit_input(self.wt / "packages" / "api" / "main.py")), (0, "", ""))

    def test_pretool_guards_block_when_they_fail(self):
        for x in json.loads((HOOKS / "hooks.json").read_text())["hooks"]["PreToolUse"]:
            self.assertEqual(x["hooks"][0].get("onFailure"), "block")

    def test_notebook_and_multiedit_are_in_the_hook_matcher(self):
        h = json.loads((HOOKS / "hooks.json").read_text())["hooks"]["PreToolUse"]
        m = next(x["matcher"] for x in h if "path_guard" in x["hooks"][0]["command"])
        for tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
            self.assertIn(tool, m.split("|"))

    def test_fails_closed_on_a_broken_config(self):
        (self.root / "build" / "config.yml").write_text("ship: nonsense\nlanguage: x\n")
        _, out, _ = self.guard(self.edit_input(self.wt / "src" / "x.py"))
        self.assertEqual(decision(out)[0], "deny")


class TestGateTests(HookBase):
    def stop(self, agent, iid, agent_id="aaa", active=False):
        brief = self.prompt(iid)
        return run_hook("test_gate.py", self.stop_input(agent, agent_id, brief, active), self.root)

    def proof_lines(self, iid):
        return [l for l in self.item(iid).sec("Proof").splitlines() if l.strip()]

    def test_silent_for_other_agents_and_reviewers_and_no_config(self):
        iid = self.building()
        wt = self.item(iid)["worktree"]
        self.commit_in(wt)
        d = self.stop_input("coder", "aaa", self.prompt(iid))
        for t in ("Explore", "coder", "buildkit:reviewer", "buildkit:planner"):
            d["agent_type"] = t
            self.assertEqual(run_hook("test_gate.py", d, self.root), (0, "", ""), t)
        self.assertEqual(self.proof_lines(iid), [])
        other = Path(tempfile.mkdtemp(prefix="nokit-"))
        d["agent_type"], d["cwd"] = "buildkit:coder", str(other)
        self.assertEqual(run_hook("test_gate.py", d, other), (0, "", ""))

    def test_pass_records_proof_and_exits_zero(self):
        iid = self.building()
        self.commit_in(self.item(iid)["worktree"])
        rc, out, err = self.stop("coder", iid)
        self.assertEqual(rc, 0, err)
        self.assertRegex(self.proof_lines(iid)[0], r"exit 0 \| head [0-9a-f]+")
        self.kit("status", iid, "ready")

    def test_fail_blocks_with_the_test_tail_then_sets_failed_on_the_third(self):
        iid = self.building()
        wt = self.item(iid)["worktree"]
        self.commit_in(wt, "BAD", "x\n")
        rc, out, err = self.stop("coder", iid)
        self.assertEqual(rc, 2)
        self.assertIn(f"item {iid} exit 1", err)
        self.assertIn("Fix the failure", err)
        self.assertEqual(self.item(iid)["attempts"], 1)
        rc, _, err = self.stop("coder", iid, active=True)
        self.assertEqual(rc, 2, err)
        self.assertEqual(self.item(iid)["attempts"], 2)
        rc, out, err = self.stop("coder", iid, active=True)
        self.assertEqual(rc, 0, err)
        self.assertIn("set to failed", out)
        self.assertEqual(self.item(iid)["status"], "failed")
        self.assertEqual(len(self.proof_lines(iid)), 3)

    def test_fix_after_a_block_passes(self):
        iid = self.building()
        wt = self.item(iid)["worktree"]
        self.commit_in(wt, "BAD", "x\n")
        self.assertEqual(self.stop("coder", iid)[0], 2)
        self.git("rm", "-q", "BAD", cwd=wt)
        self.git("commit", "-qm", "fix", cwd=wt)
        self.assertEqual(self.stop("coder", iid, active=True)[0], 0)
        self.assertEqual(self.item(iid)["status"], "building")
        self.kit("status", iid, "ready")

    def test_uncommitted_work_blocks(self):
        iid = self.building()
        (Path(self.item(iid)["worktree"]) / "loose.py").write_text("x\n")
        rc, _, err = self.stop("coder", iid)
        self.assertEqual(rc, 2)
        self.assertIn("uncommitted", err)

    def test_ui_item_needs_a_fresh_screenshot(self):
        iid = self.new("page", extra=["--role", "ui-designer"])
        wt = self.to_building(iid)
        self.commit_in(wt)
        rc, _, err = self.stop("ui-designer", iid)
        self.assertEqual(rc, 2)
        self.assertIn("screenshot", err)
        shot = self.root / "build" / "shots" / f"{iid}.png"
        shot.parent.mkdir(parents=True)
        shot.write_bytes(b"png")
        future = time.time() + 5
        os.utime(shot, (future, future))
        rc, _, err = self.stop("ui-designer", iid)
        self.assertEqual(rc, 0, err)

    def test_no_item_header_in_the_transcript_writes_nothing(self):
        iid = self.building()
        d = self.stop_input("coder", "bbb", "no header here")
        rc, out, err = run_hook("test_gate.py", d, self.root)
        self.assertEqual((rc, out), (0, ""))
        self.assertIn("no '# Item", err)
        self.assertEqual(self.proof_lines(iid), [])

    def test_item_that_left_building_does_not_loop_the_helper(self):
        iid = self.building()
        self.commit_in(self.item(iid)["worktree"])
        self.kit("proof", iid)
        self.kit("status", iid, "ready")
        rc, out, err = self.stop("coder", iid)
        self.assertEqual(rc, 0)
        self.assertIn("could not run", err)
        self.assertIn("test-gate: test gate: kit proof", self.item(iid).sec("Work log"))


class CostNoteTests(HookBase):
    def write_run(self, brief, agent_id="aaa", model="claude-sonnet-5-5"):
        p = self.sub_transcript(agent_id, brief)
        rec = {"type": "assistant", "timestamp": "2026-10-27T10:00:00Z",
               "message": {"id": "m1", "model": model, "role": "assistant",
                           "content": [{"type": "tool_use", "name": "Read"}],
                           "usage": {"input_tokens": 3, "cache_read_input_tokens": 100,
                                     "cache_creation_input_tokens": 50, "output_tokens": 20}}}
        with open(p, "a") as f:
            f.write(json.dumps(rec) + "\n")
        return p

    def stop(self, iid, agent="coder", agent_id="aaa", last=""):
        brief = self.prompt(iid)
        self.write_run(brief, agent_id)
        d = self.stop_input(agent, agent_id, brief)
        d["last_assistant_message"] = last
        return run_hook("cost_note.py", d, self.root, env={"BUILDKIT_COST_QUIET": "0.05", "BUILDKIT_COST_LIMIT": "1"})

    def test_records_whole_run_tokens_in_the_item(self):
        iid = self.building()
        rc, out, err = self.stop(iid)
        self.assertEqual((rc, out), (0, ""), err)
        cost = self.item(iid)["cost"]
        self.assertIn("173", cost)
        self.assertIn("start 153", cost)
        self.assertIn("cost agent-aaa.jsonl: 173 tokens", self.item(iid).sec("Work log"))
        row = json.loads((self.root / "build" / "cost.jsonl").read_text().splitlines()[0])
        self.assertEqual(row["agent"], "coder")

    def test_a_helper_sent_back_by_the_test_gate_is_one_row(self):
        iid = self.building()
        for _ in range(2):
            self.assertEqual(self.stop(iid)[0], 0)
        rows = (self.root / "build" / "cost.jsonl").read_text().strip().splitlines()
        self.assertEqual(len(rows), 1)

    def test_reviewers_and_planners_are_recorded_too(self):
        iid = self.building()
        self.assertEqual(self.stop(iid, agent="reviewer", agent_id="bbb")[0], 0)
        self.assertEqual(self.stop(iid, agent="planner", agent_id="ccc")[0], 0)
        rows = [json.loads(l)["agent"] for l in (self.root / "build" / "cost.jsonl").read_text().splitlines()]
        self.assertEqual(sorted(rows), ["planner", "reviewer"])

    def test_waits_for_the_final_message_and_notes_when_it_never_arrives(self):
        iid = self.building()
        rc, out, err = self.stop(iid, last="the final summary the transcript never got")
        self.assertEqual(rc, 0, err)
        self.assertIn("may be missing its last request", self.item(iid).sec("Work log"))

    def test_no_note_when_the_final_message_is_in_the_transcript(self):
        iid = self.building()
        p = self.write_run(self.prompt(iid), "ddd")
        with open(p, "a") as f:
            f.write(json.dumps({"type": "assistant", "message": {"id": "m2", "role": "assistant",
                                "content": [{"type": "text", "text": "All done, 3 lines changed"}]}}) + "\n")
        d = self.stop_input("coder", "ddd", self.prompt(iid))
        d["last_assistant_message"] = "All done, 3 lines changed"
        rc, _, err = run_hook("cost_note.py", d, self.root, env={"BUILDKIT_COST_QUIET": "0.05", "BUILDKIT_COST_LIMIT": "1"})
        self.assertEqual(rc, 0, err)
        self.assertNotIn("may be missing", self.item(iid).sec("Work log"))

    def test_silent_for_other_agents_and_no_config(self):
        iid = self.building()
        brief = self.prompt(iid)
        d = self.stop_input("coder", "aaa", brief)
        d["agent_type"] = "Explore"
        self.assertEqual(run_hook("cost_note.py", d, self.root), (0, "", ""))
        other = Path(tempfile.mkdtemp(prefix="nokit-"))
        d = self.stop_input("coder", "aaa", brief)
        d["cwd"] = str(other)
        self.assertEqual(run_hook("cost_note.py", d, other), (0, "", ""))

    def test_missing_transcript_is_reported_not_silent(self):
        iid = self.building()
        d = self.stop_input("coder", "aaa", self.prompt(iid))
        d["agent_transcript_path"] = str(self.claude / "gone.jsonl")
        rc, out, err = run_hook("cost_note.py", d, self.root)
        self.assertEqual(rc, 1)
        self.assertIn("no item id or transcript", err)


import time  # noqa: E402

if __name__ == "__main__":
    unittest.main()
