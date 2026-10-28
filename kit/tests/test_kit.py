"""Tests for kit. Each test gets a temp git repo with a bare repo as origin. No network."""
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
KIT = HERE.parent / "kit.py"
REPO = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
import kit  # noqa: E402

PASS_IF_NO_BAD = "python3 -c \\\"import pathlib,sys; sys.exit(1 if pathlib.Path('BAD').exists() else 0)\\\""


def sh(*args, cwd=None, check=True):
    r = subprocess.run(list(args), cwd=cwd, capture_output=True, text=True)
    if check and r.returncode:
        raise AssertionError(f"{args}: {r.stdout}{r.stderr}")
    return r


class Base(unittest.TestCase):
    config_extra = ""
    language = '"Python 3"'
    test_command = f'"{PASS_IF_NO_BAD}"'
    key_paths = "  - src/\n"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kit-test-")).resolve()
        self.remote = self.tmp / "remote.git"
        self.root = self.tmp / "proj"
        sh("git", "init", "-q", "--bare", "-b", "main", str(self.remote))
        sh("git", "init", "-q", "-b", "main", str(self.root))
        for k, v in (("user.name", "t"), ("user.email", "t@t"), ("commit.gpgsign", "false")):
            self.git("config", k, v)
        (self.root / "src").mkdir()
        (self.root / "src" / "a.py").write_text("A = 1\n")
        (self.root / "README.md").write_text("proj\n")
        (self.root / "secret.env").write_text("x\n")
        self.git("add", ".")
        self.git("commit", "-qm", "init")
        self.git("remote", "add", "origin", str(self.remote))
        self.git("push", "-q", "-u", "origin", "main")
        (self.root / "build").mkdir()
        self.write_config()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_config(self, **over):
        vals = {"language": self.language, "test_command": self.test_command}
        vals.update(over)
        text = ""
        for k, v in vals.items():
            if v is not None:
                text += f"{k}: {v}\n"
        if over.get("key_paths", "x") is not None:
            text += "key_paths:\n" + self.key_paths
        text += "protected:\n  - secret.env\ndesign_rules: \"Use the design kit.\"\n"
        text += self.config_extra
        (self.root / "build" / "config.yml").write_text(text)

    def git(self, *args, cwd=None, check=True):
        return sh("git", "-C", str(cwd or self.root), *args, check=check)

    def rgit(self, *args):
        return sh("git", "--git-dir", str(self.remote), *args, check=False)

    def kit(self, *args, input=None, env=None, ok=True):
        e = dict(os.environ)
        e.pop("KIT_ROOT", None)
        e.update(env or {})
        r = subprocess.run([sys.executable, str(KIT), "--root", str(self.root), *args],
                           capture_output=True, text=True, input=input, env=e)
        if ok is True and r.returncode:
            raise AssertionError(f"kit {args} failed {r.returncode}: {r.stdout}{r.stderr}")
        if ok is False and r.returncode == 0:
            raise AssertionError(f"kit {args} should have failed: {r.stdout}")
        return r

    def ctx(self):
        return kit.Ctx(self.root)

    def item(self, iid):
        return kit.load(self.ctx(), iid)

    def new(self, title="thing", size="small", files="src/a.py", done=("tests pass",), extra=()):
        args = ["new", title, "--size", size]
        if files:
            args += ["--files", files]
        for d in done:
            args += ["--done", d]
        return self.kit(*args, *extra).stdout.strip()

    def commit_in(self, wt, name="src/b.py", content="B = 2\n", msg="work"):
        p = Path(wt) / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        self.git("add", ".", cwd=wt)
        self.git("commit", "-qm", msg, cwd=wt)

    def to_building(self, iid):
        self.kit("status", iid, "building")
        return self.item(iid)["worktree"]

    def make_ready(self, title="thing", extra=(), name="src/b.py", content="B = 2\n"):
        iid = self.new(title, extra=extra)
        wt = self.to_building(iid)
        self.commit_in(wt, name, content)
        self.kit("proof", iid)
        self.kit("status", iid, "ready")
        return iid, wt

    def remote_main(self):
        return self.rgit("rev-parse", "main").stdout.strip()


# ---------------------------------------------------------------- parser and templates

class ParserTests(unittest.TestCase):
    def test_config_template_parses(self):
        cfg = kit.parse_yaml((REPO / "templates" / "config.yml").read_text())
        self.assertTrue(cfg["language"])
        self.assertTrue(cfg["test_command"])
        self.assertIsInstance(cfg["key_paths"], list)
        self.assertEqual(cfg["limits"], {"big_running": 2, "mark_owes": 5, "attempts": 2,
                                         "review_lines": 150, "plan_max_items": 8})
        self.assertIn(cfg["ship"], ("push", "pr"))

    def test_item_template_parses_and_round_trips(self):
        it = kit.parse_item((REPO / "templates" / "item.md").read_text())
        for f in kit.FIELDS:
            self.assertIn(f, it.meta, f"template lacks field {f}")
        self.assertEqual(it["status"], "new")
        self.assertEqual(it["roles"], [])
        self.assertEqual(it["attempts"], 0)
        it["id"], it["title"] = "4", 'A "quoted" # title'
        it["files"] = ["a b.py", "c.py"]
        again = kit.parse_item(it.render())
        self.assertEqual(again.meta, it.meta)
        self.assertEqual(again.done_when(), [])

    def test_scalars(self):
        P = kit.parse_scalar
        self.assertEqual(P('""'), "")
        self.assertEqual(P("[]   # x"), [])
        self.assertEqual(P('["a", "b"]'), ["a", "b"])
        self.assertEqual(P("[a, b]"), ["a", "b"])
        self.assertEqual(P("12"), 12)
        self.assertEqual(P("main  # comment"), "main")
        self.assertEqual(P("# only a comment"), "")
        self.assertEqual(P("'x # y'"), "x # y")

    def test_bad_yaml(self):
        with self.assertRaises(kit.KitError):
            kit.parse_yaml("  - orphan\n")
        with self.assertRaises(kit.KitError):
            kit.parse_yaml("nocolon\n")


class HelpTests(unittest.TestCase):
    def test_help_lists_every_command(self):
        out = sh(sys.executable, str(KIT), "--help").stdout
        for c in ["new", "plan", "brief", "map", "status", "can-start", "diff", "proof", "cost",
                  "board", "queue", "ship", "drop", "sweep", "ledger", "note", "inbox"]:
            self.assertRegex(out, rf"\n\s+{c}\s", c)
        out = sh(sys.executable, str(KIT), "ledger", "--help").stdout
        self.assertIn("add", out)
        self.assertIn("answer", out)

    def test_no_config_is_an_error(self):
        d = tempfile.mkdtemp()
        try:
            r = subprocess.run([sys.executable, str(KIT), "--root", d, "board"], capture_output=True, text=True)
            self.assertEqual(r.returncode, 1)
            self.assertIn("config.yml", r.stderr)
        finally:
            shutil.rmtree(d)


# ---------------------------------------------------------------- ids

class IdTests(Base):
    def test_two_writers_never_share_an_id(self):
        n = 12
        procs = [subprocess.Popen([sys.executable, str(KIT), "--root", str(self.root), "new", f"t{i}"],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                 for i in range(n)]
        ids = [p.communicate()[0].strip() for p in procs]
        self.assertTrue(all(p.returncode == 0 for p in procs))
        self.assertEqual(sorted(ids, key=int), [str(i) for i in range(1, n + 1)])
        self.assertEqual(len(list((self.root / "build" / "items").glob("*.md"))), n)

    def test_ids_are_not_reused(self):
        a = self.new()
        (self.root / "build" / "items" / f"{a}.md").unlink()
        self.assertEqual(self.new(), str(int(a) + 1))

    def test_build_is_kept_out_of_git(self):
        self.new()
        self.assertEqual(self.git("status", "--porcelain").stdout.strip(), "")

    def test_new_validates(self):
        self.kit("new", "x", "--size", "huge", ok=False)
        self.kit("new", "x", "--risk", "lasers", ok=False)
        self.kit("new", "x", "--role", "wizard", ok=False)


# ---------------------------------------------------------------- state machine

class StateTests(Base):
    def put(self, status, **meta):
        """Write an item straight to a state, with a matching status.log entry."""
        ctx = self.ctx()
        with ctx.lock():
            n = kit.next_id(ctx)
            m = {"id": str(n), "title": "t", "status": status, "roles": ["coder"], "size": "small",
                 "risk": [], "from": "", "parent": "", "children": [], "files": ["src/a.py"],
                 "no_touch": [], "branch": "", "worktree": "", "session": "", "shipped": "",
                 "attempts": 0, "cost": "", "created": ""}
            m.update(meta)
            it = kit.Item(m, {"Done when": "- ok"})
            kit.save(ctx, it)
            kit.status_log(ctx, it.id, "", status)
        return it.id

    def test_every_disallowed_transition_is_refused(self):
        ctx = self.ctx()
        checked = 0
        for frm in kit.STATES:
            for to in kit.STATES:
                if to in kit.TRANSITIONS[frm]:
                    continue
                iid = self.put(frm)
                with self.assertRaises(kit.KitError) as cm:
                    with ctx.lock():
                        kit.transition(ctx, kit.load(ctx, iid), to)
                self.assertIn("not allowed", str(cm.exception))
                self.assertEqual(self.item(iid)["status"], frm)
                checked += 1
        self.assertGreater(checked, 50)

    def test_shipped_and_dropped_only_through_ship_and_drop(self):
        iid = self.put("ready")
        r = self.kit("status", iid, "shipped", ok=False)
        self.assertIn("kit ship", r.stderr)
        r = self.kit("status", iid, "dropped", ok=False)
        self.assertIn("kit drop", r.stderr)

    def test_unknown_state(self):
        iid = self.new()
        self.kit("status", iid, "done", ok=False)

    def test_hand_edited_status_is_caught(self):
        iid = self.new()
        p = self.root / "build" / "items" / f"{iid}.md"
        p.write_text(p.read_text().replace("status: new", "status: ready"))
        r = self.kit("status", iid, "building", ok=False)
        self.assertIn("edited by hand", r.stderr)
        r = self.kit("can-start", iid, "coder", ok=False)
        self.assertIn("edited by hand", r.stderr)

    def test_waiting_is_for_big_or_risky(self):
        self.kit("status", self.new(), "waiting", ok=False)
        self.kit("status", self.new(extra=["--risk", "auth"]), "waiting")
        self.kit("status", self.new(size="big"), "waiting")

    def test_big_goes_through_planner(self):
        big = self.new(size="big")
        r = self.kit("status", big, "building", ok=False)
        self.assertIn("planner", r.stderr)
        self.kit("status", big, "planning")
        self.kit("status", self.new(), "planning", ok=False)

    def test_building_makes_bk_branch_and_worktree(self):
        iid = self.new()
        wt = self.to_building(iid)
        it = self.item(iid)
        self.assertEqual(it["branch"], f"bk/{iid}")
        self.assertTrue(kit.under(wt, self.root / ".claude" / "worktrees"))
        self.assertTrue(Path(wt).is_dir())
        self.assertIn(f"bk/{iid}", self.git("branch").stdout)
        self.assertEqual(self.git("status", "--porcelain").stdout.strip(), "")

    def test_ready_needs_fresh_passing_proof(self):
        iid = self.new()
        wt = self.to_building(iid)
        self.commit_in(wt)
        r = self.kit("status", iid, "ready", ok=False)
        self.assertIn("no proof", r.stderr)
        self.kit("proof", iid)
        self.commit_in(wt, "src/c.py", "C = 3\n")
        r = self.kit("status", iid, "ready", ok=False)
        self.assertIn("older than the branch head", r.stderr)
        self.kit("proof", iid)
        (Path(wt) / "src" / "d.py").write_text("dirty\n")
        r = self.kit("status", iid, "ready", ok=False)
        self.assertIn("uncommitted", r.stderr)
        (Path(wt) / "src" / "d.py").unlink()
        self.kit("status", iid, "ready")

    def test_risky_needs_review_and_review_needs_a_note(self):
        iid = self.new(extra=["--risk", "auth"])
        wt = self.to_building(iid)
        self.commit_in(wt)
        self.kit("proof", iid)
        r = self.kit("status", iid, "ready", ok=False)
        self.assertIn("needs review", r.stderr)
        self.kit("status", iid, "review")
        self.kit("status", iid, "ready", ok=False)
        self.kit("status", iid, "ready", "--note", "no findings")
        self.assertIn("no findings", self.item(iid).sec("Review"))

    def test_big_diff_needs_review(self):
        self.config_extra = "limits:\n  review_lines: 3\n"
        self.write_config()
        iid = self.new()
        wt = self.to_building(iid)
        self.commit_in(wt, content="".join(f"X{i} = {i}\n" for i in range(10)))
        self.kit("proof", iid)
        r = self.kit("status", iid, "ready", ok=False)
        self.assertIn("diff is 10 lines", r.stderr)

    def test_change_note_sends_ready_back_with_note_and_fresh_attempts(self):
        iid, wt = self.make_ready()
        r = self.kit("status", iid, "building", ok=False)
        self.assertIn("change note", r.stderr)
        self.kit("status", iid, "building", "--note", "make it blue")
        it = self.item(iid)
        self.assertEqual(it["status"], "building")
        self.assertIn("make it blue", it.sec("Brief"))
        self.assertIn("make it blue", self.kit("brief", iid).stdout)
        self.assertEqual(it["worktree"], wt)

    def test_open_question_blocks_big_or_risky_only(self):
        risky = self.new(extra=["--risk", "money"])
        small = self.new()
        d1 = self.kit("ledger", "add", "Which currency?", "--default", "USD", "--item", risky).stdout.strip()
        self.kit("ledger", "add", "Name?", "--default", "foo", "--item", small)
        r = self.kit("status", risky, "building", ok=False)
        self.assertIn(d1, r.stderr)
        self.kit("status", small, "building")
        self.kit("ledger", "answer", d1, "EUR")
        self.kit("status", risky, "building")


# ---------------------------------------------------------------- brief

class BriefTests(Base):
    def test_full_brief(self):
        iid = self.new("Add B", files="src/a.py,src/b.py",
                       extra=["--no-touch", "README.md", "--brief", "Add constant B."])
        out = self.kit("brief", iid).stdout
        for part in ["Language: Python 3", PASS_IF_NO_BAD.replace('\\"', '"'), "Key paths: src/",
                     "secret.env", "build/", "- tests pass", "- src/b.py", "- README.md",
                     "Add constant B.", f"bk/{iid}", "at most 10 lines"]:
            self.assertIn(part, out)

    def test_brief_fails_without_config_facts(self):
        iid = self.new()
        for key, word in (("language", "language"), ("test_command", "test_command")):
            with self.subTest(key=key):
                self.write_config(**{key: None})
                r = self.kit("brief", iid, ok=False)
                self.assertIn(word, r.stderr)
        self.write_config(key_paths=None)
        r = self.kit("brief", iid, ok=False)
        self.assertIn("key_paths", r.stderr)
        self.write_config()
        self.kit("brief", iid)

    def test_brief_fails_without_done_when_or_files(self):
        r = self.kit("brief", self.new(done=()), ok=False)
        self.assertIn("done-when", r.stderr)
        r = self.kit("brief", self.new(files=""), ok=False)
        self.assertIn("item files", r.stderr)
        r = self.kit("status", self.new(files=""), "building", ok=False)
        self.assertIn("item files", r.stderr)

    def test_ui_brief_has_design_rules(self):
        iid = self.new(extra=["--role", "ui-designer"])
        self.assertIn("Design rules: Use the design kit.", self.kit("brief", iid).stdout)
        self.assertNotIn("Design rules", self.kit("brief", self.new()).stdout)

    def test_planner_and_reviewer_briefs(self):
        big = self.new(size="big", files="", done=(), extra=["--from", "make poker faster"])
        out = self.kit("brief", big).stdout
        self.assertIn("title | role | size | risk | files | done-when", out)
        self.assertIn("make poker faster", out)
        self.assertIn("at most 8", out)
        iid = self.new(extra=["--risk", "auth"])
        wt = self.to_building(iid)
        self.commit_in(wt, content="SECRET_B = 2\n")
        self.kit("proof", iid)
        self.kit("status", iid, "review")
        out = self.kit("brief", iid).stdout
        self.assertIn("+SECRET_B = 2", out)
        self.assertIn("security and performance", out)


# ---------------------------------------------------------------- launch gate

class CanStartTests(Base):
    def deny(self, iid, agent, word):
        r = self.kit("can-start", iid, agent, ok=False)
        self.assertEqual(r.returncode, 1)
        self.assertIn("DENY", r.stderr)
        self.assertIn(word, r.stderr)

    def test_ok(self):
        iid = self.new()
        self.to_building(iid)
        r = self.kit("can-start", iid, "coder")
        self.assertIn("ok", r.stdout)

    def test_state_must_fit_agent(self):
        iid = self.new()
        self.deny(iid, "coder", "Builders run on items in building")
        self.deny(iid, "reviewer", "Reviewers run on items in review")
        self.deny(iid, "planner", "planning")
        self.deny(iid, "wizard", "not a buildkit agent")

    def test_agent_must_fit_size_and_role(self):
        iid = self.new()
        self.to_building(iid)
        self.deny(iid, "coder-big", "use coder")
        self.deny(iid, "ui-designer", "no ui-designer role")
        ui = self.new(extra=["--role", "ui-designer"])
        self.to_building(ui)
        self.kit("can-start", ui, "ui-designer")

    def test_reviewer_must_fit_risk(self):
        risky = self.new(extra=["--risk", "secrets"])
        wt = self.to_building(risky)
        self.commit_in(wt)
        self.kit("proof", risky)
        self.kit("status", risky, "review")
        self.deny(risky, "reviewer", "reviewer-risky")
        self.kit("can-start", risky, "reviewer-risky")
        plain = self.new(extra=["--risk", "agent"])
        wt = self.to_building(plain)
        self.commit_in(wt)
        self.kit("proof", plain)
        self.kit("status", plain, "review")
        self.deny(plain, "reviewer-risky", "use reviewer")
        self.kit("can-start", plain, "reviewer")

    def test_attempts_spent(self):
        iid = self.new()
        self.to_building(iid)
        self.kit("proof", iid, "--exit", "1", "--log", "x", ok=None)
        self.kit("can-start", iid, "coder")
        self.kit("proof", iid, "--exit", "1", "--log", "x", ok=None)
        self.kit("can-start", iid, "coder")
        self.assertEqual(self.kit("proof", iid, "--exit", "1", "--log", "x", ok=None).returncode, 3)
        self.deny(iid, "coder", "budget")

    def test_question_added_after_start_still_blocks_risky(self):
        iid = self.new(extra=["--risk", "auth"])
        self.to_building(iid)
        self.kit("can-start", iid, "coder")
        d = self.kit("ledger", "add", "Which provider?", "--default", "x", "--item", iid).stdout.strip()
        self.deny(iid, "coder", d)

    def test_brief_gap_denies(self):
        iid = self.new()
        self.to_building(iid)
        self.write_config(language=None)
        self.deny(iid, "coder", "language")

    def plan_two_big(self):
        par = self.new(size="big", files="", done=())
        self.kit("status", par, "planning")
        kids = self.kit("plan", par, input="A | coder-big | big | none | src/a.py | ok\n"
                                           "B | coder-big | big | none | src/b.py | ok\n").stdout.split()
        return par, kids

    def test_child_waits_for_marks_go(self):
        par, kids = self.plan_two_big()
        r = self.kit("status", kids[0], "building", ok=False)
        self.assertIn("go", r.stderr)
        self.kit("status", par, "building")
        self.kit("status", kids[0], "building")
        self.kit("can-start", kids[0], "coder-big")
        self.deny(kids[0], "coder", "coder-big")
        self.deny(par, "coder-big", "planned parent")

    def test_big_running_cap(self):
        par, kids = self.plan_two_big()
        self.kit("status", par, "building")
        self.kit("status", kids[0], "building")
        self.kit("status", kids[1], "building")
        self.config_extra = "limits:\n  big_running: 1\n"
        self.write_config()
        self.deny(kids[1], "coder-big", "already building")
        par2 = self.new(size="big", files="", done=())
        self.kit("status", par2, "planning")
        k3 = self.kit("plan", par2, input="C | coder-big | big | none | src/c.py | ok\n").stdout.split()[0]
        self.kit("status", par2, "building")
        r = self.kit("status", k3, "building", ok=False)
        self.assertIn("already building", r.stderr)

    def test_mark_owes_blocks_big_and_lists_quick_first(self):
        self.config_extra = "limits:\n  mark_owes: 1\n"
        self.write_config()
        par, kids = self.plan_two_big()
        self.kit("status", par, "building")
        self.kit("status", kids[0], "building")
        self.kit("can-start", kids[0], "coder-big")
        self.make_ready("done one")
        self.kit("ledger", "add", "Colour?", "--default", "red")
        r = self.kit("can-start", kids[0], "coder-big", ok=False)
        self.assertIn("Mark owes 2", r.stderr)
        self.assertLess(r.stderr.index("Colour?"), r.stderr.index("done one"))
        small = self.new()
        self.to_building(small)
        self.kit("can-start", small, "coder")


# ---------------------------------------------------------------- plan

class PlanTests(Base):
    def planning(self):
        par = self.new("Big idea", size="big", files="", done=(), extra=["--from", "idea line"])
        self.kit("status", par, "planning")
        return par

    def test_plan_writes_children_in_order(self):
        par = self.planning()
        reply = ("1. Engine | coder | small | none | src/a.py | engine test passes; lint ok\n"
                 "- Page | ui-designer | small | public | src/page.html | screenshot\n")
        kids = self.kit("plan", par, input=reply).stdout.split()
        self.assertEqual(len(kids), 2)
        p = self.item(par)
        self.assertEqual(p["children"], kids)
        self.assertEqual(p["status"], "waiting")
        a, b = self.item(kids[0]), self.item(kids[1])
        self.assertEqual((a["title"], a["parent"], a["roles"]), ("Engine", par, ["coder"]))
        self.assertEqual(a.done_when(), ["engine test passes", "lint ok"])
        self.assertEqual(b["risk"], ["public"])
        self.assertIn(f"- {par}: Big idea\n    {kids[0]} coder small: Engine", self.kit("queue").stdout)

    def test_plan_cap_and_bad_lines_write_nothing(self):
        par = self.planning()
        nine = "".join(f"T{i} | coder | small | none | f{i} | ok\n" for i in range(9))
        r = self.kit("plan", par, input=nine, ok=False)
        self.assertIn("cap is 8", r.stderr)
        bad = "A | coder | small | none | f | ok\nB | coder | small | none | f\n"
        self.kit("plan", par, input=bad, ok=False)
        self.kit("plan", par, input="A | wizard | small | none | f | ok\n", ok=False)
        self.kit("plan", par, input="A | coder | small | none |  | ok\n", ok=False)
        self.kit("plan", par, input="A | coder-big | small | none | f | ok\n", ok=False)
        self.assertEqual(len(list((self.root / "build" / "items").glob("*.md"))), 1)
        self.assertEqual(self.item(par)["status"], "planning")

    def test_plan_needs_planning(self):
        self.kit("plan", self.new(), input="A | coder | small | none | f | ok\n", ok=False)

    def test_parent_follows_children_and_ships_them_in_order(self):
        par = self.planning()
        kids = self.kit("plan", par, input="A | coder | small | none | src/a.py | ok\n"
                                           "B | coder | small | none | src/b.py | ok\n").stdout.split()
        self.kit("status", par, "building")
        for n, k in enumerate(kids):
            wt = self.to_building(k)
            self.commit_in(wt, f"src/k{n}.py", f"K{n} = {n}\n")
            self.kit("proof", k)
            self.kit("status", k, "ready")
            self.assertEqual(self.item(par)["status"], "ready" if n == 1 else "building")
        self.kit("status", kids[0], "building", "--note", "one more thing")
        self.assertEqual(self.item(par)["status"], "building")
        self.kit("proof", kids[0])
        self.kit("status", kids[0], "ready")
        self.assertEqual(self.item(par)["status"], "ready")
        self.kit("ship", par)
        self.assertEqual(self.item(par)["status"], "shipped")
        log = self.rgit("log", "--format=%s", "main").stdout.splitlines()
        self.assertEqual(log[:2], [f"kit ship {kids[1]}: B", f"kit ship {kids[0]}: A"])


# ---------------------------------------------------------------- proof

class ProofTests(Base):
    def test_attempts_block_twice_then_spent(self):
        iid = self.new()
        wt = self.to_building(iid)
        self.commit_in(wt, "BAD", "x\n")
        codes = [self.kit("proof", iid, ok=None).returncode for _ in range(3)]
        self.assertEqual(codes, [2, 2, 3])
        self.assertEqual(self.item(iid)["attempts"], 3)
        p = kit.last_proof(self.item(iid))
        self.assertEqual(p["exit"], 1)
        self.assertTrue(Path(p["log"]).exists())
        self.kit("status", iid, "failed")
        self.kit("proof", iid, ok=False)

    def test_ui_needs_fresh_screenshot(self):
        iid = self.new(extra=["--role", "ui-designer"])
        wt = self.to_building(iid)
        shot = self.tmp / "shot.png"
        shot.write_text("png")
        old = time.time() - 100
        os.utime(shot, (old, old))
        self.commit_in(wt, "src/page.html", "<p>hi</p>\n")
        self.assertEqual(self.kit("proof", iid, ok=None).returncode, 2)
        r = self.kit("proof", iid, "--screenshot", str(shot), ok=None)
        self.assertIn("older than the last edit", r.stdout)
        os.utime(shot, None)
        self.kit("proof", iid, "--screenshot", str(shot))

    def test_proof_needs_clean_worktree_on_its_branch(self):
        iid = self.new()
        wt = self.to_building(iid)
        self.commit_in(wt)
        (Path(wt) / "src" / "wip.py").write_text("WIP\n")
        r = self.kit("proof", iid, ok=None)
        self.assertEqual(r.returncode, 2)
        self.assertIn("uncommitted", r.stdout)
        (Path(wt) / "src" / "wip.py").unlink()
        self.git("checkout", "-q", "--detach", "HEAD~1", cwd=wt)
        r = self.kit("proof", iid, "--exit", "0", "--log", "x", ok=None)
        self.assertIn("not on bk/", r.stdout)
        self.git("checkout", "-q", f"bk/{iid}", cwd=wt)
        self.kit("proof", iid)

    def test_empty_test_command_never_passes(self):
        iid = self.new()
        self.to_building(iid)
        self.write_config(test_command='""')
        self.kit("proof", iid, "--exit", "0", "--log", "x", ok=False)

    def test_proof_only_while_building(self):
        self.kit("proof", self.new(), ok=False)


# ---------------------------------------------------------------- ship

class ShipTests(Base):
    def test_ship_push(self):
        iid, wt = self.make_ready()
        self.git("push", "-q", "origin", f"bk/{iid}")
        self.kit("ship", iid)
        it = self.item(iid)
        self.assertEqual(it["status"], "shipped")
        self.assertEqual(it["shipped"], self.remote_main())
        self.assertEqual(self.git("rev-parse", "main").stdout.strip(), it["shipped"])
        self.assertTrue((self.root / "src" / "b.py").exists())
        self.assertFalse(Path(wt).exists())
        self.assertNotIn(f"bk/{iid}", self.git("branch", "--list").stdout)
        self.assertEqual(self.rgit("branch", "--list", f"bk/{iid}").stdout.strip(), "")
        self.assertEqual(len(kit.worktrees(self.ctx())), 1)

    def test_failing_tests_after_merge_push_nothing(self):
        iid = self.new()
        wt = self.to_building(iid)
        self.commit_in(wt, "BAD", "x\n")
        self.kit("proof", iid, "--exit", "0", "--log", "claimed")
        self.kit("status", iid, "ready")
        before, local = self.remote_main(), self.git("rev-parse", "main").stdout.strip()
        r = self.kit("ship", iid, ok=False)
        self.assertIn("tests failed", r.stderr)
        self.assertEqual(self.remote_main(), before)
        self.assertEqual(self.git("rev-parse", "main").stdout.strip(), local)
        self.assertEqual(self.item(iid)["status"], "failed")
        self.assertTrue(Path(wt).exists())
        self.assertIn(f"bk/{iid}", self.git("branch").stdout)
        self.assertEqual(len(kit.worktrees(self.ctx())), 2)

    def test_merge_conflict_pushes_nothing(self):
        iid, wt = self.make_ready(name="src/a.py", content="A = 2\n")
        (self.root / "src" / "a.py").write_text("A = 3\n")
        self.git("commit", "-qam", "mark edits a")
        self.git("push", "-q", "origin", "main")
        before = self.remote_main()
        r = self.kit("ship", iid, ok=False)
        self.assertIn("merge", r.stderr)
        self.assertEqual(self.remote_main(), before)
        self.assertEqual(self.item(iid)["status"], "failed")

    def test_only_ready_ships(self):
        iid = self.new()
        self.to_building(iid)
        r = self.kit("ship", iid, ok=False)
        self.assertIn("Only ready", r.stderr)

    def edit_field(self, iid, field, value):
        p = self.root / "build" / "items" / f"{iid}.md"
        lines = [f'{field}: "{value}"' if ln.startswith(f"{field}:") else ln
                 for ln in p.read_text().splitlines()]
        p.write_text("\n".join(lines) + "\n")

    def test_ship_and_drop_refuse_branches_kit_did_not_make(self):
        iid, wt = self.make_ready()
        self.git("branch", "feature/mine")
        self.edit_field(iid, "branch", "feature/mine")
        before = self.remote_main()
        self.assertIn("Not touching", self.kit("ship", iid, ok=False).stderr)
        self.assertIn("Not touching", self.kit("drop", iid, ok=False).stderr)
        self.assertIn("feature/mine", self.git("branch").stdout)
        self.assertEqual(self.remote_main(), before)

    def test_ship_and_drop_refuse_worktrees_outside_claude_worktrees(self):
        iid, wt = self.make_ready()
        mine = self.tmp / "marks-tree"
        self.git("worktree", "add", "-q", "-b", "marks", str(mine))
        self.edit_field(iid, "worktree", str(mine))
        self.assertIn("Not touching", self.kit("ship", iid, ok=False).stderr)
        self.assertIn("Not touching", self.kit("drop", iid, ok=False).stderr)
        self.assertTrue(mine.exists())

    def test_ship_refuses_dirty_worktree(self):
        iid, wt = self.make_ready()
        (Path(wt) / "new.txt").write_text("forgot to commit\n")
        r = self.kit("ship", iid, ok=False)
        self.assertIn("uncommitted", r.stderr)
        self.assertEqual(self.item(iid)["status"], "ready")

    def test_ship_refuses_commits_after_ready(self):
        iid, wt = self.make_ready()
        self.commit_in(wt, "src/late.py", "LATE = 1\n")
        before = self.remote_main()
        r = self.kit("ship", iid, ok=False)
        self.assertIn("older than the branch head", r.stderr)
        self.assertEqual(self.remote_main(), before)
        self.assertEqual(self.item(iid)["status"], "ready")

    def test_second_ship_of_same_item_is_refused(self):
        iid, wt = self.make_ready()
        busy = self.tmp / "busy"
        busy.mkdir()
        (self.root / "build" / "shipping").mkdir()
        (self.root / "build" / "shipping" / iid).write_text(str(busy) + "\n")
        r = self.kit("ship", iid, ok=False)
        self.assertIn("already shipping", r.stderr)
        self.assertEqual(self.item(iid)["status"], "ready")

    def test_tests_that_change_tracked_files_stop_the_ship(self):
        iid, wt = self.make_ready()
        self.write_config(test_command='"echo changed >> README.md"')
        before = self.remote_main()
        r = self.kit("ship", iid, ok=False)
        self.assertIn("tracked files", r.stderr)
        self.assertEqual(self.remote_main(), before)

    def test_ship_refuses_empty_test_command(self):
        iid, wt = self.make_ready()
        self.write_config(test_command='""')
        before = self.remote_main()
        r = self.kit("ship", iid, ok=False)
        self.assertIn("test_command is empty", r.stderr)
        self.assertEqual(self.remote_main(), before)

    def test_ship_keeps_remote_branch_with_unmerged_commits(self):
        iid, wt = self.make_ready()
        self.git("push", "-q", "origin", f"bk/{iid}")
        self.git("checkout", "-q", "-b", "extra", f"bk/{iid}")
        (self.root / "src" / "x.py").write_text("X = 1\n")
        self.git("add", ".")
        self.git("commit", "-qm", "someone else")
        self.git("push", "-q", "origin", f"extra:refs/heads/bk/{iid}")
        self.git("checkout", "-q", "main")
        r = self.kit("ship", iid)
        self.assertIn("Kept", r.stderr)
        self.assertEqual(self.rgit("rev-parse", f"bk/{iid}").stdout.strip(),
                         self.git("rev-parse", "extra").stdout.strip())

    def test_local_main_is_not_rewound(self):
        ctx = self.ctx()
        base = self.git("rev-parse", "main").stdout.strip()
        self.git("checkout", "-q", "-b", "elsewhere")
        (self.root / "m.txt").write_text("mark\n")
        self.git("add", ".")
        self.git("commit", "-qm", "mark on main")
        marks = self.git("rev-parse", "HEAD").stdout.strip()
        self.git("update-ref", "refs/heads/main", marks)
        kit.update_local_main(ctx, base, base)
        self.assertEqual(self.git("rev-parse", "main").stdout.strip(), marks)

    def test_existing_unrecorded_bk_branch_is_not_adopted(self):
        self.git("branch", "bk/1")
        iid = self.new()
        self.assertEqual(iid, "1")
        r = self.kit("status", iid, "building", ok=False)
        self.assertIn("Not adopting", r.stderr)
        self.assertEqual(self.item(iid)["status"], "new")

    def test_proof_records_the_tested_revision(self):
        iid = self.new()
        wt = self.to_building(iid)
        self.commit_in(wt)
        self.write_config(test_command='"git commit -q --allow-empty -m during"')
        r = self.kit("proof", iid)
        self.assertIn("pass", r.stdout)
        self.assertIn("branch moved", self.item(iid).sec("Proof"))
        r = self.kit("status", iid, "ready", ok=False)
        self.assertIn("older than the branch head", r.stderr)

    def test_ship_pr_mode(self):
        self.config_extra = "ship: pr\n"
        self.write_config()
        fake = self.tmp / "gh"
        fake.write_text('#!/bin/sh\necho "$@" > "$(dirname "$0")/gh-args"\necho https://github.com/x/y/pull/7\n')
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        iid, wt = self.make_ready("pr item")
        before = self.remote_main()
        self.kit("ship", iid, env={"KIT_GH": str(fake)})
        it = self.item(iid)
        self.assertEqual(it["status"], "shipped")
        self.assertEqual(it["shipped"], "pr https://github.com/x/y/pull/7")
        self.assertEqual(self.remote_main(), before)
        self.assertTrue(self.rgit("rev-parse", f"bk/{iid}").stdout.strip())
        args = (self.tmp / "gh-args").read_text()
        self.assertIn(f"--head bk/{iid}", args)
        self.assertIn("--base main", args)
        self.assertFalse(Path(wt).exists())
        r = self.kit("sweep")
        self.assertNotIn(f"bk/{iid}", r.stdout)


# ---------------------------------------------------------------- drop and sweep

class DropSweepTests(Base):
    def test_drop(self):
        iid = self.new(extra=["--from", "raw inbox line", "--session", "abc-123"])
        wt = self.to_building(iid)
        self.commit_in(wt)
        (Path(wt) / "junk.txt").write_text("uncommitted\n")
        self.git("push", "-q", "origin", f"bk/{iid}")
        self.git("branch", "keep-me")
        r = self.kit("drop", iid)
        self.assertIn("abc-123", r.stdout)
        it = self.item(iid)
        self.assertEqual(it["status"], "dropped")
        self.assertEqual(it["from"], "raw inbox line")
        self.assertFalse(Path(wt).exists())
        self.assertNotIn(f"bk/{iid}", self.git("branch").stdout)
        self.assertEqual(self.rgit("branch", "--list", f"bk/{iid}").stdout.strip(), "")
        self.assertIn("keep-me", self.git("branch").stdout)
        self.kit("drop", iid, ok=False)

    def test_drop_parent_drops_children(self):
        par = self.new(size="big", files="", done=())
        self.kit("status", par, "planning")
        kids = self.kit("plan", par, input="A | coder | small | none | f | ok\n").stdout.split()
        self.kit("drop", par)
        self.assertEqual(self.item(kids[0])["status"], "dropped")
        self.assertEqual(self.item(par)["status"], "dropped")

    def test_sweep_never_prunes_or_takes_foreign_temp_worktrees(self):
        kt = self.tmp / "kt"
        kt.mkdir()
        env = {"TMPDIR": str(kt)}
        mine = self.tmp / "marks-missing"
        self.git("worktree", "add", "-q", "-b", "marks", str(mine))
        shutil.rmtree(mine)
        lookalike = self.tmp / "kit-ship-mark"
        self.git("worktree", "add", "-q", "--detach", str(lookalike))
        live = kt / "kit-ship-5-abc"
        self.git("worktree", "add", "-q", "--detach", str(live))
        (self.root / "build" / "shipping").mkdir()
        (self.root / "build" / "shipping" / "5").write_text(str(live) + "\n")
        leftover = kt / "kit-ship-6-def"
        self.git("worktree", "add", "-q", "--detach", str(leftover))
        unlogged = kt / "kit-ship-7-ghi"
        self.git("worktree", "add", "-q", "--detach", str(unlogged))
        (self.root / "build" / "ship-temps.log").write_text(f"{live}\n{leftover}\n")
        r = self.kit("sweep", env=env)
        self.assertIn(str(leftover), r.stdout)
        self.assertNotIn(str(lookalike), r.stdout)
        self.assertNotIn(str(live), r.stdout)
        self.assertNotIn(str(unlogged), r.stdout)
        self.kit("sweep", "--yes", env=env)
        listed = self.git("worktree", "list", "--porcelain").stdout
        self.assertIn(str(mine), listed)
        self.assertIn(str(lookalike), listed)
        self.assertIn(str(live), listed)
        self.assertNotIn(str(leftover), listed)

    def test_sweep_lists_then_deletes_only_orphan_bk(self):
        live = self.new()
        live_wt = self.to_building(live)
        gone = self.new()
        gone_wt = self.to_building(gone)
        self.git("push", "-q", "origin", f"bk/{gone}")
        self.git("fetch", "-q", "origin")
        (self.root / "build" / "items" / f"{gone}.md").unlink()
        self.git("branch", "bk/99")
        mine = self.tmp / "marks-tree"
        self.git("worktree", "add", "-q", "-b", "feature", str(mine))
        r = self.kit("sweep")
        self.assertIn(f"bk/{gone}", r.stdout)
        self.assertIn("bk/99", r.stdout)
        self.assertIn(f"origin/bk/{gone}", r.stdout)
        self.assertNotIn(f"bk/{live}\n", r.stdout)
        self.assertIn("Nothing deleted", r.stdout)
        self.assertTrue(Path(gone_wt).exists())
        self.assertIn("bk/99", self.git("branch").stdout)
        self.kit("sweep", "--yes")
        self.assertFalse(Path(gone_wt).exists())
        branches = self.git("branch").stdout
        self.assertNotIn("bk/99", branches)
        self.assertNotIn(f"bk/{gone}", branches)
        self.assertIn(f"bk/{live}", branches)
        self.assertIn("feature", branches)
        self.assertTrue(mine.exists())
        self.assertTrue(Path(live_wt).exists())
        self.assertEqual(self.rgit("branch", "--list", f"bk/{gone}").stdout.strip(), "")
        self.assertIn("nothing to sweep", self.kit("sweep").stdout)


# ---------------------------------------------------------------- cost

def write_transcript(path, start=5449, model="claude-sonnet-5-5"):
    rows = [
        {"type": "user", "timestamp": "2026-10-27T10:00:00Z", "message": {}},
        {"type": "assistant", "uuid": "u1", "timestamp": "2026-10-27T10:00:01Z",
         "message": {"id": "m1", "model": model,
                     "usage": {"input_tokens": 3, "cache_creation_input_tokens": start - 3,
                               "cache_read_input_tokens": 0, "output_tokens": 50},
                     "content": [{"type": "tool_use"}]}},
        {"type": "assistant", "uuid": "u2", "timestamp": "2026-10-27T10:00:02Z",
         "message": {"id": "m1", "model": model,
                     "usage": {"input_tokens": 3, "cache_creation_input_tokens": start - 3,
                               "cache_read_input_tokens": 0, "output_tokens": 50},
                     "content": [{"type": "tool_use"}]}},
        {"type": "assistant", "uuid": "u3", "timestamp": "2026-10-27T10:00:05Z",
         "message": {"id": "m2", "model": model,
                     "usage": {"input_tokens": 10, "cache_creation_input_tokens": 100,
                               "cache_read_input_tokens": start, "output_tokens": 20},
                     "content": [{"type": "text"}]}},
    ]
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("\n".join(json.dumps(r) for r in rows) + "\n")


class CostTests(Base):
    def test_tally_dedups_by_message_id(self):
        f = self.tmp / "t.jsonl"
        write_transcript(f)
        t = kit.tally(f)
        self.assertEqual(t["requests"], 2)
        self.assertEqual(t["tool_calls"], 1)
        self.assertEqual(t["first_request_context"], 5449)
        self.assertEqual(t["input_tokens"], 13)
        self.assertEqual(t["output_tokens"], 70)
        self.assertEqual(t["all_tokens"], 13 + 70 + 5449 + 5446 + 100)

    def test_tally_matches_experiments_tally(self):
        home = self.tmp / "home"
        f = home / ".claude" / "projects" / "p" / "sid1" / "subagents" / "agent-x.jsonl"
        write_transcript(f)
        r = subprocess.run([sys.executable, str(REPO / "experiments" / "tally.py"), "sid1"],
                           capture_output=True, text=True, env={**os.environ, "HOME": str(home)})
        ref = json.loads(r.stdout)
        mine = kit.tally(f)
        for k in ("requests", "input_tokens", "output_tokens", "cache_read_input_tokens",
                  "cache_creation_input_tokens", "all_tokens", "first_request_context"):
            self.assertEqual(mine[k], ref[k], k)
        r = self.kit("cost", self.new(), "--session", "sid1", env={"HOME": str(home)})
        self.assertIn("runs 1", r.stdout)

    def test_cost_records_and_week_checks_targets(self):
        iid = self.new()
        f = self.tmp / "a.jsonl"
        write_transcript(f)
        r = self.kit("cost", iid, "--transcript", str(f), "--agent", "coder")
        self.assertIn("runs 1", r.stdout)
        self.assertIn("runs 1", self.item(iid)["cost"])
        self.assertIn("all targets met", self.kit("cost", "--week").stdout)
        self.kit("cost", iid, "--transcript", str(f), "--transcript", str(f))
        self.assertIn("runs 1", self.item(iid)["cost"])
        big = self.tmp / "b.jsonl"
        write_transcript(big, start=46700, model="claude-opus-5-5")
        big2 = self.tmp / "c.jsonl"
        write_transcript(big2, start=8000, model="claude-opus-5-5")
        self.kit("cost", iid, "--transcript", str(big), "--transcript", str(big2))
        r = self.kit("cost", "--week", ok=False)
        self.assertIn("start 46700 > 15000", r.stdout)
        self.assertIn("2 Opus runs", r.stdout)
        self.assertIn("opus 2", self.item(iid)["cost"])


# ---------------------------------------------------------------- board, queue, ledger, map, diff

class ViewTests(Base):
    def test_board_is_rebuilt_on_every_change(self):
        a = self.new("first")
        b = self.new("second", extra=["--risk", "auth"])
        board = (self.root / "build" / "board.md").read_text()
        self.assertIn(f"- {a} [new] small coder: first", board)
        self.assertIn(f"- {b} [new] small coder: second (risk auth)", board)
        self.kit("status", a, "building")
        self.assertIn(f"- {a} [building]", (self.root / "build" / "board.md").read_text())
        self.assertEqual(self.kit("board").stdout.count("\n- "), 2)

    def test_queue(self):
        iid, _ = self.make_ready("ready one")
        self.kit("ledger", "add", "Colour?", "--default", "red", "--item", iid)
        out = self.kit("queue").stdout
        self.assertIn(f"- {iid} ready: ready one", out)
        self.assertIn("proof: exit 0", out)
        self.assertIn("risk: none", out)
        self.assertIn("Colour? Default: red", out)
        self.assertIn("Mark owes: 2", out)

    def test_ledger_answers_are_verbatim(self):
        d = self.kit("ledger", "add", "Ship  it\nnow?", "--default", "no").stdout.strip()
        self.assertEqual(d, "D1")
        self.assertEqual(self.kit("ledger", "add", "Two?", "--default", "x").stdout.strip(), "D2")
        answer = "Yes, but:\n  - only on Friday\n  - not *before* 5 pm"
        self.kit("ledger", "answer", "D1", input=answer)
        decs = kit.read_decisions(self.ctx())
        self.assertEqual(decs[0]["answer"], answer)
        self.assertEqual(decs[0]["state"], "answered")
        self.assertEqual(decs[0]["question"], "Ship it now?")
        self.assertEqual(decs[1]["state"], "open")
        self.kit("ledger", "answer", "D1", "again", ok=False)
        self.kit("ledger", "answer", "D9", "x", ok=False)
        self.kit("ledger", "add", "Q", "--default", "x", "--item", "42", ok=False)

    def test_map(self):
        (self.root / "zz.txt").write_text("z\n")
        self.git("add", "zz.txt")
        self.git("commit", "-qm", "zz")
        out = self.kit("map").stdout.splitlines()
        self.assertEqual(out[1], "src/a.py")
        self.assertIn("zz.txt", out)
        self.assertNotIn("secret.env", out)
        self.assertIn("more", self.kit("map", "--max", "1").stdout)

    def test_diff(self):
        iid = self.new()
        wt = self.to_building(iid)
        self.commit_in(wt, content="B = 42\n")
        self.assertIn("+B = 42", self.kit("diff", iid).stdout)
        self.assertIn("1 file changed", self.kit("diff", iid, "--stat").stdout)
        self.kit("diff", self.new(), ok=False)


class NoteInboxTests(Base):
    def test_note_adds_a_work_log_line_and_keeps_state(self):
        iid = self.new()
        self.kit("note", iid, "found the\nbug in a.py", "--who", "coder")
        it = self.item(iid)
        self.assertEqual(it["status"], "new")
        self.assertIn("coder: found the bug in a.py", it.sec("Work log"))

    def test_note_refuses_empty_unknown_and_closed_items(self):
        iid = self.new()
        self.kit("note", iid, "   ", ok=False)
        self.kit("note", "999", "x", ok=False)
        self.kit("drop", iid)
        self.kit("note", iid, "late", ok=False)

    def test_note_cannot_change_status_text(self):
        iid = self.new()
        self.kit("note", iid, "status: ready", "--who", "coder")
        self.assertEqual(self.item(iid)["status"], "new")

    def test_inbox_appends_one_line_per_idea(self):
        self.kit("inbox", "first idea")
        self.kit("inbox", "second\nidea with newline")
        text = (self.root / "build" / "inbox.md").read_text()
        self.assertEqual(text, "first idea\nsecond idea with newline\n")
        self.kit("inbox", "  ", ok=False)

    def test_inbox_keeps_a_line_Mark_wrote_without_newline(self):
        (self.root / "build" / "inbox.md").write_text("phone idea")
        self.kit("inbox", "next")
        self.assertEqual((self.root / "build" / "inbox.md").read_text(), "phone idea\nnext\n")

    def test_brief_tells_the_builder_how_to_record_details(self):
        iid = self.new()
        self.to_building(iid)
        out = self.kit("brief", iid).stdout
        self.assertIn(f"note {iid}", out)
        self.assertIn(str(KIT), out)


if __name__ == "__main__":
    unittest.main()
