#!/usr/bin/env python3
"""PreToolUse on Edit and Write. Keeps buildkit helpers inside their own worktree and out of
build/, the config's protected paths and the item's no_touch list."""
import fnmatch
import os
import sys
import time
from pathlib import Path

import bkhook as bk


def main():
    data = bk.read_input()
    name = bk.agent_name(data.get("agent_type"))
    if name is None:
        return 0
    root = bk.find_root(data)
    if root is None:
        return 0
    bk.mark_applies()
    try:
        return check(data, name, root)
    except (Exception, SystemExit) as e:  # fail closed
        return bk.deny_pretool(f"path guard error: {type(e).__name__}: {e}")


def under_ci(path, parent):
    """Strictly under, compared without case: the default macOS volume is case-insensitive, so
    BUILD/ and .GIT name the same folders as build/ and .git."""
    return str(path).casefold().startswith(str(parent).casefold().rstrip(os.sep) + os.sep)


def owner_item(data):
    """The item this helper was launched for, from its transcript. Retried briefly because the
    transcript is written as the run goes."""
    path = bk.subagent_transcript(data, data.get("agent_id"))
    for _ in range(4):
        owner = bk.item_id_from_transcript(path) if path else None
        if owner:
            return owner
        time.sleep(0.25)
    return None


def protected_hit(rel, entry):
    """Does rel fall under a protected entry? A plain entry is a file or folder. An entry with
    * ? [ is a glob matched against the path and every folder above it. Case is ignored."""
    e = str(entry).strip().removeprefix("./").casefold()
    if not e:
        return False
    rel = rel.casefold()
    if any(ch in e for ch in "*?["):
        parts = rel.split("/")
        paths = ["/".join(parts[:i]) for i in range(1, len(parts) + 1)]
        pats = [e] + ([e[3:]] if e.startswith("**/") else [])
        return any(fnmatch.fnmatchcase(x, pat) for x in paths for pat in pats)
    e = e.rstrip("/")
    return rel == e or rel.startswith(e + "/")


def check(data, name, root):
    k = bk.kit()
    ti = data.get("tool_input") if isinstance(data.get("tool_input"), dict) else {}
    raw = ti.get("file_path") or ti.get("notebook_path")
    if not raw:
        return 0
    if name not in k.BUILDERS:
        return bk.deny_pretool(f"{name} is read-only. It does not edit files.")
    ctx = k.Ctx(root)
    root = ctx.root
    base = Path(data.get("cwd") or root)
    absolute = raw if os.path.isabs(raw) else base / raw
    p = Path(os.path.realpath(absolute))          # where the write really lands
    lex = Path(os.path.normpath(absolute))        # the name the helper gave, symlinks not followed
    if p == root / "build" or under_ci(p, root / "build"):
        return bk.deny_pretool(f"{p} is under build/. Only kit writes there.")
    wt_dir = root / k.WORKTREES
    if not under_ci(p, wt_dir):
        return bk.deny_pretool(f"{name} edits only inside its worktree under {k.WORKTREES}/. {p} is outside.")
    wt = wt_dir / p.relative_to(wt_dir).parts[0]
    rels = [p.relative_to(wt).as_posix() if p != wt else ""]
    if under_ci(lex, wt):
        rels.append(lex.relative_to(wt).as_posix())
    if not wt.name.casefold().startswith("bk-"):
        return bk.deny_pretool(f"{wt.name} is not a kit worktree (bk-<id>).")
    iid = wt.name[len("bk-"):]
    owner = owner_item(data)
    if owner is None:
        return bk.deny_pretool("Cannot tell which item this helper works on (no '# Item <id>' in its transcript).")
    if owner != iid:
        return bk.deny_pretool(f"item {owner} works in {k.WORKTREES}/bk-{owner}, not {wt.name}.")
    try:
        no_touch = list(k.load(ctx, iid)["no_touch"])
    except k.KitError:
        return bk.deny_pretool(f"{wt.name} has no item {iid}. Nothing to edit there.")
    for rel in rels:
        if protected_hit(rel, ".git"):
            return bk.deny_pretool("git metadata is off limits.")
        for entry in ["build/", *ctx.cfg["protected"], *no_touch]:
            if protected_hit(rel, entry):
                return bk.deny_pretool(f"{rel} is protected ({entry}). Do not edit it.")
    return 0


if __name__ == "__main__":
    bk.run(main, pretool=True)
