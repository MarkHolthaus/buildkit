#!/usr/bin/env python3
"""Whole-run token totals for the subagent in a headless session.
Usage: tally.py <session_id>
Reads the sidechain (subagent) transcript under ~/.claude/projects and sums usage per
assistant message, de-duplicated by message id (streamed messages repeat)."""
import glob, json, os, sys
sid = sys.argv[1]
files = glob.glob(os.path.expanduser(f"~/.claude/projects/*/{sid}/subagents/*.jsonl"))
if not files:
    sys.exit("no subagent transcript found")
for f in files:
    seen, tools, first, last = {}, 0, None, None
    for line in open(f):
        r = json.loads(line)
        ts = r.get("timestamp")
        first = first or ts; last = ts or last
        m = r.get("message") or {}
        if r.get("type") == "assistant" and m.get("usage"):
            seen[m.get("id") or r["uuid"]] = m["usage"]
            tools += sum(1 for c in m.get("content", []) if c.get("type") == "tool_use")
    keys = ["input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"]
    tot = {k: sum(u.get(k, 0) for u in seen.values()) for k in keys}
    u1 = next(iter(seen.values()), {})
    print(json.dumps({"file": os.path.basename(f), "requests": len(seen), "tool_calls": tools,
        **tot, "all_tokens": sum(tot.values()),
        "first_request_context": sum(u1.get(k, 0) for k in ["input_tokens","cache_read_input_tokens","cache_creation_input_tokens"]),
        "start": first, "end": last}))
