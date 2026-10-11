#!/usr/bin/env python3
"""Shows Claude Code usage limits and session state on an AWTRIX 3 clock (Ulanzi TC001).

Limits come from the cache that save_limits.py writes from the official
rate_limits field of the status line JSON. Session state comes from
~/.claude/agent-state/*.json, written by hooks (agent_state_hook.py).
No tokens, no requests to Anthropic: it only reads local files and POSTs
to the clock on the local network.

Frame: "7% 49%" is the 5 hour window and the week.
The bottom row is weekly usage, the white pixel on it is how much of the week has passed.
Week colour follows the pace: green while usage stays behind time, then yellow, then red.

The indicator in the top right corner is the state of all sessions together:
blinking red when one is waiting for you, green when one is working, off when idle.
When a session starts waiting, a notification with the project name pops up once.
"""
import glob
import json
from datetime import datetime
import os
import signal
import sys
import time
import urllib.request

AWTRIX = os.environ.get("AWTRIX_HOST", "")  # clock IP or hostname, required
APP = os.environ.get("AWTRIX_APP", "claude")
ICON = os.environ.get("AWTRIX_ICON", "")  # icon id or name already on the clock, empty for none
CACHE = os.path.expanduser(os.environ.get("CLAUDE_LIMITS_CACHE", "~/.claude/statusline-limits-cache.json"))
STATE_DIR = os.path.expanduser("~/.claude/agent-state")
TICK = 2           # s between file checks
REFRESH = 240      # s: re-push even without changes so lifetime does not expire
LIFETIME = 900     # s: the clock drops the frame by itself if this script stops
RUN_STALE = 1800   # s: "running" with no update for this long means the session probably died
WAIT_STALE = 12 * 3600
GC_AGE = 24 * 3600  # state files older than a day are deleted

WEEK = 7 * 24 * 3600
WIDTH = 32
GREEN, YELLOW, RED, GREY, WHITE = "#3CD070", "#FFC400", "#FF3B30", "#303030", "#FFFFFF"
INDICATOR = {
    "wait": {"color": [255, 40, 30], "blink": 500},
    "run": {"color": [40, 200, 90]},
    "idle": {"color": [0, 0, 0]},
}


def color(pct: int) -> str:
    if pct >= 80:
        return RED
    if pct >= 50:
        return YELLOW
    return GREEN


def pace_color(used: int, elapsed: float) -> str:
    """Week: compare usage with the share of the week that has passed."""
    ahead = used - elapsed
    if used >= 90 or ahead > 15:
        return RED
    if ahead > 0:
        return YELLOW
    return GREEN


def read_limits(now: float):
    try:
        with open(CACHE) as fh:
            rl = json.load(fh) or {}
    except Exception:
        return None
    out = {}
    for key in ("five_hour", "seven_day"):
        w = rl.get(key) or {}
        pct = w.get("used_percentage")
        if pct is None:
            continue
        # The window has reset and no fresh data came in yet: usage starts from zero.
        if w.get("resets_at") and w["resets_at"] <= now:
            pct = 0
        out[key] = round(pct)
        if key == "seven_day" and w.get("resets_at") and w["resets_at"] > now:
            out["week_elapsed"] = round(max(0.0, min(100.0, (now - (w["resets_at"] - WEEK)) / WEEK * 100)))
    return out or None


def wait_resolved(s: dict) -> bool:
    """Whether the wait ended without any hook firing.

    When you reject a question or a permission prompt, or interrupt Claude,
    no hook fires (Stop is not sent on interrupt). But Claude Code writes the
    tool result, the interrupt line and turn_duration to the transcript right
    away, while nothing is written as long as the question is on screen.
    So any user/assistant/system record newer than the wait means it is over.
    """
    path = s.get("transcript")
    if not path:
        return False
    try:
        if os.path.getmtime(path) <= s["ts"]:
            return False
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - 65536))
            tail = fh.read().decode("utf-8", "ignore").splitlines()[1:]
    except OSError:
        return False
    for line in reversed(tail):
        try:
            r = json.loads(line)
            ts = datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00")).timestamp()
        except Exception:
            continue
        if ts <= s["ts"]:
            break
        if r.get("type") in ("user", "assistant", "system"):
            return True
    return False


def read_sessions(now: float) -> dict:
    """{session_id: (state, project)} for live sessions; removes stale files."""
    out = {}
    for path in glob.glob(os.path.join(STATE_DIR, "*.json")):
        try:
            with open(path) as fh:
                s = json.load(fh)
        except Exception:
            continue
        age = now - s.get("ts", 0)
        if age > GC_AGE:
            try:
                os.remove(path)
            except OSError:
                pass
            continue
        state = s.get("state")
        if state == "wait" and wait_resolved(s):
            state = "done"
            s.update(state=state, ts=now)
            try:
                with open(path + ".tmp", "w") as fh:
                    json.dump(s, fh)
                os.replace(path + ".tmp", path)
            except OSError:
                pass
        if (state == "run" and age > RUN_STALE) or (state == "wait" and age > WAIT_STALE):
            continue
        sid = os.path.basename(path)[:-5]
        out[sid] = (state, os.path.basename(s.get("cwd", "").rstrip("/")) or "claude")
    return out


def overall(sessions: dict) -> str:
    states = {st for st, _ in sessions.values()}
    if "wait" in states:
        return "wait"
    if "run" in states:
        return "run"
    return "idle"


def frame(lim: dict) -> dict:
    h = lim.get("five_hour")
    w = lim.get("seven_day")
    text = []
    if h is not None:
        text.append({"t": f"{h}%", "c": color(h)})
    el = lim.get("week_elapsed")
    wc = None
    if w is not None:
        wc = pace_color(w, el) if el is not None else color(w)
        text.append({"t": " " if text else "", "c": GREY})
        text.append({"t": f"{w}%", "c": wc})
    body = {"text": text, "noScroll": True, "lifetime": LIFETIME, "center": True}
    if w is not None:
        # Own bar in the bottom row instead of progress, to add the time marker.
        filled = round(w * WIDTH / 100)
        draw = [{"dl": [0, 7, WIDTH - 1, 7, GREY]}]
        if filled:
            draw.append({"dl": [0, 7, filled - 1, 7, wc]})
        if el is not None:
            draw.append({"dp": [min(WIDTH - 1, round(el * (WIDTH - 1) / 100)), 7, WHITE]})
        body["draw"] = draw
    if ICON:
        body["icon"] = ICON
    return body


def post(path: str, body: dict) -> None:
    req = urllib.request.Request(
        f"http://{AWTRIX}{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    urllib.request.urlopen(req, timeout=5).read()


def log(msg: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def clear_indicator() -> None:
    """The indicator never turns off by itself, so clear it when the daemon stops."""
    try:
        post("/api/indicator1", INDICATOR["idle"])
    except Exception:
        pass


def main() -> None:
    if not AWTRIX:
        sys.exit("Set AWTRIX_HOST to the clock's IP or hostname")
    once = "--once" in sys.argv
    last_lim, last_lim_at = None, 0.0
    last_ind, last_ind_at = None, 0.0
    prev, first = {}, not once
    signal.signal(signal.SIGTERM, lambda *_: (clear_indicator(), sys.exit(0)))
    while True:
        now = time.time()

        lim = read_limits(now)
        if lim and (lim != last_lim or now - last_lim_at >= REFRESH):
            try:
                post(f"/api/custom?name={APP}", frame(lim))
                log(f"limits {lim}")
                last_lim, last_lim_at = lim, now
            except Exception as e:
                log(f"limits push failed: {e}")

        sessions = read_sessions(now)
        # New waits: a session just switched to wait. Stay quiet on the first pass.
        fresh = [] if first else [proj for sid, (st, proj) in sessions.items()
                                  if st == "wait" and prev.get(sid, ("", ""))[0] != "wait"]
        prev, first = sessions, False
        for proj in fresh:
            try:
                post("/api/notify", {"text": f"WAIT {proj}", "color": RED,
                                     "duration": 6, "stack": False})
                log(f"notify wait {proj}")
            except Exception as e:
                log(f"notify failed: {e}")

        ind = overall(sessions)
        # The indicator has no lifetime, so confirm its state periodically too.
        if ind != last_ind or now - last_ind_at >= REFRESH:
            try:
                post("/api/indicator1", INDICATOR[ind])
                if ind != last_ind:
                    log(f"indicator {ind} ({len(sessions)} sessions)")
                last_ind, last_ind_at = ind, now
            except Exception as e:
                log(f"indicator push failed: {e}")

        if once:
            return
        time.sleep(TICK)


if __name__ == "__main__":
    main()
