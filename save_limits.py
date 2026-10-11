#!/usr/bin/env python3
"""Claude Code status line that saves rate_limits for awtrix_limits.py.

Claude Code passes the usage limits to the status line command on stdin
(the rate_limits field). This saves them to a cache file the daemon reads.

Usage in ~/.claude/settings.json:

    "statusLine": {"type": "command", "command": "python3 /path/to/save_limits.py"}

prints a short "5h 7% · week 49%" line. To keep your own status line, pass it
as arguments: it gets the same JSON on stdin and its output is printed as is.

    "command": "python3 /path/to/save_limits.py python3 ~/.claude/my_statusline.py"

A session that has not made a request yet gets no rate_limits, so the cache
keeps the last values: limits belong to the account, not to a session.
"""
import json
import os
import subprocess
import sys

CACHE = os.path.expanduser(os.environ.get("CLAUDE_LIMITS_CACHE", "~/.claude/statusline-limits-cache.json"))


def main() -> None:
    raw = sys.stdin.read()
    try:
        rl = (json.loads(raw) or {}).get("rate_limits") or {}
    except Exception:
        rl = {}
    if rl:
        try:
            tmp = CACHE + ".tmp"
            with open(tmp, "w") as fh:
                json.dump(rl, fh)
            os.replace(tmp, CACHE)
        except OSError:
            pass
    else:
        try:
            with open(CACHE) as fh:
                rl = json.load(fh) or {}
        except Exception:
            pass

    if len(sys.argv) > 1:
        out = subprocess.run(sys.argv[1:], input=raw, capture_output=True, text=True)
        sys.stdout.write(out.stdout)
        return

    parts = []
    for key, label in (("five_hour", "5h"), ("seven_day", "week")):
        pct = (rl.get(key) or {}).get("used_percentage")
        parts.append(f"{label} {round(pct)}%" if pct is not None else f"{label} --")
    print(" · ".join(parts))


if __name__ == "__main__":
    main()
