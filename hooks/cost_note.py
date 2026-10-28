#!/usr/bin/env python3
"""SubagentStop for buildkit agents. Records the helper's whole-run tokens in the item through
kit cost. Not PostToolUse: the Agent tool returns at launch for a background helper, before any
of the run exists. SubagentStop fires when the helper ends, in the foreground or the background.
A helper the test gate sends back stops more than once. kit keeps one row per transcript file."""
import json
import os
import sys
import time
from pathlib import Path

import bkhook as bk


def wait_until_complete(path, last_message, quiet=None, limit=None):
    """The transcript is still being written when SubagentStop fires: in one smoke run the last
    request was missing and the total came out 14k short. Wait until the helper's final message
    is in the file's tail and the file has stopped growing, or give up after the limit."""
    quiet = float(os.environ.get("BUILDKIT_COST_QUIET", "0.6")) if quiet is None else quiet
    limit = float(os.environ.get("BUILDKIT_COST_LIMIT", "8")) if limit is None else limit
    snippet = json.dumps(str(last_message or "").strip()[:40])[1:-1]
    end = time.time() + limit
    last = -1
    while True:
        size = os.path.getsize(path)
        if size == last:
            with open(path, "rb") as f:
                f.seek(max(0, size - 65536))
                if not snippet or snippet in f.read().decode("utf-8", "replace"):
                    return True
        last = size
        if time.time() >= end:
            return False
        time.sleep(quiet)


def main():
    data = bk.read_input()
    name = bk.agent_name(data.get("agent_type"))
    if name is None:
        return 0
    root = bk.find_root(data)
    if root is None:
        return 0
    bk.mark_applies()
    path = data.get("agent_transcript_path") or bk.subagent_transcript(data, data.get("agent_id"))
    iid = bk.item_id_from_transcript(path) if path else None
    if iid is None or not Path(path).is_file():
        print(f"cost note: no item id or transcript for {name} ({path})", file=sys.stderr)
        return 1
    try:
        complete = wait_until_complete(path, data.get("last_assistant_message"))
        r = bk.run_kit(root, "cost", iid, "--transcript", str(path), "--agent", name, timeout=40)
        if not complete:
            bk.run_kit(root, "note", iid, "cost note: transcript may be missing its last request, "
                       "total can be short", "--who", "cost-note", timeout=20)
    except Exception as e:
        print(f"cost note error: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    if r.returncode:
        print((r.stderr or r.stdout).strip(), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    bk.run(main)
