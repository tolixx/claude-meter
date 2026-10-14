#!/usr/bin/env python3
"""Handheld remote for Claude Code sessions (Anbernic RG40XX H on muOS).

Polls remote_server.py on the Mac and shows the sessions and limits.
When a session asks for a permission, the prompt fills the screen:
A allows, B denies, left/right switch between prompts. MENU or SELECT+START quits.

Python 3 standard library only: SDL2 and SDL2_ttf are loaded with ctypes
from the system libraries muOS already has. Settings come from config.json
next to this file: {"server": "http://192.168.1.10:8787", "token": "..."}.

--snapshot out.bmp [state.json] renders one frame into a file, no window
needed, which is how the layout was tuned over SSH.
"""
import ctypes
import json
import os
import sys
import textwrap
import threading
import time
import urllib.request
from ctypes import POINTER, Structure, byref, c_char_p, c_int, c_uint8, c_uint32, c_void_p

HERE = os.path.dirname(os.path.abspath(__file__))
W, H = 640, 480
POLL = 1.0       # s between requests to the Mac
OFFLINE = 5.0    # s without an answer before the screen says so
FONT_DIR = "/usr/share/fonts/liberation"

BG = (14, 14, 16)
PANEL = (30, 30, 34)
TEXT = (235, 235, 235)
DIM = (130, 130, 136)
GREEN = (60, 208, 112)
YELLOW = (255, 196, 0)
RED = (255, 59, 48)
GREY = (90, 90, 96)
STATE_COLOR = {"wait": RED, "run": GREEN, "done": GREY}

# Deeplay-keys mapping from muOS gamecontrollerdb.txt (joystick button numbers)
BTN_A, BTN_B, BTN_SELECT, BTN_START, BTN_MENU = 3, 4, 9, 10, 11
HAT_UP, HAT_RIGHT, HAT_DOWN, HAT_LEFT = 1, 2, 4, 8

SDL_INIT_VIDEO, SDL_INIT_JOYSTICK = 0x20, 0x200
SDL_WINDOW_FULLSCREEN = 0x1
SDL_QUIT, SDL_KEYDOWN, SDL_JOYHATMOTION, SDL_JOYBUTTONDOWN = 0x100, 0x300, 0x602, 0x603
# Keyboard fallback for testing: arrows, A/Enter = allow, B/Backspace = deny, Esc = quit
KEYS = {1073741904: "left", 1073741903: "right", 1073741906: "up", 1073741905: "down",
        ord("a"): "allow", 13: "allow", ord("b"): "deny", 8: "deny", 27: "quit"}


class Color(Structure):
    _fields_ = [("r", c_uint8), ("g", c_uint8), ("b", c_uint8), ("a", c_uint8)]


class Rect(Structure):
    _fields_ = [("x", c_int), ("y", c_int), ("w", c_int), ("h", c_int)]


class Surface(Structure):
    _fields_ = [("flags", c_uint32), ("format", c_void_p), ("w", c_int), ("h", c_int)]


def load(name: str):
    for cand in (f"lib{name}-2.0.so.0", f"lib{name}.so", f"lib{name}-2.0.0.dylib"):
        try:
            return ctypes.CDLL(cand)
        except OSError:
            continue
    sys.exit(f"cannot load {name}")


sdl = load("SDL2")
ttf = load("SDL2_ttf")
sdl.SDL_CreateWindow.restype = c_void_p
sdl.SDL_CreateWindow.argtypes = [c_char_p, c_int, c_int, c_int, c_int, c_uint32]
sdl.SDL_CreateRenderer.restype = c_void_p
sdl.SDL_CreateRenderer.argtypes = [c_void_p, c_int, c_uint32]
sdl.SDL_CreateRGBSurface.restype = POINTER(Surface)
sdl.SDL_CreateRGBSurface.argtypes = [c_uint32, c_int, c_int, c_int, c_uint32, c_uint32, c_uint32, c_uint32]
sdl.SDL_CreateSoftwareRenderer.restype = c_void_p
sdl.SDL_CreateSoftwareRenderer.argtypes = [POINTER(Surface)]
sdl.SDL_CreateTextureFromSurface.restype = c_void_p
sdl.SDL_CreateTextureFromSurface.argtypes = [c_void_p, POINTER(Surface)]
sdl.SDL_SetRenderDrawColor.argtypes = [c_void_p, c_uint8, c_uint8, c_uint8, c_uint8]
sdl.SDL_RenderClear.argtypes = [c_void_p]
sdl.SDL_RenderFillRect.argtypes = [c_void_p, POINTER(Rect)]
sdl.SDL_RenderCopy.argtypes = [c_void_p, c_void_p, POINTER(Rect), POINTER(Rect)]
sdl.SDL_RenderPresent.argtypes = [c_void_p]
sdl.SDL_DestroyTexture.argtypes = [c_void_p]
sdl.SDL_FreeSurface.argtypes = [POINTER(Surface)]
sdl.SDL_JoystickOpen.restype = c_void_p
sdl.SDL_RWFromFile.restype = c_void_p
sdl.SDL_RWFromFile.argtypes = [c_char_p, c_char_p]
sdl.SDL_SaveBMP_RW.argtypes = [POINTER(Surface), c_void_p, c_int]
sdl.SDL_GetError.restype = c_char_p
ttf.TTF_OpenFont.restype = c_void_p
ttf.TTF_OpenFont.argtypes = [c_char_p, c_int]
ttf.TTF_RenderUTF8_Blended.restype = POINTER(Surface)
ttf.TTF_RenderUTF8_Blended.argtypes = [c_void_p, c_char_p, Color]
ttf.TTF_SizeUTF8.argtypes = [c_void_p, c_char_p, POINTER(c_int), POINTER(c_int)]


class Painter:
    def __init__(self, renderer):
        self.r = renderer
        self.fonts = {}
        self.cache = {}

    def font(self, size: int, kind: str = "Sans-Bold"):
        key = (size, kind)
        if key not in self.fonts:
            f = ttf.TTF_OpenFont(os.path.join(FONT_DIR, f"Liberation{kind}.ttf").encode(), size)
            if not f:
                sys.exit(f"font: {sdl.SDL_GetError().decode()}")
            self.fonts[key] = f
        return self.fonts[key]

    def rect(self, x, y, w, h, color):
        sdl.SDL_SetRenderDrawColor(self.r, *color, 255)
        sdl.SDL_RenderFillRect(self.r, byref(Rect(x, y, w, h)))

    def width(self, text: str, size: int, kind: str = "Sans-Bold") -> int:
        w, h = c_int(), c_int()
        ttf.TTF_SizeUTF8(self.font(size, kind), text.encode(), byref(w), byref(h))
        return w.value

    def text(self, x, y, text, size, color=TEXT, kind="Sans-Bold", align="left") -> int:
        """Draws text with its top left at x, y (or right/centre aligned). Returns the width."""
        if not text:
            return 0
        key = (text, size, kind, color)
        if key not in self.cache:
            if len(self.cache) > 300:
                for tex, _, _ in self.cache.values():
                    sdl.SDL_DestroyTexture(tex)
                self.cache.clear()
            surf = ttf.TTF_RenderUTF8_Blended(self.font(size, kind), text.encode(), Color(*color, 255))
            if not surf:
                return 0
            tex = sdl.SDL_CreateTextureFromSurface(self.r, surf)
            self.cache[key] = (tex, surf.contents.w, surf.contents.h)
            sdl.SDL_FreeSurface(surf)
        tex, w, h = self.cache[key]
        if align == "right":
            x -= w
        elif align == "center":
            x -= w // 2
        sdl.SDL_RenderCopy(self.r, tex, None, byref(Rect(x, y, w, h)))
        return w


def limit_color(pct: int) -> tuple:
    return RED if pct >= 80 else YELLOW if pct >= 50 else GREEN


def draw_header(p: Painter, st: dict, online: bool) -> None:
    lim = st.get("limits") or {}
    x = 20
    for label, key in (("5h", "five_hour"), ("week", "seven_day")):
        if key in lim:
            x += p.text(x, 16, label, 20, DIM) + 8
            x += p.text(x, 14, f"{lim[key]}%", 24, limit_color(lim[key])) + 24
    sessions = st.get("sessions") or []
    busy = sum(1 for s in sessions if s["state"] != "done")
    status = f"{busy} active" if online else "offline"
    p.text(W - 44, 16, status, 20, DIM if online else RED, align="right")
    p.rect(W - 34, 21, 14, 14, GREEN if online else RED)
    p.rect(0, 56, W, 2, PANEL)


def draw_sessions(p: Painter, st: dict) -> None:
    sessions = st.get("sessions") or []
    if not sessions:
        p.text(W // 2, 220, "No sessions", 28, DIM, align="center")
        return
    y = 76
    for s in sessions[:6]:
        color = STATE_COLOR.get(s["state"], GREY)
        p.rect(20, y, W - 40, 58, PANEL)
        p.rect(20, y, 8, 58, color)
        p.text(46, y + 14, s["project"][:28], 28, TEXT)
        p.text(W - 40, y + 18, s["state"].upper(), 22, color, align="right")
        y += 66
    if len(sessions) > 6:
        p.text(W // 2, y, f"+{len(sessions) - 6} more", 20, DIM, align="center")


def draw_prompt(p: Painter, st: dict, idx: int, flash: bool) -> None:
    pending = st["pending"]
    req = pending[idx]
    p.rect(0, 58, W, H - 58, (40, 12, 12) if flash else BG)
    p.text(20, 74, req["project"][:24], 34, TEXT)
    p.text(W - 20, 82, f"{idx + 1} of {len(pending)}" if len(pending) > 1 else "", 22, DIM, align="right")
    p.text(20, 120, req["tool"], 24, YELLOW)
    p.rect(20, 156, W - 40, 220, PANEL)
    lines = []
    for part in req["summary"].splitlines() or [""]:
        lines += textwrap.wrap(part, 44) or [""]
    for i, line in enumerate(lines[:8]):
        p.text(34, 166 + i * 26, line, 21, TEXT, kind="Mono-Regular")
    if len(lines) > 8:
        p.text(W - 34, 348, "...", 21, DIM, kind="Mono-Regular", align="right")
    # Buttons in the same places as on the handheld: B left, A right.
    p.rect(20, 396, 290, 64, (90, 22, 20))
    p.text(165, 412, "B  deny", 30, TEXT, align="center")
    p.rect(330, 396, 290, 64, (22, 96, 52))
    p.text(475, 412, "A  allow", 30, TEXT, align="center")


def draw(p: Painter, st: dict, online: bool, idx: int, flash: bool) -> None:
    sdl.SDL_SetRenderDrawColor(p.r, *BG, 255)
    sdl.SDL_RenderClear(p.r)
    draw_header(p, st, online)
    if st.get("pending"):
        draw_prompt(p, st, min(idx, len(st["pending"]) - 1), flash)
    else:
        draw_sessions(p, st)


class Client:
    """Polls the Mac in a background thread so the UI never blocks on the network."""

    def __init__(self, server: str, token: str):
        self.server, self.token = server.rstrip("/"), token
        self.state, self.last_ok = {}, 0.0
        self.lock = threading.Lock()

    def request(self, path: str, body: dict = None) -> dict:
        req = urllib.request.Request(self.server + path, headers={"X-Token": self.token},
                                     data=json.dumps(body).encode() if body is not None else None)
        with urllib.request.urlopen(req, timeout=3) as resp:
            return json.load(resp)

    def loop(self) -> None:
        while True:
            try:
                st = self.request("/state")
                with self.lock:
                    self.state, self.last_ok = st, time.time()
            except Exception:
                pass
            time.sleep(POLL)

    def decide(self, rid: str, behavior: str) -> None:
        def send():
            try:
                self.request("/decide", {"id": rid, "behavior": behavior})
            except Exception:
                pass
        threading.Thread(target=send, daemon=True).start()
        # Hide it right away; the next poll confirms.
        with self.lock:
            self.state["pending"] = [r for r in self.state.get("pending", []) if r["id"] != rid]

    def snapshot(self):
        with self.lock:
            return dict(self.state), time.time() - self.last_ok < OFFLINE


def snapshot(out: str, state_file: str = "") -> None:
    st = {}
    if state_file:
        with open(state_file) as fh:
            st = json.load(fh)
    surf = sdl.SDL_CreateRGBSurface(0, W, H, 32, 0xFF0000, 0xFF00, 0xFF, 0)
    p = Painter(sdl.SDL_CreateSoftwareRenderer(surf))
    draw(p, st, True, 0, False)
    sdl.SDL_SaveBMP_RW(surf, sdl.SDL_RWFromFile(out.encode(), b"wb"), 1)


def main() -> None:
    if ttf.TTF_Init() != 0:
        sys.exit("TTF_Init failed")
    if len(sys.argv) > 2 and sys.argv[1] == "--snapshot":
        return snapshot(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "")
    with open(os.path.join(HERE, "config.json")) as fh:
        cfg = json.load(fh)
    if sdl.SDL_Init(SDL_INIT_VIDEO | SDL_INIT_JOYSTICK) != 0:
        sys.exit(f"SDL_Init: {sdl.SDL_GetError().decode()}")
    win = sdl.SDL_CreateWindow(b"Agent Remote", 0, 0, W, H, SDL_WINDOW_FULLSCREEN)
    p = Painter(sdl.SDL_CreateRenderer(win, -1, 0))
    if sdl.SDL_NumJoysticks() > 0:
        sdl.SDL_JoystickOpen(0)
    client = Client(cfg["server"], cfg["token"])
    threading.Thread(target=client.loop, daemon=True).start()

    ev = ctypes.create_string_buffer(64)
    idx, seen, flash_until, held = 0, set(), 0.0, set()
    while True:
        actions = []
        while sdl.SDL_PollEvent(ev):
            kind = int.from_bytes(ev.raw[0:4], "little")
            if kind == SDL_QUIT:
                actions.append("quit")
            elif kind == SDL_JOYBUTTONDOWN:
                b = ev.raw[12]
                held.add(b)
                if b == BTN_A:
                    actions.append("allow")
                elif b == BTN_B:
                    actions.append("deny")
                elif b == BTN_MENU or {BTN_SELECT, BTN_START} <= held:
                    actions.append("quit")
            elif kind == 0x604:  # SDL_JOYBUTTONUP
                held.discard(ev.raw[12])
            elif kind == SDL_JOYHATMOTION:
                v = ev.raw[13]
                actions.append({HAT_LEFT: "left", HAT_RIGHT: "right", HAT_UP: "up", HAT_DOWN: "down"}.get(v))
            elif kind == SDL_KEYDOWN:
                actions.append(KEYS.get(int.from_bytes(ev.raw[20:24], "little", signed=True)))

        st, online = client.snapshot()
        pending = st.get("pending") or []
        ids = {r["id"] for r in pending}
        if ids - seen:
            flash_until = time.time() + 0.6
        seen = ids
        idx = min(idx, max(0, len(pending) - 1))
        for a in actions:
            if a == "quit":
                return
            if pending and a in ("allow", "deny"):
                client.decide(pending[idx]["id"], a)
                break
            if a == "left":
                idx = max(0, idx - 1)
            elif a == "right":
                idx = min(len(pending) - 1, idx + 1) if pending else 0

        draw(p, client.snapshot()[0], online, idx, time.time() < flash_until)
        sdl.SDL_RenderPresent(p.r)
        sdl.SDL_Delay(33)


if __name__ == "__main__":
    main()
