"""winuse host: a small HTTP service that lets an AI agent see and operate this desktop.

Started at logon by the 'winuse' scheduled task, inside the user's desktop session.
Listens on 127.0.0.1 only; the Mac reaches it through an SSH tunnel.
"""
from __future__ import annotations

import base64
import hmac
import json
import os
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
LOG_DIR = os.path.join(ROOT, "logs")
TOKEN_FILE = os.path.join(ROOT, "token.txt")
os.makedirs(LOG_DIR, exist_ok=True)


def _run_without_console() -> None:
    """Log to a file and drop any console window, so nothing appears on the user's screen.

    Needed because the venv's pythonw.exe in Python 3.13.0 starts the console interpreter.
    Pass --console to keep the console when running by hand."""
    if "--console" in sys.argv:
        return
    logfile = open(os.path.join(LOG_DIR, "host.log"), "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = logfile
    try:
        os.dup2(logfile.fileno(), 1)
        os.dup2(logfile.fileno(), 2)
    except OSError:
        pass
    try:
        import ctypes
        ctypes.windll.kernel32.FreeConsole()
    except Exception:
        pass


_run_without_console()

import winuse_core as core  # noqa: E402
import winuse_py  # noqa: E402
import winuse_win as win  # noqa: E402

try:  # imported on the main thread so the COM apartment outlives the request threads
    import winuse_uia as uia  # noqa: E402
    UIA_ERROR = ""
except Exception as _e:
    uia = None
    UIA_ERROR = f"{type(_e).__name__}: {_e}"
try:
    import winuse_launch as launcher  # noqa: E402
    LAUNCH_ERROR = ""
except Exception as _e:  # noqa: BLE001
    launcher = None
    LAUNCH_ERROR = f"{type(_e).__name__}: {_e}"

STARTED = time.time()
MIN_IDLE = float(os.environ.get("WINUSE_MIN_IDLE", "3"))  # seconds the user must be idle before real input
_input_lock = threading.Lock()
_last_host_input = 0.0
_own_tick = None   # tick count right after our last injected input
_user_tick = None  # tick count of the last input that was not ours


class UserActive(RuntimeError):
    """The person at the PC is using the mouse or keyboard right now."""


def _tick_after(a: int, b: int, slack: int = 0) -> bool:
    """True if tick a is more than slack ms after tick b (32-bit tick counts wrap)."""
    d = (a - b) & 0xFFFFFFFF
    return slack < d < 0x80000000


def user_idle_seconds() -> float:
    """Seconds since the last mouse/keyboard input that did not come from this host."""
    global _user_tick
    last = win.last_input_tick()
    if _user_tick is None or _own_tick is None or _tick_after(last, _own_tick, 80):
        if _user_tick is None or _tick_after(last, _user_tick):
            _user_tick = last
    return ((win.kernel32.GetTickCount() - _user_tick) & 0xFFFFFFFF) / 1000.0


def _require_idle(p: dict) -> None:
    need = float(p.get("min_idle", MIN_IDLE))
    idle = user_idle_seconds()
    if not p.get("force") and idle < need:
        raise UserActive(f"someone used the mouse or keyboard {idle:.1f}s ago; real input waits for "
                         f"{need:g}s of idle time (pass force to override)")


def _mark_own_input() -> None:
    global _own_tick, _last_host_input
    _own_tick = win.kernel32.GetTickCount()
    _last_host_input = time.time()


def log(msg: str) -> None:
    print(time.strftime("%Y-%m-%d %H:%M:%S"), msg, flush=True)


def _log_action(method: str, params: dict, ok: bool, ms: float, error: str | None) -> None:
    safe = dict(params)
    for k in ("text", "code"):  # never keep typed/clipboard text or script source, only the length
        if k in safe:
            safe[k] = f"<{len(str(safe[k]))} chars>"
    path = os.path.join(LOG_DIR, "actions.log")
    try:
        if os.path.exists(path) and os.path.getsize(path) > 5_000_000:
            os.replace(path, path + ".1")
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "method": method, "params": safe,
                                "ok": ok, "ms": round(ms), "error": error}, ensure_ascii=False) + "\n")
    except OSError:
        pass


# ------------------------------------------------------------------- methods
def _require_reachable(hwnd) -> None:
    """Refuse instead of failing silently when Windows would block input to this window (UIPI)."""
    if hwnd and win.uipi_blocked(hwnd):
        raise win.InputBlocked(
            f'"{win.window_title(hwnd)}" belongs to a program running as administrator, but the winuse host runs '
            "without admin rights (installed with WINUSE_NOADMIN), and Windows blocks input from "
            "lower-privilege programs (UIPI). Reinstall without WINUSE_NOADMIN to operate elevated windows.")


def _require_desktop() -> None:
    """Refuse input when the lock screen or a UAC prompt owns the input desktop."""
    desk = win.input_desktop()
    if desk != "Default":
        state = "locked" if win.session_locked() else "secure desktop (UAC prompt?)"
        raise win.InputBlocked(f"cannot send input: the desktop is not interactive ({state})")


def _point(p: dict, kx: str = "x", ky: str = "y") -> tuple[int, int]:
    return win.to_screen(p[kx], p[ky], absolute=bool(p.get("abs")), shot=p.get("shot"))


def _after() -> dict:
    time.sleep(0.05)
    return {"foreground": win.foreground(), "cursor": list(win.cursor_pos())}


def m_status(p: dict) -> dict:
    s = win.status()
    s.update({"version": core.VERSION, "pid": os.getpid(), "uptime_seconds": round(time.time() - STARTED),
              "user_idle_seconds": round(user_idle_seconds(), 1), "min_idle": MIN_IDLE,
              "accessibility": uia is not None, "accessibility_error": UIA_ERROR or None,
              "seconds_since_host_input": round(time.time() - _last_host_input, 1) if _last_host_input else None})
    return s


def m_screenshot(p: dict) -> dict:
    r = win.screenshot(window=p.get("window"), region=p.get("region"), region_abs=bool(p.get("abs")),
                       max_edge=int(p.get("max_edge", 1280)), fmt=p.get("format", "png"),
                       quality=int(p.get("quality", 80)), background=bool(p.get("background")),
                       display=p.get("display"))
    r["image"] = base64.b64encode(r["image"]).decode("ascii")
    r["foreground"] = win.foreground()
    r["locked"] = win.session_locked()
    return r


def m_click(p: dict) -> dict:
    x, y = _point(p)
    mods = core.parse_combo(p["modifiers"]) if p.get("modifiers") else []
    pos = win.click(x, y, button=p.get("button", "left"), count=int(p.get("count", 1)), modifiers=mods)
    return {"clicked": list(pos), **_after()}


def m_move(p: dict) -> dict:
    return {"moved": list(win.move_to(*_point(p))), **_after()}


def m_drag(p: dict) -> dict:
    x1, y1 = _point(p, "x1", "y1")
    x2, y2 = _point(p, "x2", "y2")
    win.drag(x1, y1, x2, y2, button=p.get("button", "left"), duration=float(p.get("duration", 0.4)))
    return {"dragged": [[x1, y1], [x2, y2]], **_after()}


def m_scroll(p: dict) -> dict:
    at = _point(p) if "x" in p and "y" in p else None
    win.scroll(dx=int(p.get("dx", 0)), dy=int(p.get("dy", 0)), at=at)
    return _after()


def m_type(p: dict) -> dict:
    n = win.type_text(str(p["text"]), delay=float(p.get("delay", 0)))
    if p.get("enter"):
        win.press([core.VK_RETURN])
    return {"typed_chars": n, **_after()}


def m_key(p: dict) -> dict:
    combos = p["keys"] if isinstance(p["keys"], list) else [p["keys"]]
    parsed = [core.parse_combo(c) for c in combos]  # validate all before pressing any
    for vks in parsed:
        win.press(vks)
        time.sleep(0.05)
    return {"pressed": combos, **_after()}


def m_windows(p: dict) -> dict:
    return {"windows": win.list_windows(include_all=bool(p.get("all")))}


def m_window(p: dict) -> dict:
    hwnd = win.resolve_window(p.get("window"))
    try:
        return win.window_action(hwnd, p["action"], x=p.get("x"), y=p.get("y"), w=p.get("w"), h=p.get("h"))
    finally:
        if p["action"] == "focus":
            _mark_own_input()  # focusing may tap Alt; that is our input, not the user's


def m_launch(p: dict) -> dict:
    if launcher is None:  # no COM support: start it from the host, with the host's rights
        win.launch(p["target"], args=p.get("args"), cwd=p.get("cwd"), show=p.get("show") or "normal",
                   no_console=bool(p.get("no_console")))
        time.sleep(float(p.get("wait") or 1.0))
        return {"launched": p["target"], "as": "host", "note": "launching as the desktop user is unavailable: "
                + LAUNCH_ERROR, "foreground": win.foreground()}
    r = launcher.launch(p["target"], args=p.get("args"), cwd=p.get("cwd"), show=p.get("show") or "normal",
                        no_console=bool(p.get("no_console")), admin=bool(p.get("admin")),
                        new=bool(p.get("new")), wait=p.get("wait"))
    if "focused" in r:
        _mark_own_input()  # focusing may tap Alt; that is our input, not the user's
    return r


def m_apps(p: dict) -> dict:
    if launcher is None:
        raise ValueError("the app list is unavailable on the PC: " + LAUNCH_ERROR)
    if p.get("refresh"):
        launcher.apps(refresh=True)
    hits = launcher.find_apps(p.get("query") or "", int(p.get("limit") or 30))
    return {"apps": [{k: a.get(k) for k in ("name", "source", "exe", "id")} for a in hits]}


def _need_uia() -> None:
    if uia is None:
        raise ValueError("accessibility support is not available on the PC: " + UIA_ERROR)


def m_snapshot(p: dict) -> dict:
    _need_uia()
    if p.get("all"):
        wins = [w for w in win.list_windows() if not w["minimized"]][: int(p.get("max_windows", 8))]
    else:
        wins = [win.window_info(win.resolve_window(p.get("window")))]
    def take():
        return uia.snapshot([w["hwnd"] for w in wins], interactive_only=bool(p.get("interactive")),
                            max_nodes=int(p.get("max_nodes", 400)), include_offscreen=bool(p.get("offscreen")),
                            structured=bool(p.get("structured")))

    r = take()
    # Chromium (Chrome, Edge, Electron apps) builds a page's accessibility tree only once a client
    # asks, so the first read of a fresh window has the frame and no page. Read again shortly.
    for delay in (0.4, 0.8):
        if not any(win.window_class(h).startswith("Chrome_WidgetWin") for h in r.get("no_document", [])):
            break
        time.sleep(delay)
        r = take()
    r.pop("no_document", None)
    r["windows"] = wins
    return r


def m_py(p: dict) -> dict:
    """Script mode: run Python in a persistent namespace whose helpers call this same dispatcher."""
    timeout = min(float(p.get("timeout", 120)), 1800)
    return _engine.run(str(p.get("code", "")), session=str(p.get("session") or "default"),
                       reset=bool(p.get("reset")), timeout=timeout)


def m_element(p: dict) -> dict:
    """Act on a snapshot element. Tries the accessibility interface first (nothing on screen
    moves); only if the control has none, and unless bg is set, borrows the real mouse/keyboard
    and then puts the pointer and the foreground window back."""
    _need_uia()
    action = p["action"]
    r = uia.act(p["ref"], action, text=p.get("text"), snap=p.get("snap"), amount=int(p.get("amount", 1)))
    if action not in ("click", "set") or r.get("via") is not None:
        return r
    if p.get("bg"):
        raise ValueError("this control cannot be operated in the background; retry without bg to borrow the mouse")
    with _input_lock:
        _require_desktop()
        _require_reachable(r.get("hwnd"))
        _require_idle(p)
        prev_fg, prev_cursor = win.user32.GetForegroundWindow(), win.cursor_pos()
        try:
            if r.get("hwnd"):
                win.focus_window(r["hwnd"])
            win.click(*r["point"])
            if action == "set" and r.get("combo"):
                time.sleep(0.2)
                win.type_text(str(p["text"]))  # type-ahead picks the item; Enter commits it
                win.press([core.VK_RETURN])
            elif action == "set":
                win.press(core.parse_combo("ctrl+a"))
                win.type_text(str(p["text"]))
            time.sleep(0.15)
        finally:
            win.user32.SetCursorPos(*prev_cursor)
            if p.get("restore", True) and prev_fg and win.user32.IsWindow(prev_fg):
                win.focus_window(prev_fg)
            _mark_own_input()
    return {"via": "mouse" if action == "click" else "mouse+keyboard", "state": "done",
            "point": r["point"], "borrowed": True, "foreground": win.foreground()}


def m_run(p: dict) -> dict:
    return win.run_command(p["command"], shell=p.get("shell", "powershell"),
                           timeout=float(p.get("timeout", 60)), cwd=p.get("cwd"))


def m_clipboard(p: dict) -> dict:
    if "text" in p:
        win.clipboard_set(str(p["text"]))
        return {"set_chars": len(str(p["text"]))}
    return {"text": win.clipboard_get()}


def m_wait(p: dict) -> dict:
    """Wait until a window matching 'window' exists (or is gone, with gone=true)."""
    deadline = time.time() + float(p.get("timeout", 15))
    want_gone = bool(p.get("gone"))
    while True:
        try:
            info = win.window_info(win.resolve_window(p["window"]))
        except ValueError:
            info = None
        if (info is None) == want_gone:
            return {"matched": True, "window": info}
        if time.time() >= deadline:
            return {"matched": False, "window": info}
        time.sleep(0.2)


# name -> (handler, sends_input)
METHODS = {
    "status": (m_status, False), "screenshot": (m_screenshot, False), "windows": (m_windows, False),
    "clipboard": (m_clipboard, False), "run": (m_run, False), "launch": (m_launch, False), "apps": (m_apps, False),
    "wait": (m_wait, False), "window": (m_window, False),
    "snapshot": (m_snapshot, False), "element": (m_element, False), "py": (m_py, False),
    "click": (m_click, True), "move": (m_move, True), "drag": (m_drag, True), "scroll": (m_scroll, True),
    "type": (m_type, True), "key": (m_key, True),
}


def dispatch(method: str, params: dict) -> dict:
    if method not in METHODS:
        return {"ok": False, "error": "unknown_method", "message": f"unknown method {method!r}"}
    fn, sends_input = METHODS[method]
    t0 = time.time()
    win.ensure_thread_dpi()  # request threads must see physical pixels, whatever the process manifest says
    try:
        if sends_input:
            with _input_lock:
                _require_desktop()
                _require_reachable(win.user32.GetForegroundWindow())
                _require_idle(params)
                try:
                    result = fn(params)
                finally:
                    _mark_own_input()
        else:
            result = fn(params)
        out = {"ok": True, **result}
    except win.InputBlocked as e:
        out = {"ok": False, "error": "blocked", "message": str(e), "locked": win.session_locked()}
    except UserActive as e:
        out = {"ok": False, "error": "user_active", "message": str(e)}
    except (ValueError, KeyError) as e:
        out = {"ok": False, "error": "bad_request", "message": str(e) if isinstance(e, ValueError) else f"missing parameter {e}"}
    except Exception as e:  # report instead of dying; the task would only restart us a minute later
        log("ERROR in " + method + "\n" + traceback.format_exc())
        out = {"ok": False, "error": "failed", "message": f"{type(e).__name__}: {e}", "locked": win.session_locked()}
    _log_action(method, params, out["ok"], (time.time() - t0) * 1000, out.get("message"))
    return out


_engine = winuse_py.Engine(lambda method, params: dispatch(method, params))

# ---------------------------------------------------------------------- http
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "winuse/" + core.VERSION
    token = ""

    def log_message(self, *args):  # the action log covers this
        pass

    def _send(self, code: int, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self._send(200, {"ok": True, "name": "winuse", "version": core.VERSION})
        else:
            self._send(404, {"ok": False, "error": "not_found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        if self.path != "/rpc":
            return self._send(404, {"ok": False, "error": "not_found"})
        given = (self.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
        if not hmac.compare_digest(given.encode(), self.token.encode()):
            return self._send(401, {"ok": False, "error": "unauthorized"})
        try:
            req = json.loads(raw or b"{}")
            method, params = req["method"], req.get("params") or {}
            if not isinstance(params, dict):
                raise TypeError("params must be an object")
        except Exception as e:
            return self._send(400, {"ok": False, "error": "bad_request", "message": str(e)})
        self._send(200, dispatch(method, params))


def main() -> None:
    port = int(os.environ.get("WINUSE_PORT", core.DEFAULT_PORT))
    with open(TOKEN_FILE, encoding="utf-8") as f:
        Handler.token = f.read().strip()
    if len(Handler.token) < 16:
        raise SystemExit(f"{TOKEN_FILE} is missing or too short")
    dpi = win.init_dpi()
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    log(f"winuse host {core.VERSION} listening on 127.0.0.1:{port} pid={os.getpid()} "
        f"session={win.session_id()} dpi={dpi}")
    server.serve_forever()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        log("FATAL\n" + traceback.format_exc())
        sys.exit(1)
