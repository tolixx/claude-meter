#!/usr/bin/env python3
"""Claude Code PermissionRequest hook: lets the handheld answer permission prompts.

It writes the request to ~/.claude/agent-remote/pending/<id>.json, where
remote_server.py shows it to the handheld, then waits for
~/.claude/agent-remote/decisions/<id>.json. No answer in time, or no handheld
polling at all: it exits without output and the normal prompt shows in the
terminal. It never approves anything by itself.
"""
import json
import os
import sys
import time
import uuid

BASE = os.path.expanduser("~/.claude/agent-remote")
PENDING = os.path.join(BASE, "pending")
DECISIONS = os.path.join(BASE, "decisions")
SEEN = os.path.join(BASE, "client-seen")  # touched by the server on every poll
HIDE = os.path.join(BASE, "hide")  # projects the handheld does not show
WAIT = float(os.environ.get("CLAUDE_REMOTE_WAIT", "90"))  # s to wait for a button
CLIENT_FRESH = 15  # s: the handheld counts as connected if it polled this recently
POLL = 0.25


def summary(tool: str, inp: dict, cwd: str) -> str:
    """One line the handheld can show: the command, the file, the URL.

    Paths inside the project become relative and the home folder becomes ~,
    so the screen (and any video of it) doesn't show your username.
    """
    text = json.dumps(inp)
    for key in ("command", "file_path", "notebook_path", "url", "query", "pattern", "prompt"):
        if isinstance(inp.get(key), str) and inp[key]:
            text = inp[key]
            break
    if cwd:
        text = text.replace(cwd.rstrip("/") + "/", "")
    return text.replace(os.path.expanduser("~"), "~")[:300]


def client_connected() -> bool:
    try:
        return time.time() - os.path.getmtime(SEEN) < CLIENT_FRESH
    except OSError:
        return False


def is_hidden(project: str) -> bool:
    try:
        with open(HIDE) as fh:
            return project in {line.strip() for line in fh}
    except OSError:
        return False


def write_json(path: str, data: dict) -> None:
    with open(path + ".tmp", "w") as fh:
        json.dump(data, fh)
    os.replace(path + ".tmp", path)


def main() -> None:
    try:
        data = json.load(sys.stdin) or {}
    except Exception:
        return
    project = os.path.basename(data.get("cwd", "").rstrip("/")) or "claude"
    if not client_connected() or is_hidden(project):
        return
    os.makedirs(PENDING, exist_ok=True)
    os.makedirs(DECISIONS, exist_ok=True)
    rid = uuid.uuid4().hex[:12]
    tool = data.get("tool_name", "")
    pending = os.path.join(PENDING, f"{rid}.json")
    decision = os.path.join(DECISIONS, f"{rid}.json")
    write_json(pending, {
        "id": rid,
        "session": data.get("session_id", ""),
        "project": project,
        "tool": tool,
        "summary": summary(tool, data.get("tool_input") or {}, data.get("cwd", "")),
        "ts": time.time(),
    })
    try:
        deadline = time.time() + WAIT
        while time.time() < deadline:
            try:
                with open(decision) as fh:
                    behavior = (json.load(fh) or {}).get("behavior")
            except (OSError, ValueError):
                time.sleep(POLL)
                continue
            if behavior not in ("allow", "deny"):
                return
            out = {"behavior": behavior}
            if behavior == "deny":
                out["message"] = "Denied from the handheld"
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": out}}))
            return
    finally:
        for path in (pending, decision):
            try:
                os.remove(path)
            except OSError:
                pass


if __name__ == "__main__":
    main()
