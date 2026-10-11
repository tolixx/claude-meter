#!/usr/bin/env python3
"""Claude Code hook: records the session state for awtrix_limits.py.

Usage: agent_state_hook.py run|wait|done|end, with the hook event JSON on stdin.
Each session writes its own ~/.claude/agent-state/<session_id>.json, so
parallel sessions don't step on each other. No network here: only the
daemon talks to the clock (on macOS it is the one with Local Network access).
"""
import json
import os
import sys
import time

STATE_DIR = os.path.expanduser("~/.claude/agent-state")


def main() -> None:
    state = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        data = json.load(sys.stdin) or {}
    except Exception:
        data = {}
    sid = data.get("session_id")
    if not sid or state not in ("run", "wait", "done", "end"):
        return
    path = os.path.join(STATE_DIR, f"{sid}.json")
    if state == "end":
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        return
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump({"state": state, "cwd": data.get("cwd", ""), "ts": time.time(),
                   "transcript": data.get("transcript_path", "")}, fh)
    os.replace(tmp, path)


if __name__ == "__main__":
    main()
