#!/usr/bin/env python3
"""PreToolUse on Agent. Denies a buildkit helper launch that kit can-start refuses. When it allows,
it replaces the prompt with the output of kit brief, so a helper never gets a prompt the desk wrote."""
import sys

import bkhook as bk

KIT_TIMEOUT = 15  # three kit calls must fit inside the hook timeout in hooks.json
EXPECTED_MODEL = {"coder-big": "opus", "reviewer-risky": "opus"}


def main():
    data = bk.read_input()
    ti = data.get("tool_input") if isinstance(data.get("tool_input"), dict) else {}
    sub = ti.get("subagent_type")
    name = bk.agent_name(sub)
    if name is None:
        return 0
    root = bk.find_root(data)
    if root is None:
        return 0
    bk.mark_applies()
    try:
        k = bk.kit()
        if name not in k.AGENTS:
            return bk.deny_pretool(f"'{sub}' is not a buildkit agent ({', '.join(k.AGENTS)}).")
        model = str(ti.get("model") or "").lower()
        want = EXPECTED_MODEL.get(name, "sonnet")
        if model and want not in model:
            return bk.deny_pretool(f"{name} runs on {want}. Launch it without a model override.")
        if ti.get("isolation"):
            return bk.deny_pretool("kit makes the worktree (kit status <id> building). Launch without isolation.")
        prompt = str(ti.get("prompt") or "")
        iid = bk.item_id_from_text(prompt)
        if iid is None:
            return bk.deny_pretool("Name the item: the prompt must start with '# Item <id>'. "
                                   "The hook fills in the brief from kit brief.")
        brief = bk.run_kit(root, "brief", iid, timeout=KIT_TIMEOUT)
        if brief.returncode:
            return bk.deny_pretool((brief.stderr or brief.stdout).strip() or f"kit brief {iid} failed")
        gate = bk.run_kit(root, "can-start", iid, name, timeout=KIT_TIMEOUT)
        if gate.returncode:
            reason = (gate.stderr or gate.stdout).strip()
            return bk.deny_pretool(reason.removeprefix("DENY: ") or f"kit can-start {iid} {name} failed")
        full = brief.stdout.rstrip("\n")
        if name == "planner":
            fmap = bk.run_kit(root, "map", timeout=KIT_TIMEOUT)
            if fmap.returncode:
                return bk.deny_pretool("kit map failed: " + (fmap.stderr or fmap.stdout).strip())
            full += "\n\n## File map (kit map)\n" + fmap.stdout.rstrip("\n")
        return bk.allow_with_prompt(ti, full)
    except (Exception, SystemExit) as e:  # fail closed: a broken gate must not let a launch through
        return bk.deny_pretool(f"launch gate error: {type(e).__name__}: {e}")


if __name__ == "__main__":
    bk.run(main, pretool=True)
