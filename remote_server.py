#!/usr/bin/env python3
"""Small LAN API for a handheld remote: session states, limits and permission prompts.

GET  /state   sessions (wait first), pending permission prompts, limits
POST /decide  {"id": "...", "behavior": "allow" | "deny"}

Every request needs the X-Token header. The token is generated on the first
run into ~/.claude/agent-remote/token (or set CLAUDE_REMOTE_TOKEN).
Approving a prompt runs a command on this Mac, so keep the token secret
and the port on your LAN only.
"""
import glob
import hmac
import json
import os
import secrets
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from awtrix_limits import log, read_limits, read_sessions

BASE = os.path.expanduser("~/.claude/agent-remote")
PENDING = os.path.join(BASE, "pending")
DECISIONS = os.path.join(BASE, "decisions")
SEEN = os.path.join(BASE, "client-seen")
PORT = int(os.environ.get("CLAUDE_REMOTE_PORT", "8787"))
PENDING_STALE = 600  # s: a hook never waits this long, so older files are leftovers
ORDER = {"wait": 0, "run": 1, "done": 2}


def load_token() -> str:
    if os.environ.get("CLAUDE_REMOTE_TOKEN"):
        return os.environ["CLAUDE_REMOTE_TOKEN"]
    path = os.path.join(BASE, "token")
    try:
        with open(path) as fh:
            return fh.read().strip()
    except FileNotFoundError:
        os.makedirs(BASE, exist_ok=True)
        token = secrets.token_urlsafe(16)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(token + "\n")
        log(f"new token in {path}")
        return token


TOKEN = load_token()


def read_pending(now: float) -> list:
    out = []
    for path in glob.glob(os.path.join(PENDING, "*.json")):
        try:
            with open(path) as fh:
                p = json.load(fh)
        except (OSError, ValueError):
            continue
        if now - p.get("ts", 0) > PENDING_STALE:
            try:
                os.remove(path)
            except OSError:
                pass
            continue
        out.append(p)
    return sorted(out, key=lambda p: p["ts"])


def state(now: float) -> dict:
    pending = read_pending(now)
    asking = {p["session"] for p in pending}
    sessions = [{"id": sid, "state": "wait" if sid in asking else st, "project": proj}
                for sid, (st, proj) in read_sessions(now).items()]
    sessions.sort(key=lambda s: (ORDER.get(s["state"], 3), s["project"]))
    return {"sessions": sessions, "pending": pending, "limits": read_limits(now) or {}}


class Handler(BaseHTTPRequestHandler):
    def authorized(self) -> bool:
        if hmac.compare_digest(self.headers.get("X-Token", ""), TOKEN):
            return True
        self.reply(403, {"error": "bad token"})
        return False

    def reply(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path != "/state":
            return self.reply(404, {"error": "not found"})
        if not self.authorized():
            return
        os.makedirs(BASE, exist_ok=True)
        with open(SEEN, "w"):
            pass
        self.reply(200, state(time.time()))

    def do_POST(self):
        if self.path != "/decide":
            return self.reply(404, {"error": "not found"})
        if not self.authorized():
            return
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            rid, behavior = str(body["id"]), body["behavior"]
        except (ValueError, KeyError, TypeError):
            return self.reply(400, {"error": "need id and behavior"})
        if behavior not in ("allow", "deny") or not rid.isalnum():
            return self.reply(400, {"error": "behavior is allow or deny"})
        if not os.path.exists(os.path.join(PENDING, f"{rid}.json")):
            return self.reply(410, {"error": "prompt is gone"})
        os.makedirs(DECISIONS, exist_ok=True)
        path = os.path.join(DECISIONS, f"{rid}.json")
        with open(path + ".tmp", "w") as fh:
            json.dump({"behavior": behavior}, fh)
        os.replace(path + ".tmp", path)
        log(f"{behavior} {rid}")
        self.reply(200, {"ok": True})

    def log_message(self, *args):
        pass


def main() -> None:
    if "--token" in sys.argv:
        print(TOKEN)
        return
    log(f"listening on :{PORT}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
