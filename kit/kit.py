#!/usr/bin/env python3
"""kit: the buildkit state tool. One script, stdlib only.

Works on a project's build/ folder (see SPEC.md, "Files in each project").
Every state change goes through transition(). Every write holds build/.lock.
"""
import argparse
import contextlib
import datetime
import fcntl
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# ---------------------------------------------------------------- constants

STATES = ["new", "planning", "waiting", "building", "failed", "review",
          "ready", "shipped", "dropped"]
TERMINAL = {"shipped", "dropped"}
TRANSITIONS = {
    "new": {"planning", "waiting", "building", "dropped"},
    "planning": {"waiting", "dropped"},
    "waiting": {"planning", "building", "dropped"},
    "building": {"failed", "review", "ready", "dropped"},
    "failed": {"building", "dropped"},
    "review": {"ready", "building", "failed", "dropped"},
    "ready": {"building", "failed", "shipped", "dropped"},
    "shipped": set(),
    "dropped": set(),
}
SIZES = ["quick", "small", "big"]
RISKS = ["auth", "send", "public", "delete", "money", "secrets", "agent"]
RISKY = {"auth", "money", "send", "delete", "public", "secrets"}
BUILDERS = ["coder", "coder-big", "ui-designer"]
REVIEWERS = ["reviewer", "reviewer-risky"]
AGENTS = ["planner"] + BUILDERS + REVIEWERS
FIELDS = ["id", "title", "status", "roles", "size", "risk", "from", "parent",
          "children", "files", "no_touch", "branch", "worktree", "session",
          "shipped", "attempts", "cost", "created"]
BARE = {"status", "size"}
SECTIONS = ["Done when", "Brief", "Work log", "Proof", "Review", "For Mark", "Flaws"]
DEFAULTS = {
    "language": "", "test_command": "", "key_paths": [], "protected": [],
    "design_rules": "", "main_branch": "main", "remote": "origin",
    "ship": "push", "gh": "gh", "test_timeout": 1800,
    "limits": {"big_running": 2, "mark_owes": 5, "attempts": 2,
               "review_lines": 150, "plan_max_items": 8},
}
WORKTREES = ".claude/worktrees"
KIT_PATH = os.path.abspath(__file__)
NOTE_MAX = 2000
START_TARGET = 15000  # SPEC usage target: helper start cost under 15k


class KitError(Exception):
    pass


class Deny(Exception):
    pass


def now():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


# ---------------------------------------------------------------- tiny YAML subset
# Supports: `key: scalar`, `key: [a, b]`, `key: "str"`, ints, trailing `# comment`,
# a block list (`key:` then `  - item`) and a one-level mapping (`key:` then `  sub: v`).

def parse_scalar(s):
    s = s.strip()
    if not s or s.startswith("#"):
        return ""
    if s[0] in '["':
        try:
            v, end = json.JSONDecoder().raw_decode(s)
            rest = s[end:].strip()
            if not rest or rest.startswith("#"):
                return v
        except ValueError:
            pass
        if s[0] == "[" and "]" in s:
            inner = s[1:s.index("]")]
            return [x.strip().strip("'\"") for x in inner.split(",") if x.strip()]
    if s[0] == "'" and s.find("'", 1) > 0:
        return s[1:s.find("'", 1)]
    i = s.find(" #")
    if i >= 0:
        s = s[:i].strip()
    if re.fullmatch(r"-?\d+", s):
        return int(s)
    if s in ("true", "false"):
        return s == "true"
    return s


def parse_yaml(text, where="yaml"):
    data, cur = {}, None
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.rstrip()
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if not line[0].isspace():
            k, sep, v = s.partition(":")
            if not sep:
                raise KitError(f"{where} line {n}: expected 'key: value'")
            k = k.strip()
            if v.strip() == "" or v.strip().startswith("#"):
                data[k], cur = None, k
            else:
                data[k], cur = parse_scalar(v), None
            continue
        if cur is None:
            raise KitError(f"{where} line {n}: indented line with no parent key")
        if s.startswith("- ") or s == "-":
            if data[cur] is None:
                data[cur] = []
            if not isinstance(data[cur], list):
                raise KitError(f"{where} line {n}: mixed list and mapping")
            data[cur].append(parse_scalar(s[1:]))
        else:
            k, sep, v = s.partition(":")
            if not sep:
                raise KitError(f"{where} line {n}: expected 'key: value'")
            if data[cur] is None:
                data[cur] = {}
            if not isinstance(data[cur], dict):
                raise KitError(f"{where} line {n}: mixed list and mapping")
            data[cur][k.strip()] = parse_scalar(v)
    return {k: ("" if v is None else v) for k, v in data.items()}


def fmt_value(key, v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, list):
        return json.dumps([str(x) for x in v], ensure_ascii=False)
    v = "" if v is None else str(v)
    if key in BARE and re.fullmatch(r"[a-z-]+", v):
        return v
    return json.dumps(v, ensure_ascii=False)


# ---------------------------------------------------------------- context

class Ctx:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.build = self.root / "build"
        cfg_path = self.build / "config.yml"
        if not cfg_path.exists():
            raise KitError(f"no {cfg_path}. Copy templates/config.yml there first.")
        raw = parse_yaml(cfg_path.read_text(), "config.yml")
        cfg = json.loads(json.dumps(DEFAULTS))
        for k, v in raw.items():
            if k == "limits" and isinstance(v, dict):
                cfg["limits"].update(v)
            else:
                cfg[k] = v
        for k in ("key_paths", "protected"):
            if isinstance(cfg[k], str):
                cfg[k] = [cfg[k]] if cfg[k] else []
        if cfg["ship"] not in ("push", "pr"):
            raise KitError("config ship must be 'push' or 'pr'")
        self.cfg = cfg
        self.lim = cfg["limits"]
        self.items = self.build / "items"
        self._lock_fd = None
        self._lock_depth = 0

    @contextlib.contextmanager
    def lock(self, timeout=60):
        if self._lock_depth:
            self._lock_depth += 1
            try:
                yield
            finally:
                self._lock_depth -= 1
            return
        self.build.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.build / ".lock", os.O_CREAT | os.O_RDWR, 0o644)
        end = time.time() + timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.time() > end:
                    os.close(fd)
                    raise KitError("build/.lock is held by another kit run")
                time.sleep(0.05)
        self._lock_fd, self._lock_depth = fd, 1
        try:
            yield
        finally:
            self._lock_depth = 0
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
            self._lock_fd = None

    def git(self, *args, cwd=None, check=True):
        r = subprocess.run(["git", "-C", str(cwd or self.root), *args],
                           capture_output=True, text=True)
        if check and r.returncode:
            raise KitError(f"git {' '.join(args)}: {(r.stderr or r.stdout).strip()}")
        return r

    def wt_dir(self):
        return self.root / WORKTREES


def find_root(arg):
    if arg:
        return arg
    if os.environ.get("KIT_ROOT"):
        return os.environ["KIT_ROOT"]
    p = Path.cwd().resolve()
    for d in [p, *p.parents]:
        if (d / "build" / "config.yml").exists():
            return d
    raise KitError("no build/config.yml here or above. Use --root.")


def write_atomic(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(tmp, path)


def ensure_excluded(ctx):
    """Keep build/ and .claude/worktrees/ out of git in this clone only."""
    r = ctx.git("rev-parse", "--git-common-dir", check=False)
    if r.returncode:
        return
    gd = Path(r.stdout.strip())
    if not gd.is_absolute():
        gd = ctx.root / gd
    ex = gd / "info" / "exclude"
    have = ex.read_text().splitlines() if ex.exists() else []
    add = [p for p in ("/build/", "/.claude/worktrees/") if p not in have]
    if add:
        ex.parent.mkdir(parents=True, exist_ok=True)
        with open(ex, "a") as f:
            f.write(("\n" if have and have[-1] else "") + "\n".join(add) + "\n")


# ---------------------------------------------------------------- items

class Item:
    def __init__(self, meta, sections):
        self.meta, self.sections = meta, sections

    def __getitem__(self, k):
        return self.meta.get(k, "")

    def __setitem__(self, k, v):
        self.meta[k] = v

    @property
    def id(self):
        return str(self.meta["id"])

    def sec(self, name):
        return self.sections.get(name, "").strip()

    def set_sec(self, name, text):
        self.sections[name] = text.strip()

    def add_line(self, name, line):
        cur = self.sec(name)
        self.set_sec(name, (cur + "\n" if cur else "") + line)

    def done_when(self):
        out = []
        for ln in self.sec("Done when").splitlines():
            ln = ln.strip()
            if ln.startswith("- "):
                ln = ln[2:].strip()
            elif ln == "-":
                ln = ""
            if ln:
                out.append(ln)
        return out

    def render(self):
        lines = ["---"] + [f"{k}: {fmt_value(k, self.meta.get(k, 0 if k == 'attempts' else ''))}"
                           for k in FIELDS] + ["---", ""]
        names = SECTIONS + [s for s in self.sections if s not in SECTIONS]
        for s in names:
            lines += [f"## {s}", self.sec(s), ""] if self.sec(s) else [f"## {s}", ""]
        return "\n".join(lines).rstrip() + "\n"


def parse_item(text, where="item"):
    m = re.match(r"---\n(.*?)\n---\n?(.*)", text, re.S)
    if not m:
        raise KitError(f"{where}: no frontmatter")
    meta = parse_yaml(m.group(1), where)
    for k in ("roles", "risk", "children", "files", "no_touch"):
        v = meta.get(k, [])
        meta[k] = v if isinstance(v, list) else ([v] if v else [])
    meta["attempts"] = int(meta.get("attempts") or 0)
    if meta.get("id") != "":
        meta["id"] = str(meta.get("id"))
    sections, cur, buf = {}, None, []
    for ln in m.group(2).splitlines():
        h = re.match(r"## (.+)$", ln)
        if h:
            if cur:
                sections[cur] = "\n".join(buf).strip()
            cur, buf = h.group(1).strip(), []
        elif cur:
            buf.append(ln)
    if cur:
        sections[cur] = "\n".join(buf).strip()
    return Item(meta, sections)


def item_path(ctx, iid):
    if not re.fullmatch(r"\d+", str(iid)):
        raise KitError(f"bad item id '{iid}'")
    return ctx.items / f"{int(iid)}.md"


def load(ctx, iid):
    p = item_path(ctx, iid)
    if not p.exists():
        raise KitError(f"no item {iid}")
    it = parse_item(p.read_text(), p.name)
    if it.id != str(int(iid)):
        raise KitError(f"{p.name}: id field says {it.id}")
    return it


def save(ctx, it):
    write_atomic(item_path(ctx, it.id), it.render())


def all_items(ctx):
    if not ctx.items.exists():
        return []
    out = [parse_item(p.read_text(), p.name) for p in ctx.items.glob("*.md")]
    return sorted(out, key=lambda i: int(i.id))


def next_id(ctx, counter=".next_id", existing=()):
    """Hand out the next number. Caller holds the lock."""
    f = ctx.build / counter
    n = int(f.read_text().strip() or 1) if f.exists() else 1
    n = max([n] + [int(x) + 1 for x in existing])
    write_atomic(f, f"{n + 1}\n")
    return n


def log_work(it, who, what):
    it.add_line("Work log", f"- {now()} {who}: {what}")


# ---------------------------------------------------------------- status log
# Every transition kit makes is appended to build/status.log. A status that does not
# match the last logged one was edited by hand, and kit refuses to act on it.

def status_log(ctx, iid, frm, to):
    with open(ctx.build / "status.log", "a") as f:
        f.write(json.dumps({"t": now(), "id": str(iid), "from": frm, "to": to}) + "\n")


def last_logged(ctx, iid):
    p = ctx.build / "status.log"
    last = None
    if p.exists():
        for ln in p.read_text().splitlines():
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            if r.get("id") == str(iid):
                last = r.get("to")
    return last


def check_untouched(ctx, it):
    last = last_logged(ctx, it.id)
    if last is None:
        raise KitError(f"item {it.id} has no entry in build/status.log. kit did not make it, "
                       "or the log was lost.")
    if last != it["status"]:
        raise KitError(f"item {it.id} says status '{it['status']}' but kit last set "
                       f"'{last}'. It was edited by hand. Put it back to '{last}'.")


# ---------------------------------------------------------------- decisions ledger

DEC_RE = re.compile(r"^## (D\d+) (open|answered)\s*$", re.M)


def read_decisions(ctx):
    p = ctx.build / "decisions.md"
    if not p.exists():
        return []
    text = p.read_text()
    marks = list(DEC_RE.finditer(text))
    out = []
    for i, m in enumerate(marks):
        body = text[m.end():marks[i + 1].start() if i + 1 < len(marks) else len(text)]
        d = {"id": m.group(1), "state": m.group(2), "item": "", "question": "",
             "default": "", "answer": ""}
        for key in ("Item", "Question", "Default"):
            mm = re.search(rf"^{key}: ?(.*)$", body, re.M)
            if mm:
                d[key.lower()] = mm.group(1).strip()
        mm = re.search(r"^Answer:[ ]?(.*)\Z", body, re.M | re.S)
        if mm:
            d["answer"] = mm.group(1).rstrip("\n")
        out.append(d)
    return out


def write_decisions(ctx, decs):
    parts = ["# Decisions", "", "Questions with defaults. Answers are verbatim. Ids come from kit.", ""]
    for d in decs:
        parts += [f"## {d['id']} {d['state']}", f"Item: {d['item']}",
                  f"Question: {d['question']}", f"Default: {d['default']}",
                  f"Answer:{(' ' + d['answer']) if d['answer'] else ''}", ""]
    write_atomic(ctx.build / "decisions.md", "\n".join(parts))


def open_decisions(ctx, iid=None):
    return [d for d in read_decisions(ctx) if d["state"] == "open"
            and (iid is None or d["item"] == str(iid))]


# ---------------------------------------------------------------- git helpers

def branch_of(it):
    return f"bk/{it.id}"


def branch_head(ctx, branch):
    r = ctx.git("rev-parse", "--verify", "-q", f"refs/heads/{branch}", check=False)
    return r.stdout.strip() if r.returncode == 0 else ""


def worktrees(ctx):
    """[(path, branch or '', detached bool)] from git worktree list."""
    r = ctx.git("worktree", "list", "--porcelain")
    out, cur = [], {}
    for ln in r.stdout.splitlines() + [""]:
        if not ln:
            if cur:
                out.append((cur.get("worktree", ""), cur.get("branch", "").replace("refs/heads/", ""),
                            "detached" in cur))
            cur = {}
            continue
        k, _, v = ln.partition(" ")
        cur[k] = v if v else True
    return out


def wt_registered(ctx, path, branch):
    rp = os.path.realpath(path)
    return any(os.path.realpath(p) == rp and b == branch for p, b, _ in worktrees(ctx))


def under(path, parent):
    rp, pp = os.path.realpath(path), os.path.realpath(parent)
    return rp.startswith(pp + os.sep)


def wt_dirty(ctx, path):
    return ctx.git("status", "--porcelain", cwd=path).stdout.strip()


def diff_lines(ctx, it):
    if not branch_head(ctx, branch_of(it)):
        return 0
    r = ctx.git("diff", "--numstat", f"{ctx.cfg['main_branch']}...{branch_of(it)}")
    n = 0
    for ln in r.stdout.splitlines():
        a, d, _ = (ln.split("\t") + ["", "", ""])[:3]
        n += (int(a) if a.isdigit() else 0) + (int(d) if d.isdigit() else 0)
    return n


def guard_owned(ctx, it):
    """ship and drop only touch the bk/<id> branch and worktree the item records."""
    br, wt = it["branch"], it["worktree"]
    if br and br != branch_of(it):
        raise KitError(f"item {it.id} records branch '{br}', not {branch_of(it)}. Not touching it.")
    if wt:
        if not under(wt, ctx.wt_dir()):
            raise KitError(f"item {it.id} worktree '{wt}' is not under {WORKTREES}. Not touching it.")
        if os.path.exists(wt) and not wt_registered(ctx, wt, branch_of(it)):
            raise KitError(f"worktree '{wt}' is not a git worktree on {branch_of(it)}. Not touching it.")


def make_worktree(ctx, it):
    ensure_excluded(ctx)
    br = branch_of(it)
    path = ctx.wt_dir() / f"bk-{it.id}"
    if it["worktree"] and os.path.exists(it["worktree"]):
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if branch_head(ctx, br):
        if it["branch"] != br:
            raise KitError(f"branch {br} already exists and item {it.id} did not make it. "
                           "Not adopting it. Rename or remove it by hand.")
        ctx.git("worktree", "add", str(path), br)
    else:
        ctx.git("worktree", "add", "-b", br, str(path), ctx.cfg["main_branch"])
    it["branch"], it["worktree"] = br, str(path)


# ---------------------------------------------------------------- proof

def last_proof(it):
    """Parse the newest proof line: '- exit N | head SHA | log PATH | TIME[ | shot PATH]'."""
    for ln in reversed(it.sec("Proof").splitlines()):
        m = re.match(r"- exit (-?\d+) \| head (\S*) \| log (.*?) \| (\S+)", ln)
        if m:
            return {"exit": int(m.group(1)), "head": m.group(2), "log": m.group(3), "time": m.group(4)}
    return None


def proof_ok(ctx, it):
    """Error text if the item has no passing proof for its current branch head."""
    p = last_proof(it)
    head = branch_head(ctx, branch_of(it))
    if not p:
        return "no proof yet. The test gate writes it (kit proof)."
    if p["exit"] != 0:
        return f"last proof failed (exit {p['exit']})."
    if not head or p["head"] != head:
        return "proof is older than the branch head. Run the tests again."
    if it["worktree"] and os.path.exists(it["worktree"]) and wt_dirty(ctx, it["worktree"]):
        return "worktree has uncommitted changes. Commit them first."
    return ""


# ---------------------------------------------------------------- brief

def config_gaps(ctx):
    gaps = []
    if not str(ctx.cfg.get("language") or "").strip():
        gaps.append("config language")
    if not str(ctx.cfg.get("test_command") or "").strip():
        gaps.append("config test_command")
    if not ctx.cfg.get("key_paths"):
        gaps.append("config key_paths")
    return gaps


def project_block(ctx, it):
    c = ctx.cfg
    lines = ["## Project", f"Language: {c['language']}",
             f"Test command (run from the repo root): {c['test_command']}",
             "Key paths: " + ", ".join(c["key_paths"]),
             "Protected, never edit: " + ", ".join(["build/"] + list(c["protected"]))]
    if "ui-designer" in it["roles"] and c.get("design_rules"):
        lines.append(f"Design rules: {c['design_rules']}")
    return lines


def build_brief(ctx, it):
    """The full prompt for a helper. Raises KitError naming every missing part."""
    gaps = config_gaps(ctx)
    status = it["status"]
    head = [f"# Item {it.id}: {it['title']}",
            f"Size: {it['size']}. Roles: {', '.join(it['roles']) or 'none'}. "
            f"Risk: {', '.join(it['risk']) or 'none'}."]
    if status in ("new", "planning") and it["size"] == "big" and not it["parent"]:
        if gaps:
            raise KitError("brief incomplete: missing " + ", ".join(gaps))
        n = ctx.lim["plan_max_items"]
        return "\n".join(head + [""] + project_block(ctx, it) + [
            "", "## Idea", it.sec("Brief") or it["from"] or it["title"], "",
            "## Your job",
            f"Split this into at most {n} child items, ordered by dependency.",
            "Run `kit map` output is below if given. Read only what you need.",
            "Reply with one line per child and nothing else, in this exact format:",
            "title | role | size | risk | files | done-when",
            f"role: one of {', '.join(BUILDERS)}. size: quick, small or big. "
            f"risk: none or comma tags from {', '.join(RISKS)}.",
            "files: comma separated paths to read or change. done-when: checks joined by ';'."])
    if not it.done_when():
        gaps.append("item done-when")
    if not it["files"]:
        gaps.append("item files")
    if gaps:
        raise KitError("brief incomplete: missing " + ", ".join(gaps))
    no_touch = list(dict.fromkeys(["build/"] + list(ctx.cfg["protected"]) + list(it["no_touch"])))
    out = head + [""] + project_block(ctx, it) + ["", "## Done when"]
    out += [f"- {d}" for d in it.done_when()]
    out += ["", "## Files to read"] + [f"- {f}" for f in it["files"]]
    out += ["", "## Do not touch"] + [f"- {f}" for f in no_touch]
    out += ["", "## Task", it.sec("Brief") or it["from"] or it["title"]]
    if status == "review":
        diff = ctx.git("diff", f"{ctx.cfg['main_branch']}...{branch_of(it)}").stdout
        out += ["", "## Your job", "Review the diff below for security and performance only.",
                "Return at most 10 lines: findings, each with file and line, or 'no findings'.",
                "", "## Diff", "```diff", diff.rstrip(), "```"]
    else:
        wt = it["worktree"] or f"{WORKTREES}/bk-{it.id} (made by kit status building)"
        out += ["", "## Rules", f"Work only in {wt}, on branch {branch_of(it)}.",
                "Commit your work on that branch. Run the test command before you stop.",
                "Return at most 10 lines. Put details in the item, not in your reply: "
                f"python3 {KIT_PATH} --root {ctx.root} note {it.id} \"<text>\"."]
    return "\n".join(out)


# ---------------------------------------------------------------- owes

def owes(ctx, items=None):
    """What Mark owes: open decisions, plans waiting for go, ready and failed items.
    Returns a list of (kind, size, text), quick ones first."""
    items = items if items is not None else all_items(ctx)
    by = {i.id: i for i in items}
    out = [("decision", "quick", f"{d['id']}: {d['question']} (default: {d['default']})")
           for d in open_decisions(ctx)]
    for i in items:
        if i["status"] == "waiting" and i["children"]:
            out.append(("plan", i["size"], f"plan {i.id}: {i['title']}"))
        elif i["status"] in ("ready", "failed"):
            par = by.get(i["parent"])
            if par is not None and par["status"] == "ready":
                continue
            out.append((i["status"], i["size"], f"{i['status']} {i.id}: {i['title']}"))
    order = {"quick": 0, "small": 1, "big": 2}
    return sorted(out, key=lambda x: order.get(x[1], 3))


# ---------------------------------------------------------------- transitions

def budget_spent(ctx, it):
    """attempts counts test failures. The gate blocks (retries) at most limit times,
    so the budget is spent once failures exceed the limit."""
    return it["attempts"] > ctx.lim["attempts"]


def big_building(items, exclude=None):
    return [i for i in items if i["status"] == "building" and i["size"] == "big"
            and not i["children"] and i.id != exclude]


def guard(ctx, it, to, note, internal):
    frm = it["status"]
    if to not in STATES:
        raise KitError(f"unknown state '{to}'")
    if to not in TRANSITIONS[frm]:
        allowed = ", ".join(sorted(TRANSITIONS[frm])) or "nothing (final)"
        raise KitError(f"item {it.id}: {frm} -> {to} is not allowed. From {frm}: {allowed}.")
    if to in ("shipped", "dropped") and not internal:
        raise KitError(f"use kit {'ship' if to == 'shipped' else 'drop'} {it.id}, not kit status.")
    items = all_items(ctx)
    by = {i.id: i for i in items}
    risky = bool(it["risk"])
    parent = by.get(it["parent"])
    if to == "planning":
        if it["size"] != "big" or it["parent"] or it["children"]:
            raise KitError("only a big top-level item with no plan yet goes to planning.")
    elif to == "waiting":
        if it["size"] != "big" and not risky:
            raise KitError("only big or risky items wait. Quick and small go on the default.")
        if frm == "planning" and not it["children"]:
            raise KitError("planning -> waiting needs a plan. Run kit plan first.")
    elif to == "building":
        if open_decisions(ctx, it.id) and (it["size"] == "big" or risky):
            ids = ", ".join(d["id"] for d in open_decisions(ctx, it.id))
            raise KitError(f"big or risky item {it.id} waits for Mark's answer on {ids}.")
        if frm in ("ready", "failed") and not (note or "").strip():
            raise KitError(f"{frm} -> building needs Mark's change note (--note).")
        if it["children"]:
            if frm != "waiting" and not internal:
                raise KitError("a planned parent starts building only on Mark's go, from waiting. "
                               "To change a finished plan, send a child back: kit status <child> building --note.")
            return
        if it["size"] == "big" and not it["parent"]:
            raise KitError("big items go through the planner first (status planning).")
        if parent is not None and parent["status"] in ("new", "planning", "waiting"):
            raise KitError(f"parent {parent.id} plan is still waiting for Mark's go.")
        if it["size"] == "big" and len(big_building(items, it.id)) >= ctx.lim["big_running"]:
            raise KitError(f"{ctx.lim['big_running']} big items are already building.")
        if frm != "review":
            try:
                build_brief(ctx, it)
            except KitError as e:
                raise KitError(f"cannot start building: {e}")
    elif to in ("review", "ready") and it["children"]:
        if to == "review":
            raise KitError("a planned parent is not reviewed. Its children are.")
        kids = [by[c] for c in it["children"] if c in by]
        if not any(k["status"] == "ready" for k in kids) or \
                any(k["status"] not in ("ready", "shipped", "dropped") for k in kids):
            raise KitError("a planned parent is ready only when all its children are ready.")
    elif to in ("review", "ready"):
        err = proof_ok(ctx, it)
        if err:
            raise KitError(f"item {it.id} -> {to}: {err}")
        if to == "ready" and frm == "building":
            n = diff_lines(ctx, it)
            if risky or n > ctx.lim["review_lines"]:
                why = f"risk {', '.join(it['risk'])}" if risky else f"diff is {n} lines"
                raise KitError(f"item {it.id} needs review first ({why}). Use status review.")
        if to == "ready" and frm == "review" and not (note or "").strip():
            raise KitError("review -> ready needs the review result (--note).")


def transition(ctx, it, to, note="", internal=False, who="kit"):
    """The one place a status changes. Caller holds the lock. Saves the item."""
    check_untouched(ctx, it)
    frm = it["status"]
    guard(ctx, it, to, note, internal)
    if to == "building" and not it["children"] and frm != "review":
        make_worktree(ctx, it)
    if to == "building" and frm in ("ready", "failed"):
        it["attempts"] = 0
        it.add_line("Brief", f"Change note from Mark ({now()}): {note.strip()}")
    elif to == "ready" and frm == "review":
        it.add_line("Review", note.strip())
    elif note:
        log_work(it, who, note.strip())
    it["status"] = to
    log_work(it, who, f"status {frm} -> {to}")
    save(ctx, it)
    status_log(ctx, it.id, frm, to)
    sync_parent(ctx, it)


def sync_parent(ctx, child):
    """A planned parent follows its children: ready when all are ready, building again if not."""
    if not child["parent"]:
        return
    try:
        par = load(ctx, child["parent"])
    except KitError:
        return
    kids = [load(ctx, c) for c in par["children"]]
    done = all(k["status"] in ("ready", "shipped", "dropped") for k in kids)
    any_ready = any(k["status"] == "ready" for k in kids)
    if par["status"] == "building" and done and any_ready:
        transition(ctx, par, "ready", internal=True)
    elif par["status"] == "ready" and not done:
        transition(ctx, par, "building", note="child reopened", internal=True)


# ---------------------------------------------------------------- board and queue

def board_text(ctx):
    lines = ["# Board", "", "Generated by kit. Do not edit by hand.", ""]
    for i in all_items(ctx):
        extra = []
        if i["risk"]:
            extra.append("risk " + ",".join(i["risk"]))
        if i["parent"]:
            extra.append(f"part of {i['parent']}")
        if i["children"]:
            extra.append("children " + ",".join(i["children"]))
        tail = f" ({'; '.join(extra)})" if extra else ""
        lines.append(f"- {i.id} [{i['status']}] {i['size']} {'/'.join(i['roles']) or '-'}: "
                     f"{i['title']}{tail}")
    return "\n".join(lines) + "\n"


def write_board(ctx):
    write_atomic(ctx.build / "board.md", board_text(ctx))


def queue_text(ctx):
    items = all_items(ctx)
    by = {i.id: i for i in items}
    out = ["Ready and failed:"]
    n0 = len(out)
    for i in items:
        if i["status"] not in ("ready", "failed"):
            continue
        p = last_proof(i)
        proof = f"exit {p['exit']} at {p['time']}" if p else "none"
        changed = (i.sec("For Mark").splitlines() or [""])[0] or f"{diff_lines(ctx, i)} diff lines"
        out.append(f"- {i.id} {i['status']}: {i['title']} | changed: {changed} | proof: {proof} | "
                   f"risk: {','.join(i['risk']) or 'none'} | cost: {i['cost'] or 'none'}")
    if len(out) == n0:
        out.append("- none")
    out.append("Plans waiting for go:")
    n0 = len(out)
    for i in items:
        if i["status"] == "waiting" and i["children"]:
            out.append(f"- {i.id}: {i['title']}")
            for c in i["children"]:
                k = by.get(c)
                if k is not None:
                    out.append(f"    {k.id} {'/'.join(k['roles'])} {k['size']}: {k['title']}")
    if len(out) == n0:
        out.append("- none")
    out.append("Open questions:")
    decs = open_decisions(ctx)
    out += [f"- {d['id']} (item {d['item'] or '-'}): {d['question']} Default: {d['default']}"
            for d in decs] or ["- none"]
    out.append(f"Mark owes: {len(owes(ctx, items))} (limit {ctx.lim['mark_owes']})")
    return "\n".join(out)


# ---------------------------------------------------------------- cost

def tally(path):
    """Whole-run tokens from one subagent transcript JSONL. Same method as
    experiments/tally.py: sum message.usage per assistant message, de-duplicated by id."""
    seen, tools, first, last, model = {}, 0, None, None, ""
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            ts = r.get("timestamp")
            first = first or ts
            last = ts or last
            m = r.get("message") or {}
            if r.get("type") == "assistant" and m.get("usage"):
                key = m.get("id") or r.get("uuid")
                if key not in seen:
                    tools += sum(1 for c in m.get("content", []) or []
                                 if isinstance(c, dict) and c.get("type") == "tool_use")
                seen[key] = m["usage"]
                model = model or m.get("model", "")
    keys = ["input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"]
    tot = {k: sum(u.get(k, 0) or 0 for u in seen.values()) for k in keys}
    u1 = next(iter(seen.values()), {})
    return {"file": os.path.basename(path), "requests": len(seen), "tool_calls": tools, **tot,
            "all_tokens": sum(tot.values()), "model": model,
            "first_request_context": sum(u1.get(k, 0) or 0 for k in
                                         ["input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"]),
            "start": first, "end": last}


def cost_rows(ctx):
    p = ctx.build / "cost.jsonl"
    if not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()]


def cost_summary(rows):
    opus = sum(1 for r in rows if "opus" in (r.get("model") or "").lower())
    return (f"runs {len(rows)}, tokens {sum(r['all_tokens'] for r in rows)}, "
            f"max start {max([r['first_request_context'] for r in rows] or [0])}, opus {opus}")


# ---------------------------------------------------------------- commands

def cmd_new(ctx, a):
    if a.size not in SIZES:
        raise KitError(f"size must be one of {', '.join(SIZES)}")
    risk = split_list(a.risk)
    bad = [r for r in risk if r not in RISKS]
    if bad:
        raise KitError(f"unknown risk tag {', '.join(bad)}. Use {', '.join(RISKS)}.")
    roles = a.role or (["planner"] if a.size == "big" else ["coder"])
    bad = [r for r in roles if r not in AGENTS]
    if bad:
        raise KitError(f"unknown role {', '.join(bad)}")
    with ctx.lock():
        ensure_excluded(ctx)
        existing = [p.stem for p in ctx.items.glob("*.md")] if ctx.items.exists() else []
        n = next_id(ctx, existing=[e for e in existing if e.isdigit()])
        it = Item({"id": str(n), "title": a.title, "status": "new", "roles": roles,
                   "size": a.size, "risk": risk, "from": a.from_ or "", "parent": "",
                   "children": [], "files": split_list(a.files), "no_touch": split_list(a.no_touch),
                   "branch": "", "worktree": "", "session": a.session or "", "shipped": "",
                   "attempts": 0, "cost": "", "created": now()}, {})
        it.set_sec("Done when", "\n".join(f"- {d}" for d in (a.done or [])))
        it.set_sec("Brief", a.brief or "")
        log_work(it, "kit", "created")
        save(ctx, it)
        status_log(ctx, it.id, "", "new")
        write_board(ctx)
    print(it.id)


def split_list(s):
    if not s:
        return []
    if isinstance(s, list):
        s = ",".join(s)
    return [x.strip() for x in s.split(",") if x.strip() and x.strip().lower() != "none"]


PLAN_PREFIX = re.compile(r"^\s*(?:[-*]\s+|\d+[.)]\s+)")


def parse_plan(text, max_items):
    rows = []
    for n, ln in enumerate(text.splitlines(), 1):
        if not ln.strip():
            continue
        ln = PLAN_PREFIX.sub("", ln)
        parts = [p.strip() for p in ln.split("|")]
        if len(parts) != 6:
            raise KitError(f"plan line {n}: need 6 fields 'title | role | size | risk | files | done-when'")
        title, role, size, risk, files, done = parts
        if not title:
            raise KitError(f"plan line {n}: empty title")
        if role not in BUILDERS:
            raise KitError(f"plan line {n}: role '{role}' is not one of {', '.join(BUILDERS)}")
        if size not in SIZES:
            raise KitError(f"plan line {n}: size '{size}' is not quick, small or big")
        risks = split_list(risk)
        bad = [r for r in risks if r not in RISKS]
        if bad:
            raise KitError(f"plan line {n}: unknown risk {', '.join(bad)}")
        if role == "coder-big" and size != "big":
            raise KitError(f"plan line {n}: coder-big is for big items only")
        fl = split_list(files)
        dw = [d.strip() for d in done.split(";") if d.strip()]
        if not fl or not dw:
            raise KitError(f"plan line {n}: files and done-when are required")
        rows.append((title, role, size, risks, fl, dw))
    if not rows:
        raise KitError("plan is empty")
    if len(rows) > max_items:
        raise KitError(f"plan has {len(rows)} items, the cap is {max_items}")
    return rows


def cmd_plan(ctx, a):
    text = sys.stdin.read()
    rows = parse_plan(text, ctx.lim["plan_max_items"])
    with ctx.lock():
        par = load(ctx, a.id)
        check_untouched(ctx, par)
        if par["status"] != "planning":
            raise KitError(f"item {par.id} is {par['status']}. kit plan needs it in planning.")
        if par["children"]:
            raise KitError(f"item {par.id} already has a plan")
        existing = [p.stem for p in ctx.items.glob("*.md") if p.stem.isdigit()]
        kids = []
        for title, role, size, risks, fl, dw in rows:
            n = next_id(ctx, existing=existing)
            existing.append(str(n))
            k = Item({"id": str(n), "title": title, "status": "new", "roles": [role], "size": size,
                      "risk": risks, "from": f"plan of item {par.id}", "parent": par.id,
                      "children": [], "files": fl, "no_touch": list(par["no_touch"]),
                      "branch": "", "worktree": "", "session": "", "shipped": "", "attempts": 0,
                      "cost": "", "created": now()}, {})
            k.set_sec("Done when", "\n".join(f"- {d}" for d in dw))
            k.set_sec("Brief", f"Part of item {par.id}: {par['title']}.\nIdea: {par['from'] or par['title']}")
            log_work(k, "planner", f"planned under {par.id}")
            save(ctx, k)
            status_log(ctx, k.id, "", "new")
            kids.append(k.id)
        par["children"] = kids
        log_work(par, "planner", f"plan with {len(kids)} children: {', '.join(kids)}")
        save(ctx, par)
        transition(ctx, par, "waiting", who="planner")
        write_board(ctx)
    print(" ".join(kids))


def cmd_brief(ctx, a):
    it = load(ctx, a.id)
    print(build_brief(ctx, it))


def cmd_map(ctx, a):
    files = ctx.git("ls-files").stdout.splitlines()
    skip = [p.rstrip("/") for p in ["build"] + list(ctx.cfg["protected"])]
    files = [f for f in files if not any(f == s or f.startswith(s + "/") for s in skip)]
    key = [k.rstrip("/") for k in ctx.cfg["key_paths"]]
    first = [f for f in files if any(f == k or f.startswith(k + "/") for k in key)]
    rest = [f for f in files if f not in set(first)]
    ordered = first + rest
    print(f"{len(files)} files, key paths first")
    for f in ordered[:a.max]:
        print(f)
    if len(ordered) > a.max:
        print(f"... {len(ordered) - a.max} more (use --max)")


def cmd_status(ctx, a):
    if not a.state:
        it = load(ctx, a.id)
        print(f"{it.id} {it['status']}")
        return
    with ctx.lock():
        it = load(ctx, a.id)
        transition(ctx, it, a.state, note=a.note or "", who=a.who)
        write_board(ctx)
    print(f"{it.id} {it['status']}")


def can_start(ctx, it, agent):
    check_untouched(ctx, it)
    if agent not in AGENTS:
        raise Deny(f"'{agent}' is not a buildkit agent ({', '.join(AGENTS)})")
    items = all_items(ctx)
    by = {i.id: i for i in items}
    st, size, risky = it["status"], it["size"], set(it["risk"]) & RISKY
    if agent == "planner":
        if st != "planning":
            raise Deny(f"item {it.id} is {st}. The planner runs on items in planning.")
    elif agent in BUILDERS:
        if st != "building":
            raise Deny(f"item {it.id} is {st}. Builders run on items in building (kit status {it.id} building).")
        if it["children"]:
            raise Deny(f"item {it.id} is a planned parent with no branch. Start its children.")
        par = by.get(it["parent"])
        if par is not None and par["status"] in ("new", "planning", "waiting"):
            raise Deny(f"parent {par.id} plan waits for Mark's go.")
        if agent == "ui-designer" and "ui-designer" not in it["roles"]:
            raise Deny(f"item {it.id} has no ui-designer role.")
        if agent == "coder-big" and size != "big":
            raise Deny(f"coder-big (Opus) is for big items. Item {it.id} is {size}: use coder.")
        if agent == "coder" and size == "big":
            raise Deny(f"item {it.id} is big: use coder-big.")
        if budget_spent(ctx, it):
            raise Deny(f"item {it.id} failed {it['attempts']} times, the budget is "
                       f"{ctx.lim['attempts']} retries. It goes to Mark (kit status {it.id} failed).")
        if size == "big" and len(big_building(items, it.id)) >= ctx.lim["big_running"]:
            raise Deny(f"{ctx.lim['big_running']} big items are already building.")
        if not it["worktree"] or not os.path.isdir(it["worktree"]):
            raise Deny(f"item {it.id} has no worktree. Set it to building through kit status.")
    else:
        if st != "review":
            raise Deny(f"item {it.id} is {st}. Reviewers run on items in review.")
        if risky and agent != "reviewer-risky":
            raise Deny(f"item {it.id} is tagged {', '.join(sorted(risky))}: use reviewer-risky.")
        if not risky and agent == "reviewer-risky":
            raise Deny(f"reviewer-risky (Opus) is for risky items. Item {it.id}: use reviewer.")
    blockers = open_decisions(ctx, it.id) + (open_decisions(ctx, it["parent"]) if it["parent"] else [])
    if blockers and (size == "big" or it["risk"]):
        raise Deny(f"big or risky item {it.id} waits for Mark's answer on "
                   + ", ".join(d["id"] for d in blockers))
    try:
        build_brief(ctx, it)
    except KitError as e:
        raise Deny(str(e))
    if size == "big":
        ow = owes(ctx, items)
        if len(ow) > ctx.lim["mark_owes"]:
            raise Deny(f"Mark owes {len(ow)} (limit {ctx.lim['mark_owes']}). Big items wait. "
                       "Clear these first: " + "; ".join(t for _, _, t in ow))


def cmd_can_start(ctx, a):
    it = load(ctx, a.id)
    try:
        can_start(ctx, it, a.agent)
    except Deny as e:
        print(f"DENY: {e}", file=sys.stderr)
        return 1
    print(f"ok: {a.agent} may start item {it.id}")
    return 0


def cmd_diff(ctx, a):
    it = load(ctx, a.id)
    if not branch_head(ctx, branch_of(it)):
        raise KitError(f"no branch {branch_of(it)}")
    args = ["diff"] + (["--stat"] if a.stat else []) + [f"{ctx.cfg['main_branch']}...{branch_of(it)}"]
    sys.stdout.write(ctx.git(*args).stdout)
    if it["worktree"] and os.path.isdir(it["worktree"]) and wt_dirty(ctx, it["worktree"]):
        print(f"note: {it['worktree']} has uncommitted changes, not in this diff", file=sys.stderr)


def newest_change_mtime(ctx, it):
    wt = it["worktree"]
    names = set(ctx.git("diff", "--name-only", f"{ctx.cfg['main_branch']}...HEAD", cwd=wt).stdout.split())
    for ln in ctx.git("status", "--porcelain", cwd=wt).stdout.splitlines():
        names.add(ln[3:].strip())
    times = [os.path.getmtime(os.path.join(wt, n)) for n in names if os.path.isfile(os.path.join(wt, n))]
    return max(times or [0])


def cmd_proof(ctx, a):
    """Record a test result. Exit 0 pass, 2 fail with attempts left (block), 3 budget spent."""
    with ctx.lock():
        it = load(ctx, a.id)
        check_untouched(ctx, it)
        if it["status"] != "building":
            raise KitError(f"item {it.id} is {it['status']}. Proof is written while building.")
        wt = it["worktree"]
        if not wt or not os.path.isdir(wt):
            raise KitError(f"item {it.id} has no worktree")
    if not str(ctx.cfg.get("test_command") or "").strip():
        raise KitError("config test_command is empty")
    tail = ""
    tested = branch_head(ctx, branch_of(it))
    wt_head = ctx.git("rev-parse", "HEAD", cwd=wt, check=False).stdout.strip()
    wt_branch = ctx.git("symbolic-ref", "--short", "-q", "HEAD", cwd=wt, check=False).stdout.strip()
    pre = ""
    if wt_branch != branch_of(it) or wt_head != tested:
        pre = f"worktree is not on {branch_of(it)} at its tip."
    elif wt_dirty(ctx, wt):
        pre = "worktree has uncommitted changes. Commit first, then the tests run."
    if pre:
        a.exit, a.log = 1, "none"
    if a.exit is None:
        logs = ctx.build / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        log = logs / f"{it.id}-{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}.log"
        try:
            r = subprocess.run(ctx.cfg["test_command"], shell=True, cwd=wt, capture_output=True,
                               text=True, timeout=int(ctx.cfg["test_timeout"]))
            code, text = r.returncode, r.stdout + r.stderr
        except subprocess.TimeoutExpired:
            code, text = 124, "timed out"
        log.write_text(text)
        tail = "\n".join(text.splitlines()[-15:])
        log_path = str(log)
    else:
        code, log_path = a.exit, a.log or "none"
    reason = pre
    if code == 0 and "ui-designer" in it["roles"]:
        if not a.screenshot or not os.path.exists(a.screenshot):
            code, reason = 1, "UI item needs a screenshot (--screenshot)."
        elif os.path.getmtime(a.screenshot) <= newest_change_mtime(ctx, it):
            code, reason = 1, "screenshot is older than the last edit."
    with ctx.lock():
        it = load(ctx, a.id)
        if it["status"] != "building":
            raise KitError(f"item {it.id} left building while its tests ran. Proof not written.")
        if branch_head(ctx, branch_of(it)) != tested:
            reason = (reason + " " if reason else "") + "branch moved during the test run."
        line = f"- exit {code} | head {tested} | log {log_path} | {now()}"
        if a.screenshot:
            line += f" | shot {a.screenshot}"
        if reason:
            line += f" | {reason}"
        it.add_line("Proof", line)
        rc = 0
        if code != 0:
            it["attempts"] = it["attempts"] + 1
            rc = 3 if budget_spent(ctx, it) else 2
        save(ctx, it)
        write_board(ctx)
    if rc == 0:
        print(f"pass: item {it.id} exit 0")
    else:
        print(f"fail {it['attempts']} of {ctx.lim['attempts'] + 1}: item {it.id} exit {code}. {reason}".rstrip())
        if tail:
            print(tail)
        if rc == 3:
            print(f"attempts spent. Next: kit status {it.id} failed")
    return rc


def cmd_cost(ctx, a):
    if a.week:
        rows = cost_rows(ctx)
        cutoff = datetime.datetime.now().astimezone() - datetime.timedelta(days=7)
        rows = [r for r in rows if datetime.datetime.fromisoformat(r["t"]) >= cutoff]
        print(f"last 7 days: {cost_summary(rows)}")
        miss = []
        for r in rows:
            if r["first_request_context"] > START_TARGET:
                miss.append(f"item {r['id']} {r.get('agent') or '?'} start {r['first_request_context']} > {START_TARGET}")
        per = {}
        for r in rows:
            if "opus" in (r.get("model") or "").lower():
                per[r["id"]] = per.get(r["id"], 0) + 1
        miss += [f"item {i} had {n} Opus runs (target at most 1)" for i, n in per.items() if n > 1]
        for m in miss:
            print(f"MISS: {m}")
        if not miss:
            print("all targets met")
        return 1 if miss else 0
    if not a.id:
        raise KitError("kit cost <id> [--transcript FILE | --session SID] or kit cost --week")
    files = list(a.transcript or [])
    if a.session:
        files += glob.glob(os.path.expanduser(f"~/.claude/projects/*/{a.session}/subagents/*.jsonl"))
        if not files:
            raise KitError(f"no subagent transcript for session {a.session}")
    files = list(dict.fromkeys(os.path.realpath(f) for f in files))
    with ctx.lock():
        it = load(ctx, a.id)
        rows = [r for r in cost_rows(ctx) if r.get("path") not in files]
        for f in files:
            t = tally(f)
            t.update({"t": now(), "id": it.id, "agent": a.agent or "", "path": f})
            rows.append(t)
            log_work(it, "kit", f"cost {t['file']}: {t['all_tokens']} tokens, start "
                     f"{t['first_request_context']}, {t['tool_calls']} tool calls, {t['model'] or 'model?'}")
        if files:
            write_atomic(ctx.build / "cost.jsonl", "".join(json.dumps(r) + "\n" for r in rows))
        rows = [r for r in cost_rows(ctx) if r["id"] == it.id]
        if files:
            it["cost"] = cost_summary(rows)
            save(ctx, it)
            write_board(ctx)
    print(f"item {it.id}: {cost_summary(rows)}")


def cmd_note(ctx, a):
    """A helper's detail line in the item's Work log. Never changes state."""
    text = " ".join(a.text.split())
    if not text:
        raise KitError("empty note")
    with ctx.lock():
        it = load(ctx, a.id)
        check_untouched(ctx, it)
        if it["status"] in TERMINAL:
            raise KitError(f"item {it.id} is {it['status']}. Notes are for open items.")
        log_work(it, a.who, text[:NOTE_MAX])
        save(ctx, it)
    print(f"noted on item {it.id}")


def cmd_inbox(ctx, a):
    """Append one idea to build/inbox.md. Newlines become spaces, so one idea stays one line."""
    text = " ".join(a.text.split())
    if not text:
        raise KitError("empty idea")
    with ctx.lock():
        ctx.build.mkdir(parents=True, exist_ok=True)
        path = ctx.build / "inbox.md"
        old = path.read_text() if path.exists() else ""
        if old and not old.endswith("\n"):
            old += "\n"
        write_atomic(path, old + text + "\n")
    print(text)


def cmd_board(ctx, a):
    with ctx.lock():
        write_board(ctx)
    print(board_text(ctx), end="")


def cmd_queue(ctx, a):
    print(queue_text(ctx))


def run_tests_in(ctx, path):
    if not str(ctx.cfg.get("test_command") or "").strip():
        raise KitError("config test_command is empty. Nothing is tested, so nothing ships.")
    try:
        r = subprocess.run(ctx.cfg["test_command"], shell=True, cwd=path, capture_output=True,
                           text=True, timeout=int(ctx.cfg["test_timeout"]))
        return r.returncode, r.stdout + r.stderr
    except subprocess.TimeoutExpired:
        return 124, "timed out"


def ship_tmp_dir():
    return os.path.realpath(tempfile.gettempdir())


def remove_tmp_worktree(ctx, tmp):
    ctx.git("worktree", "remove", "--force", tmp, check=False)
    if under(tmp, ship_tmp_dir()) and os.path.basename(tmp).startswith("kit-ship-"):
        shutil.rmtree(tmp, ignore_errors=True)


def remote_sha(ctx, branch):
    r = ctx.git("ls-remote", "--heads", ctx.cfg["remote"], f"refs/heads/{branch}", check=False)
    return r.stdout.split()[0] if r.returncode == 0 and r.stdout.strip() else ""


def ship_one(ctx, iid):
    """Ship one leaf item. Returns the shipped value. Raises KitError on any stop."""
    main, remote = ctx.cfg["main_branch"], ctx.cfg["remote"]
    with ctx.lock():
        it = load(ctx, iid)
        check_untouched(ctx, it)
        if it["status"] != "ready":
            raise KitError(f"item {it.id} is {it['status']}. Only ready items ship.")
        guard_owned(ctx, it)
        br = branch_of(it)
        if it["branch"] != br or not branch_head(ctx, br):
            raise KitError(f"item {it.id} has no {br} branch")
        if it["worktree"] and os.path.isdir(it["worktree"]) and wt_dirty(ctx, it["worktree"]):
            raise KitError(f"{it['worktree']} has uncommitted changes. Nothing shipped.")
        err = proof_ok(ctx, it)
        if err:
            raise KitError(f"item {it.id} not shipped: {err}")
        if not str(ctx.cfg.get("test_command") or "").strip():
            raise KitError("config test_command is empty. Nothing is tested, so nothing ships.")
        head = branch_head(ctx, br)
        base = branch_head(ctx, main)
        if not base:
            raise KitError(f"no local branch {main}")
        shipping = ctx.build / "shipping"
        shipping.mkdir(exist_ok=True)
        marker = shipping / it.id
        if marker.exists() and os.path.exists(marker.read_text().strip()):
            raise KitError(f"item {it.id} is already shipping in {marker.read_text().strip()}")
        tmp = tempfile.mkdtemp(prefix=f"kit-ship-{it.id}-", dir=ship_tmp_dir())
        os.rmdir(tmp)
        marker.write_text(tmp + "\n")
        with open(ctx.build / "ship-temps.log", "a") as f:
            f.write(tmp + "\n")
    try:
        ctx.git("worktree", "add", "--detach", tmp, base)
        r = ctx.git("merge", "--no-ff", "-m", f"kit ship {it.id}: {it['title']}", head, cwd=tmp, check=False)
        if r.returncode:
            ctx.git("merge", "--abort", cwd=tmp, check=False)
            fail_ship(ctx, it.id, f"merge into {main} failed: {(r.stdout + r.stderr).strip()[-300:]}")
        merged = ctx.git("rev-parse", "HEAD", cwd=tmp).stdout.strip()
        code, out = run_tests_in(ctx, tmp)
        if code:
            fail_ship(ctx, it.id, f"tests failed after merge (exit {code}): {out.strip()[-300:]}")
        if ctx.git("rev-parse", "HEAD", cwd=tmp).stdout.strip() != merged or \
                ctx.git("status", "--porcelain", "--untracked-files=no", cwd=tmp).stdout.strip():
            fail_ship(ctx, it.id, "the tests changed HEAD or tracked files, so the tested tree is not the merge.")
    finally:
        remove_tmp_worktree(ctx, tmp)
        with contextlib.suppress(OSError):
            (ctx.build / "shipping" / it.id).unlink()
    with ctx.lock():
        it = load(ctx, iid)
        if it["status"] != "ready" or branch_head(ctx, br) != head:
            raise KitError(f"item {it.id} changed while testing. Nothing pushed. Ship again.")
        if ctx.cfg["ship"] == "pr":
            r = ctx.git("push", remote, f"{head}:refs/heads/{br}", check=False)
            if r.returncode:
                raise KitError(f"push of {br} failed, item stays ready: {r.stderr.strip()}")
            gh = os.environ.get("KIT_GH") or ctx.cfg["gh"]
            pr = subprocess.run([gh, "pr", "create", "--base", main, "--head", br,
                                 "--title", f"{it.id}: {it['title']}",
                                 "--body", it.sec("For Mark") or it["title"]],
                                cwd=ctx.root, capture_output=True, text=True)
            if pr.returncode:
                raise KitError(f"gh pr create failed, item stays ready: {(pr.stderr or pr.stdout).strip()}")
            shipped = "pr " + (pr.stdout.strip().splitlines() or ["?"])[-1]
            clean_item(ctx, it, branches=False)
        else:
            r = ctx.git("push", remote, f"{merged}:refs/heads/{main}", check=False)
            if r.returncode:
                raise KitError(f"push to {remote}/{main} failed, item stays ready: {r.stderr.strip()}")
            update_local_main(ctx, merged, base)
            shipped = merged
            clean_item(ctx, it, branches=True, merged=merged)
        it["shipped"] = shipped
        transition(ctx, it, "shipped", note=f"shipped {shipped}", internal=True)
        write_board(ctx)
    return shipped


def fail_ship(ctx, iid, why):
    with ctx.lock():
        it = load(ctx, iid)
        it.add_line("Proof", f"- ship stopped {now()}: {why}")
        save(ctx, it)
        transition(ctx, it, "failed", internal=True)
        write_board(ctx)
    raise KitError(f"item {iid} not shipped, now failed. {why}")


def update_local_main(ctx, sha, base):
    """Fast-forward local main to the shipped merge, only from the tip the merge was built on."""
    main = ctx.cfg["main_branch"]
    warn = f"warning: pushed, but local {main} was not updated. Run git pull."
    if branch_head(ctx, main) != base:
        print(warn + f" Local {main} moved during the ship.", file=sys.stderr)
        return
    holders = [p for p, b, _ in worktrees(ctx) if b == main]
    if any(os.path.realpath(p) != os.path.realpath(ctx.root) for p in holders):
        print(warn + f" {main} is checked out in another worktree.", file=sys.stderr)
        return
    if holders:
        r = ctx.git("merge", "--ff-only", sha, check=False)
    else:
        r = ctx.git("update-ref", f"refs/heads/{main}", sha, base, check=False)
    if r.returncode:
        print(warn, file=sys.stderr)


def clean_item(ctx, it, branches=True, merged=None, force=False):
    """Remove the item's worktree and, if asked, its bk/<id> branch (local and remote)."""
    guard_owned(ctx, it)
    br = branch_of(it)
    wt = it["worktree"]
    if wt and os.path.exists(wt):
        args = ["worktree", "remove"] + (["--force"] if force else []) + [wt]
        r = ctx.git(*args, check=False)
        if r.returncode:
            print(f"warning: could not remove {wt}: {r.stderr.strip()}", file=sys.stderr)
    if branches and it["branch"] == br:
        if branch_head(ctx, br):
            if merged and ctx.git("merge-base", "--is-ancestor", br, merged, check=False).returncode:
                print(f"warning: {br} is not in {merged}. Branch kept.", file=sys.stderr)
            else:
                r = ctx.git("branch", "-D", br, check=False)
                if r.returncode:
                    print(f"warning: could not delete {br}: {r.stderr.strip()}", file=sys.stderr)
        rsha = remote_sha(ctx, br)
        if rsha:
            if merged and ctx.git("merge-base", "--is-ancestor", rsha, merged, check=False).returncode:
                print(f"warning: {ctx.cfg['remote']}/{br} has commits not in {merged}. Kept.", file=sys.stderr)
            else:
                r = ctx.git("push", f"--force-with-lease=refs/heads/{br}:{rsha}", ctx.cfg["remote"],
                            "--delete", br, check=False)
                if r.returncode:
                    print(f"warning: could not delete {ctx.cfg['remote']}/{br}: {r.stderr.strip()}",
                          file=sys.stderr)
    if it["session"]:
        print(f"archive chat session {it['session']}")


def cmd_ship(ctx, a):
    it = load(ctx, a.id)
    if it["children"]:
        if it["status"] != "ready":
            raise KitError(f"item {it.id} is {it['status']}. A planned parent ships when ready.")
        last = ""
        for c in it["children"]:
            k = load(ctx, c)
            if k["status"] == "ready":
                last = ship_one(ctx, c)
                print(f"shipped {c}: {last}")
        with ctx.lock():
            par = load(ctx, a.id)
            if par["status"] == "ready":
                par["shipped"] = last
                transition(ctx, par, "shipped", note="all children shipped", internal=True)
            write_board(ctx)
        print(f"shipped {a.id}")
        return 0
    print(f"shipped {a.id}: {ship_one(ctx, a.id)}")
    return 0


def drop_one(ctx, it):
    if it["status"] in TERMINAL:
        return
    guard_owned(ctx, it)
    clean_item(ctx, it, branches=True, force=True)
    transition(ctx, it, "dropped", internal=True, who="Mark")


def cmd_drop(ctx, a):
    with ctx.lock():
        it = load(ctx, a.id)
        check_untouched(ctx, it)
        if it["status"] in TERMINAL:
            raise KitError(f"item {it.id} is already {it['status']}")
        for c in it["children"]:
            drop_one(ctx, load(ctx, c))
        it = load(ctx, a.id)
        if it["status"] not in TERMINAL:
            drop_one(ctx, it)
        write_board(ctx)
    print(f"dropped {a.id}")


def sweep_targets(ctx):
    items = {i.id: i for i in all_items(ctx)}

    def orphan(iid):
        i = items.get(iid)
        if i is None:
            return True
        if i["status"] not in TERMINAL:
            return False
        return not str(i["shipped"]).startswith("pr ")  # an open PR keeps its branch

    made = set()
    if (ctx.build / "ship-temps.log").exists():
        made = set((ctx.build / "ship-temps.log").read_text().split("\n"))
    active = set()
    if (ctx.build / "shipping").is_dir():
        active = {f.read_text().strip() for f in (ctx.build / "shipping").iterdir()}
    wts, brs, rbrs = [], [], []
    for path, br, detached in worktrees(ctx):
        m = re.fullmatch(r"bk/(\d+)", br or "")
        if m and orphan(m.group(1)):
            wts.append(path)
        elif detached and os.path.basename(path).startswith("kit-ship-") \
                and under(path, ship_tmp_dir()) and path not in active \
                and (path in made or os.path.realpath(path) in made):
            wts.append(path)
    for ln in ctx.git("for-each-ref", "--format=%(refname:short)", "refs/heads/bk/").stdout.split():
        m = re.fullmatch(r"bk/(\d+)", ln)
        if m and orphan(m.group(1)):
            brs.append(ln)
    remote = ctx.cfg["remote"]
    for ln in ctx.git("for-each-ref", "--format=%(refname:short)", f"refs/remotes/{remote}/bk/").stdout.split():
        m = re.fullmatch(rf"{re.escape(remote)}/(bk/(\d+))", ln)
        if m and orphan(m.group(2)):
            rbrs.append(m.group(1))
    return wts, brs, rbrs


def cmd_sweep(ctx, a):
    with ctx.lock():
        wts, brs, rbrs = sweep_targets(ctx)
        if not (wts or brs or rbrs):
            print("nothing to sweep")
            return 0
        for w in wts:
            print(f"worktree {w}")
        for b in brs:
            print(f"branch {b}")
        for b in rbrs:
            print(f"remote branch {ctx.cfg['remote']}/{b}")
        if not a.yes:
            print("Nothing deleted. Run kit sweep --yes after Mark says yes.")
            return 0
        for w in wts:
            if under(w, ctx.wt_dir()):
                ctx.git("worktree", "remove", "--force", w, check=False)
            elif under(w, ship_tmp_dir()) and os.path.basename(w).startswith("kit-ship-"):
                remove_tmp_worktree(ctx, w)
        for b in brs:
            if b.startswith("bk/"):
                ctx.git("branch", "-D", b, check=False)
        for b in rbrs:
            rsha = remote_sha(ctx, b) if b.startswith("bk/") else ""
            if rsha:
                ctx.git("push", f"--force-with-lease=refs/heads/{b}:{rsha}", ctx.cfg["remote"],
                        "--delete", b, check=False)
        print("deleted")
    return 0


def cmd_ledger(ctx, a):
    with ctx.lock():
        decs = read_decisions(ctx)
        if a.ledger_cmd == "add":
            if a.item:
                load(ctx, a.item)
            n = next_id(ctx, ".next_decision", existing=[d["id"][1:] for d in decs])
            d = {"id": f"D{n}", "state": "open", "item": str(a.item or ""),
                 "question": " ".join(a.question.split()), "default": " ".join(a.default.split()),
                 "answer": ""}
            decs.append(d)
            write_decisions(ctx, decs)
            print(d["id"])
        else:
            d = next((x for x in decs if x["id"] == a.did), None)
            if d is None:
                raise KitError(f"no decision {a.did}")
            if d["state"] == "answered":
                raise KitError(f"{a.did} is already answered")
            text = a.answer if a.answer is not None else sys.stdin.read()
            if not text.strip():
                raise KitError("empty answer")
            d["state"], d["answer"] = "answered", text.rstrip("\n")
            write_decisions(ctx, decs)
            print(f"{a.did} answered")
        write_board(ctx)
    return 0


# ---------------------------------------------------------------- CLI

def make_parser():
    p = argparse.ArgumentParser(prog="kit", description="buildkit state tool. Works on build/ in the project.")
    p.add_argument("--root", help="project root (default: nearest folder with build/config.yml, or $KIT_ROOT)")
    sub = p.add_subparsers(dest="cmd", metavar="command")
    sub.required = True

    s = sub.add_parser("new", help="create an item, print its id")
    s.add_argument("title")
    s.add_argument("--size", default="small", help="quick, small or big")
    s.add_argument("--role", action="append", help="repeatable. Default coder, planner for big")
    s.add_argument("--risk", default="", help=f"comma tags: {', '.join(RISKS)}")
    s.add_argument("--from", dest="from_", default="", help="the inbox line, verbatim")
    s.add_argument("--done", action="append", help="a done-when check, repeatable")
    s.add_argument("--files", default="", help="comma list of files to read or change")
    s.add_argument("--no-touch", default="", help="comma list of files not to touch")
    s.add_argument("--brief", default="", help="what to build")
    s.add_argument("--session", default="", help="chat session id, if run in its own chat")
    s.set_defaults(fn=cmd_new)

    s = sub.add_parser("plan", help="write a planner reply (stdin) as child items")
    s.add_argument("id")
    s.set_defaults(fn=cmd_plan)

    s = sub.add_parser("brief", help="print the full helper prompt, fail if a part is missing")
    s.add_argument("id")
    s.set_defaults(fn=cmd_brief)

    s = sub.add_parser("map", help="file list for the planner, key paths first")
    s.add_argument("--max", type=int, default=300)
    s.set_defaults(fn=cmd_map)

    s = sub.add_parser("status", help="show or change an item's state (the only state setter)")
    s.add_argument("id")
    s.add_argument("state", nargs="?", help=", ".join(STATES))
    s.add_argument("--note", default="", help="change note, review result or log line")
    s.add_argument("--who", default="kit", help="who made the change, for the work log")
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser("can-start", help="launch gate: exit 0 if the agent may start the item")
    s.add_argument("id")
    s.add_argument("agent", help=", ".join(AGENTS))
    s.set_defaults(fn=cmd_can_start)

    s = sub.add_parser("diff", help="diff of bk/<id> against the main branch")
    s.add_argument("id")
    s.add_argument("--stat", action="store_true")
    s.set_defaults(fn=cmd_diff)

    s = sub.add_parser("proof", help="run or record the tests for an item (exit 0 pass, 2 retry, 3 spent)")
    s.add_argument("id")
    s.add_argument("--exit", type=int, help="record this exit code instead of running the tests")
    s.add_argument("--log", help="log path to record with --exit")
    s.add_argument("--screenshot", help="screenshot path, required for UI items")
    s.set_defaults(fn=cmd_proof)

    s = sub.add_parser("cost", help="record a run's tokens from a transcript, or check the week")
    s.add_argument("id", nargs="?")
    s.add_argument("--transcript", action="append", help="subagent transcript JSONL, repeatable")
    s.add_argument("--session", help="parent session id: reads its subagents/*.jsonl")
    s.add_argument("--agent", help="which agent ran")
    s.add_argument("--week", action="store_true", help="check the usage targets for the last 7 days")
    s.set_defaults(fn=cmd_cost)

    s = sub.add_parser("note", help="add a detail line to an item's work log (no state change)")
    s.add_argument("id")
    s.add_argument("text")
    s.add_argument("--who", default="helper")
    s.set_defaults(fn=cmd_note)

    s = sub.add_parser("inbox", help="append one idea line to build/inbox.md")
    s.add_argument("text")
    s.set_defaults(fn=cmd_inbox)

    s = sub.add_parser("board", help="rebuild build/board.md and print it")
    s.set_defaults(fn=cmd_board)

    s = sub.add_parser("queue", help="Mark's review list")
    s.set_defaults(fn=cmd_queue)

    s = sub.add_parser("ship", help="merge bk/<id>, test, push (or open a PR), clean up")
    s.add_argument("id")
    s.set_defaults(fn=cmd_ship)

    s = sub.add_parser("drop", help="delete bk/<id> branch and worktree, mark dropped")
    s.add_argument("id")
    s.set_defaults(fn=cmd_drop)

    s = sub.add_parser("sweep", help="list bk/ worktrees and branches with no open item")
    s.add_argument("--yes", action="store_true", help="delete them (only after Mark says yes)")
    s.set_defaults(fn=cmd_sweep)

    s = sub.add_parser("ledger", help="decisions: ledger add, ledger answer")
    ls = s.add_subparsers(dest="ledger_cmd", metavar="ledger-command")
    ls.required = True
    la = ls.add_parser("add", help="add a question with a default, print its id")
    la.add_argument("question")
    la.add_argument("--default", required=True)
    la.add_argument("--item", help="item id the question blocks")
    la.set_defaults(fn=cmd_ledger)
    lb = ls.add_parser("answer", help="record Mark's answer verbatim (arg or stdin)")
    lb.add_argument("did")
    lb.add_argument("answer", nargs="?")
    lb.set_defaults(fn=cmd_ledger)
    return p


def main(argv=None):
    p = make_parser()
    a = p.parse_args(argv)
    try:
        ctx = Ctx(find_root(a.root))
        rc = a.fn(ctx, a)
        return rc or 0
    except KitError as e:
        print(f"kit: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
