#!/usr/bin/env python3
"""SubagentStop for buildkit builders. Runs kit proof. Exit 0 pass, 2 block with the test tail,
and on the last failed try sets the item failed through kit status."""
import sys
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
        k = bk.kit()
        if name not in k.BUILDERS:
            return 0
        path = data.get("agent_transcript_path") or bk.subagent_transcript(data, data.get("agent_id"))
        iid = bk.item_id_from_transcript(path)
        if iid is None:
            print("test gate: no '# Item <id>:' brief found in the helper transcript, no proof written",
                  file=sys.stderr)
            return 0
        args = ["proof", iid]
        shot = Path(root) / "build" / "shots" / f"{iid}.png"
        if name == "ui-designer" and shot.is_file():
            args += ["--screenshot", str(shot)]
        timeout = int(k.Ctx(root).cfg["test_timeout"]) + 120
        r = bk.run_kit(root, *args, timeout=timeout)
    except Exception as e:
        print(f"test gate error: {type(e).__name__}: {e}", file=sys.stderr)
        return 0
    if r.returncode == 0:
        return 0
    if r.returncode == 2:
        sys.stderr.write(r.stdout.rstrip() + "\nFix the failure, commit on your branch, then stop again.\n")
        return 2
    if r.returncode == 3:
        s = bk.run_kit(root, "status", iid, "failed", "--who", "test-gate",
                       "--note", "test attempts spent, goes to Mark")
        print(r.stdout.rstrip() + ("" if s.returncode else f"\nitem {iid} set to failed"))
        if s.returncode:
            unrun(root, iid, f"kit status {iid} failed: {(s.stderr or s.stdout).strip()}")
        return 0
    unrun(root, iid, f"kit proof {iid} could not run: {(r.stderr or r.stdout).strip()}")
    return 0


def unrun(root, iid, why):
    """The gate could not do its job. The helper may stop (no proof means the item cannot reach
    ready, and can-start still counts attempts), but the item says so, so Mark sees it in the log."""
    print(f"test gate: {why}", file=sys.stderr)
    try:
        bk.run_kit(root, "note", iid, f"test gate: {why}", "--who", "test-gate", timeout=30)
    except Exception:
        pass


if __name__ == "__main__":
    bk.run(main)
