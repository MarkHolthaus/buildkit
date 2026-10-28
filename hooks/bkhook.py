"""Shared bits for the buildkit hooks. Stdlib only.

Every hook exits silently (code 0, no output) unless the project has build/config.yml
and the agent is a buildkit agent. Plugin agents arrive as `buildkit:<name>`, so a user's
own agent called `coder` is never mistaken for ours.
"""
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent
KIT_FILE = Path(os.environ.get("BUILDKIT_KIT") or PLUGIN / "kit" / "kit.py")
PREFIX = "buildkit:"
ITEM_RE = re.compile(r"^# Item (\d+)\b", re.M)

_kit = None
_applies = False


def kit():
    """kit.py loaded as a module, for its constants, config parser and item loader."""
    global _kit
    if _kit is None:
        spec = importlib.util.spec_from_file_location("bk_kit", KIT_FILE)
        _kit = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_kit)
    return _kit


def read_input():
    raw = sys.stdin.read()
    log = os.environ.get("BUILDKIT_HOOK_LOG")
    if log:  # debug aid: one line per hook call, raw input included. Never fatal.
        try:
            with open(log, "a") as f:
                f.write(json.dumps({"hook": Path(sys.argv[0]).name, "input": raw}) + "\n")
        except OSError:
            pass
    try:
        data = json.loads(raw or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def mark_applies():
    """Called once a hook knows it is looking at a buildkit agent in a buildkit project.
    From then on a crash is a deny (PreToolUse) instead of a silent pass."""
    global _applies
    _applies = True


def run(main, pretool=False):
    """Run a hook's main. A crash exits 1, which Claude Code treats as a non-blocking error, so a
    PreToolUse guard that crashed would let the call through. Once the hook has decided it applies,
    a crash becomes a deny. Before that it is silent."""
    try:
        code = main()
    except (Exception, SystemExit) as e:
        reason = f"{Path(sys.argv[0]).name} crashed: {type(e).__name__}: {e}"
        if pretool and _applies:
            code = deny_pretool(reason)
        else:
            print(reason, file=sys.stderr)
            code = 0 if not _applies else 1
    sys.exit(code)


def agent_name(agent_type):
    """'buildkit:coder' -> 'coder'. None for anything that is not one of our agents."""
    if isinstance(agent_type, str) and agent_type.startswith(PREFIX):
        return agent_type[len(PREFIX):]
    return None


def find_root(data):
    """Nearest folder at or above the hook's cwd that has build/config.yml, or None."""
    start = data.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    p = Path(start).resolve()
    for d in [p, *p.parents]:
        if (d / "build" / "config.yml").is_file():
            return d
    return None


def item_id_from_text(text):
    m = ITEM_RE.search(text or "")
    return m.group(1) if m else None


def item_id_from_transcript(path):
    """The item id in the first user message of a subagent transcript (the kit brief)."""
    try:
        with open(path) as f:
            for _ in range(20):
                line = f.readline()
                if not line:
                    break
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                msg = rec.get("message") if isinstance(rec, dict) else None
                if not isinstance(msg, dict) or msg.get("role") != "user":
                    continue
                content = msg.get("content")
                if isinstance(content, list):
                    content = "\n".join(c.get("text", "") for c in content if isinstance(c, dict))
                return item_id_from_text(content if isinstance(content, str) else "")
    except OSError:
        return None
    return None


def subagent_transcript(data, agent_id):
    """Where a subagent's transcript lives: <parent transcript minus .jsonl>/subagents/agent-<id>.jsonl."""
    parent = data.get("transcript_path")
    if not parent or not agent_id:
        return None
    return str(Path(str(parent)[:-len(".jsonl")]) / "subagents" / f"agent-{agent_id}.jsonl")


def run_kit(root, *args, stdin=None, timeout=60):
    return subprocess.run([sys.executable, str(KIT_FILE), "--root", str(root), *args],
                          input=stdin, capture_output=True, text=True, timeout=timeout)


def allow_with_prompt(tool_input, prompt):
    """Let the launch go ahead with the prompt replaced. The desk names the item, kit writes the brief."""
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                             "permissionDecision": "allow",
                                             "updatedInput": {**tool_input, "prompt": prompt}}}))
    return 0


def deny_pretool(reason):
    """Print a PreToolUse deny and return the exit code (0, the decision is in the JSON)."""
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                             "permissionDecision": "deny",
                                             "permissionDecisionReason": reason}}))
    return 0
