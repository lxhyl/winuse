"""Script mode: run a batch of winuse actions as Python inside the host, in a persistent namespace.

One call can snapshot, filter, act and verify, with no SSH round trip per step and no tree dumped
back to the caller unless the script prints it. Helpers are thin wrappers over the host's normal
RPC dispatcher, so every guard (lock screen, user present, UIPI, the input lock, the action log)
applies exactly as for single commands.

This module has no Windows imports; the host injects its dispatcher, so it is testable anywhere.
"""
from __future__ import annotations

import ast
import re
import sys
import threading
import time
import traceback

FILENAME = "<winuse>"
MAX_OUTPUT = 20000
MAX_IMAGES = 4
HELPERS = ("status", "windows", "window", "snap", "find", "find1", "click", "fill", "write", "key",
           "scroll", "wait_for", "launch", "apps", "shot", "run", "clip", "sleep", "call")


class ScriptTimeout(Exception):
    pass


class ActionError(Exception):
    """A winuse action failed; the message is the host's error."""


class El:
    """One element of a snapshot. Prints as its outline line, e.g. @e12 Button "Save" [disabled]."""
    __slots__ = ("ref", "snap", "type", "name", "value", "rect", "enabled", "toggle", "selected",
                 "expand", "focused", "_s")

    def __init__(self, session, snap: str, item: dict):
        self._s = session
        self.snap = snap
        self.ref = item["ref"]
        self.type = item.get("type", "")
        self.name = item.get("name") or ""
        self.value = item.get("value")
        self.rect = item.get("rect")
        self.enabled = item.get("enabled", True)
        self.toggle = item.get("toggle")
        self.selected = item.get("selected")
        self.expand = item.get("expand")
        self.focused = item.get("focused", False)

    def __repr__(self) -> str:
        line = f"@{self.ref} {self.type}"
        if self.name:
            line += f' "{_clip(self.name)}"'
        if self.value not in (None, "") and _clip(self.value) != _clip(self.name):
            line += f' value="{_clip(self.value)}"'
        flags = []
        if self.enabled is False:
            flags.append("disabled")
        if self.toggle is not None:
            flags.append({0: "unchecked", 1: "checked"}.get(self.toggle, "mixed"))
        if self.selected:
            flags.append("selected")
        if self.expand in (0, 1):
            flags.append("expanded" if self.expand == 1 else "collapsed")
        return line + (f" [{' '.join(flags)}]" if flags else "")

    __str__ = __repr__

    @property
    def checked(self) -> bool | None:
        return None if self.toggle is None else self.toggle == 1

    def _act(self, action: str, **extra):
        return self._s.call("element", ref=self.ref, snap=self.snap, action=action, **extra)

    def click(self, bg: bool = False):
        return _via(self._act("click", bg=bg or None))

    def set(self, text: str, bg: bool = False):
        return _via(self._act("set", text=text, bg=bg or None))

    def toggle_(self):
        return _via(self._act("toggle"))

    def invoke(self):
        return _via(self._act("invoke"))

    def select(self):
        return _via(self._act("select"))

    def expand_(self):
        return _via(self._act("expand"))

    def collapse(self):
        return _via(self._act("collapse"))

    def focus(self):
        return _via(self._act("focus"))

    def show(self):
        return _via(self._act("show"))

    def text(self) -> str:
        return self._act("text").get("text", "")

    def point(self) -> tuple[int, int]:
        return tuple(self._act("point")["point"])

    def info(self) -> dict:
        r = self._act("info")
        return {k: v for k, v in r.items() if k != "ok"}


class Snapshot(list):
    """List of El from one snapshot; .text is the outline as the CLI prints it."""

    def __init__(self, items, sid: str, text: str, windows: list):
        super().__init__(items)
        self.id, self.text, self.windows = sid, text, windows

    def __repr__(self) -> str:
        return self.text


class RunResult:
    __slots__ = ("code", "out", "err", "timed_out")

    def __init__(self, r: dict):
        self.code, self.out, self.err = r.get("exit_code"), r.get("stdout", ""), r.get("stderr", "")
        self.timed_out = r.get("timed_out", False)

    def __repr__(self) -> str:
        return self.out.rstrip("\n")

    __str__ = __repr__

    @property
    def ok(self) -> bool:
        return self.code == 0 and not self.timed_out


def _clip(text, limit: int = 80) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "..."


def _via(r: dict) -> str:
    """Compact description of how an element action was done: 'invoke', 'mouse (borrowed)', ..."""
    via = r.get("via") or "mouse"
    return via + (" (borrowed the real pointer)" if r.get("borrowed") else "") + \
        (" (pending: may have opened a dialog)" if r.get("state") == "pending" else "")


class Session:
    """One persistent namespace and the helpers bound to it."""

    def __init__(self, dispatch, name: str = "default"):
        self.dispatch = dispatch
        self.name = name
        self.busy = threading.Lock()
        self.last: Snapshot | None = None
        self._out: list[str] = []
        self._out_len = 0
        self.images: list[dict] = []
        self.deadline = float("inf")
        self.ns: dict = {"__name__": "__winuse__", "__builtins__": __builtins__}
        for h in HELPERS:
            self.ns[h] = getattr(self, h)
        self.ns.update(El=El, ActionError=ActionError, ScriptTimeout=ScriptTimeout)

    # -------------------------------------------------------------- plumbing
    def _print(self, *args, sep=" ", end="\n", file=None, flush=False):
        if file not in (None, sys.stdout):
            return print(*args, sep=sep, end=end, file=file, flush=flush)
        text = sep.join(str(a) for a in args) + end
        room = MAX_OUTPUT - self._out_len
        if room > 0:
            self._out.append(text[:room])
        self._out_len += len(text)

    def check_deadline(self):
        if time.monotonic() > self.deadline:
            raise ScriptTimeout("script timed out")

    def call(self, method: str, **params) -> dict:
        """Escape hatch: any agent RPC method; raises ActionError on failure."""
        self.check_deadline()
        r = self.dispatch(method, {k: v for k, v in params.items() if v is not None})
        if not r.get("ok"):
            raise ActionError(f'{method}: {r.get("error", "failed")}: {r.get("message", "")}'.rstrip(": "))
        return r

    # --------------------------------------------------------------- helpers
    def status(self) -> dict:
        r = self.call("status")
        fg = r.get("foreground") or {}
        return {"locked": r.get("locked"), "can_interact": r.get("can_interact"),
                "user_idle": r.get("user_idle_seconds"), "foreground": fg.get("title"),
                "foreground_process": fg.get("process"), "displays": len(r.get("displays") or []) or 1}

    def windows(self, all: bool = False) -> list[dict]:
        return [{"hwnd": w["hwnd"], "title": w["title"], "process": w["process"], "rect": w["rect"],
                 "foreground": w["foreground"], "minimized": w["minimized"]}
                for w in self.call("windows", all=all or None)["windows"]]

    def window(self, spec, action: str = "focus", **kw) -> dict:
        r = self.call("window", window=spec, action=action, **kw)
        return {"done": r.get("done"), "window": (r.get("window") or {}).get("title")}

    def snap(self, window=None, interactive: bool = False, all: bool = False, max_nodes: int = 400) -> Snapshot:
        r = self.call("snapshot", window=window, interactive=interactive or None, all=all or None,
                      max_nodes=max_nodes, structured=True)
        sid = r["snapshot"]
        self.last = Snapshot([El(self, sid, it) for it in r.get("items", [])], sid, r["text"],
                             [w["title"] for w in r.get("windows", [])])
        return self.last

    def find(self, pattern=None, type=None, window=None, value=None, fresh: bool = False) -> list[El]:
        """Elements whose name (or value) matches pattern (regex, case-insensitive) and whose type is
        type. Takes a new snapshot if window is given, fresh=True, or none was taken yet."""
        if window is not None or fresh or self.last is None:
            self.snap(window)
        rx = re.compile(pattern, re.I) if pattern else None
        rv = re.compile(value, re.I) if value else None
        tp = type.lower() if type else None
        out = []
        for e in self.last:
            if tp and e.type.lower() != tp:
                continue
            if rx and not (rx.search(e.name) or (e.value and rx.search(str(e.value)))):
                continue
            if rv and not (e.value is not None and rv.search(str(e.value))):
                continue
            out.append(e)
        return out

    def find1(self, pattern=None, type=None, window=None, value=None, fresh: bool = False) -> El:
        found = self.find(pattern, type, window, value, fresh)
        if not found:
            what = " ".join(x for x in (f"type={type}" if type else "", f"/{pattern}/" if pattern else "",
                                        f"value=/{value}/" if value else "") if x)
            raise ActionError(f"no element matches {what or 'anything'} in snapshot {self.last.id}")
        return found[0]

    def _target(self, target):
        if isinstance(target, El):
            return {"ref": target.ref, "snap": target.snap}
        if isinstance(target, str) and re.fullmatch(r"@?e\d+", target):
            return {"ref": target.lstrip("@")}
        return None

    def click(self, target=None, y=None, *, right=False, double=False, abs=False, bg=False, force=False,
              shot=None) -> str:
        """click(el) / click("@e12") act on an element (background first); click(x, y) or click((x, y))
        is a real mouse click on a point of the last screenshot (abs=True: screen pixels)."""
        t = self._target(target)
        if t:
            return _via(self.call("element", action="click", bg=bg or None, force=force or None, **t))
        x, y = (target if y is None else (target, y))
        r = self.call("click", x=x, y=y, button="right" if right else None, count=2 if double else None,
                      abs=abs or None, force=force or None, shot=shot)
        return f'clicked {r["clicked"][0]},{r["clicked"][1]}'

    def fill(self, target, text: str, bg: bool = False, force: bool = False) -> str:
        """Put text into a field (or choose a combo box item) in the background when possible."""
        t = self._target(target)
        if not t:
            raise ActionError("fill() needs an element or a ref like '@e5'")
        return _via(self.call("element", action="set", text=text, bg=bg or None, force=force or None, **t))

    def write(self, text: str, enter: bool = False, force: bool = False) -> str:
        """Type with the real keyboard into the focused window (any language)."""
        r = self.call("type", text=text, enter=enter or None, force=force or None)
        return f'typed {r.get("typed_chars")} chars'

    def key(self, *combos: str, force: bool = False) -> str:
        self.call("key", keys=list(combos), force=force or None)
        return "pressed " + " ".join(combos)

    def scroll(self, direction: str = "down", amount: int = 3, at=None, force: bool = False) -> str:
        p = {"dy": amount if direction == "up" else -amount if direction == "down" else 0,
             "dx": amount if direction == "right" else -amount if direction == "left" else 0}
        if at:
            p.update(x=at[0], y=at[1])
        self.call("scroll", force=force or None, **p)
        return f"scrolled {direction} {amount}"

    def wait_for(self, pattern=None, window=None, type=None, gone: bool = False, timeout: float = 10,
                 every: float = 0.5):
        """Wait until an element matching pattern/type exists in window (re-snapshotting), or with
        gone=True until it is gone. Without pattern/type: wait for the window itself."""
        if pattern is None and type is None:
            r = self.call("wait", window=window, gone=gone or None, timeout=timeout)
            if not r.get("matched"):
                raise ActionError(f"timed out after {timeout:g}s waiting for window {window!r}"
                                  + (" to close" if gone else ""))
            return (r.get("window") or {}).get("title")
        end = time.monotonic() + timeout
        while True:
            try:
                found = self.find(pattern, type, window=window, fresh=True)
            except ActionError:
                found = []
            if bool(found) != gone:
                return None if gone else found[0]
            if time.monotonic() > end:
                raise ActionError(f"timed out after {timeout:g}s waiting for {pattern or type!r}"
                                  + (" to disappear" if gone else ""))
            self.sleep(every)

    def launch(self, target: str, *args: str, no_activate: bool = False, no_console: bool = False,
               new: bool = False, admin: bool = False, wait: float | None = None) -> str:
        """Open an app by its Start/taskbar name, a link, a file or a program, as the desktop user.
        An app that is already open is brought forward (new=True starts another copy). Returns the
        title of the window that opened (or was reused), or of the foreground window."""
        r = self.call("launch", target=target, args=list(args) or None, wait=wait,
                      show="noactivate" if no_activate else None, no_console=no_console or None,
                      new=new or None, admin=admin or None)
        w = r.get("window") or (r.get("opened") or [None])[0] or r.get("foreground") or {}
        return w.get("title") or ""

    def apps(self, query: str = "") -> list[dict]:
        """Apps Start, the taskbar and the desktop offer, matching `query`: name, source, exe, id."""
        return self.call("apps", query=query)["apps"]

    def shot(self, window=None, display=None, region=None, bg: bool = False, max_edge: int = 1280) -> dict:
        """Screenshot; the image is returned with the script's result. Returns its id and geometry."""
        if len(self.images) >= MAX_IMAGES:
            raise ActionError(f"at most {MAX_IMAGES} screenshots per script")
        r = self.call("screenshot", window=window, display=display, region=region, background=bg or None,
                      max_edge=max_edge)
        self.images.append({"shot": r["shot"], "format": r.get("format", "png"), "image": r["image"]})
        return {"shot": r["shot"], "size": (r["width"], r["height"]), "scale": r["scale"][0],
                "origin": tuple(r["origin"])}

    def run(self, command: str, shell: str = "powershell", timeout: float = 60) -> RunResult:
        return RunResult(self.call("run", command=command, shell=shell, timeout=timeout))

    def clip(self, text=None) -> str:
        if text is None:
            return self.call("clipboard")["text"]
        self.call("clipboard", text=text)
        return text

    def sleep(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while True:
            self.check_deadline()
            left = end - time.monotonic()
            if left <= 0:
                return
            time.sleep(min(left, 0.2))

    # ------------------------------------------------------------------- run
    def run_code(self, code: str, timeout: float = 120) -> dict:
        """Execute code in this session. Returns output, error (with the script line), images."""
        if not self.busy.acquire(blocking=False):
            return {"output": "", "error": "the previous script in this session is still running",
                    "images": [], "seconds": 0}
        self._out, self._out_len, self.images = [], 0, []
        t0 = time.monotonic()
        self.deadline = t0 + timeout
        self.ns["print"] = self._print
        box: dict = {}
        worker = threading.Thread(target=self._exec, args=(code, box), daemon=True)
        worker.start()
        worker.join(timeout + 5)
        if worker.is_alive():
            box.setdefault("error", f"script timed out after {timeout:g}s (still blocked in a Windows call; "
                                    "it stops at its next step)")
            threading.Thread(target=self._release_when_done, args=(worker,), daemon=True).start()
        else:
            self.busy.release()
        out = "".join(self._out)
        if self._out_len > MAX_OUTPUT:
            out += f"\n... [output truncated: {self._out_len} chars, showing {MAX_OUTPUT}]\n"
        return {"output": out, "error": box.get("error"), "images": list(self.images),
                "seconds": round(time.monotonic() - t0, 2)}

    def _release_when_done(self, worker):
        worker.join()
        self.busy.release()

    def _exec(self, code: str, box: dict) -> None:
        def tracer(frame, event, arg):
            if frame.f_code.co_filename != FILENAME:
                return None
            if time.monotonic() > self.deadline:
                raise ScriptTimeout("script timed out")
            return local

        def local(frame, event, arg):
            if event == "line" and time.monotonic() > self.deadline:
                raise ScriptTimeout("script timed out")
            return local

        sys.settrace(tracer)
        try:
            tree = ast.parse(code, FILENAME, "exec")
            last = None
            if tree.body and isinstance(tree.body[-1], ast.Expr):
                last = ast.Expression(tree.body.pop().value)
            exec(compile(tree, FILENAME, "exec"), self.ns)
            if last is not None:
                value = eval(compile(last, FILENAME, "eval"), self.ns)
                if value is not None:
                    self._print(repr(value) if not isinstance(value, str) else value)
        except SyntaxError as e:
            box["error"] = f"line {e.lineno}: syntax error: {e.msg}"
        except BaseException as e:  # report everything, including ScriptTimeout and SystemExit
            box["error"] = _describe(e, code)
        finally:
            sys.settrace(None)


def _describe(e: BaseException, code: str) -> str:
    """'line 4: click(x) -> ActionError: ...' using the deepest frame that is in the script."""
    lines = code.splitlines()
    lineno = None
    for frame, n in traceback.walk_tb(e.__traceback__):
        if frame.f_code.co_filename == FILENAME:
            lineno = n
    where = ""
    if lineno is not None:
        src = lines[lineno - 1].strip() if 0 < lineno <= len(lines) else ""
        where = f"line {lineno}: {src}\n" if src else f"line {lineno}: "
    if isinstance(e, ScriptTimeout):
        return where + "ScriptTimeout: the script ran past its time limit"
    return f"{where}{type(e).__name__}: {e}"


class Engine:
    """Named sessions over one dispatcher."""

    def __init__(self, dispatch):
        self.dispatch = dispatch
        self.sessions: dict[str, Session] = {}
        self.lock = threading.Lock()

    def run(self, code: str, session: str = "default", reset: bool = False, timeout: float = 120) -> dict:
        with self.lock:
            if reset or session not in self.sessions:
                self.sessions[session] = Session(self.dispatch, session)
            s = self.sessions[session]
        r = s.run_code(code, timeout=timeout)
        r["session"] = session
        return r
