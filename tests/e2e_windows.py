#!/usr/bin/env python3
"""End-to-end tests against the real PC, using a throwaway test window.

    python3 tests/e2e_windows.py            background only: never moves the pointer or takes focus
    python3 tests/e2e_windows.py --input    also real mouse/keyboard input. Borrows the pointer and focus
                                           for a few seconds; refuses to run unless the PC has been idle.
"""
import importlib.machinery
import importlib.util
import json
import os
import re
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_loader = importlib.machinery.SourceFileLoader("winuse_cli", os.path.join(REPO, "bin", "winuse"))
cli = importlib.util.module_from_spec(importlib.util.spec_from_loader("winuse_cli", _loader))
_loader.exec_module(cli)

PAD = "winuse-testpad"
results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(f'{"PASS" if ok else "FAIL"}  {name}' + (f"  ({detail})" if detail else ""))
    return ok


def call(method, **params):
    r = cli.call(method, params)
    if not r.get("ok"):
        raise RuntimeError(f'{method}: {r.get("error")}: {r.get("message")}')
    return r


def pad_state():
    r = call("run", command=r"Get-Content -Raw -Encoding UTF8 C:\winuse\logs\testpad.json")
    return json.loads(r["stdout"])


def wait_state(pred, timeout=5):
    deadline = time.time() + timeout
    while True:
        st = pad_state()
        if pred(st) or time.time() > deadline:
            return st
        time.sleep(0.3)


def ref_of(text, kind, name):
    m = re.search(rf'@(e\d+) {kind} "{re.escape(name)}"', text)
    return m.group(1) if m else None


def open_pad(quiet):
    args = ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", r"C:\winuse\host\testpad.ps1", "-X", "700", "-Y", "500"]
    call("launch", target="powershell", args=args + (["-Quiet"] if quiet else []), no_console=True, wait=0.5)
    return call("wait", window=PAD, timeout=20)["matched"]


def close_pad():
    try:
        call("window", window=PAD, action="close")
        call("wait", window=PAD, gone=True, timeout=5)
    except RuntimeError:
        pass


def background_tests():
    before = call("status")
    fg0, cur0 = before["foreground"]["hwnd"] if before["foreground"] else None, before["cursor"]

    def undisturbed(label):
        st = call("status")
        fg = st["foreground"]["hwnd"] if st["foreground"] else None
        check(f"{label}: foreground window and pointer untouched", fg == fg0 and st["cursor"] == cur0,
              f'foreground {fg0}->{fg}, pointer {cur0}->{st["cursor"]}')

    check("open the test window behind the others", open_pad(quiet=True))
    undisturbed("after opening")

    snap = call("snapshot", window=PAD)
    text = snap["text"]
    refs = {k: ref_of(text, kind, name) for k, kind, name in (
        ("button", "Button", "Press me"), ("edit", "Edit", "Input box"),
        ("check", "CheckBox", "Enable option"), ("combo", "ComboBox", "Color"))}
    check("snapshot finds button, text box, checkbox and combo box", all(refs.values()),
          f'{snap["elements"]} elements in {snap["ms"]} ms; refs {refs}')
    if not all(refs.values()):
        print(text)
        return

    r = call("element", ref=refs["button"], action="click", bg=True)
    check("click the button in the background", wait_state(lambda s: s["clicks"] == 1)["clicks"] == 1, f'via {r["via"]}')

    wanted = "hello 你好 winuse"
    r = call("element", ref=refs["edit"], action="set", text=wanted, bg=True)
    check("fill the text box in the background (Chinese included)", wait_state(lambda s: s["text"] == wanted)["text"] == wanted,
          f'via {r["via"]}')

    r = call("element", ref=refs["check"], action="click", bg=True)
    check("tick the checkbox in the background", wait_state(lambda s: s["checked"])["checked"] is True, f'via {r["via"]}')

    # A classic combo box has no background route (setting its value does not notify the app),
    # so with bg it must refuse instead of pretending. The real-input part covers the fallback.
    r = cli.call("element", {"ref": refs["combo"], "action": "set", "text": "Green", "bg": True})
    check("classic combo box refuses a background-only set", r.get("error") == "bad_request", r.get("message", ""))

    shot = call("screenshot", window=PAD, background=True)
    path = cli.save_shot(shot, None)
    check("capture the covered window", not shot["blank"] and shot["width"] > 300, f'{shot["width"]}x{shot["height"]} {path}')
    undisturbed("after all background actions")
    check("the test window never became the active window", pad_state()["active"] is False)


def input_tests():
    st = call("status")
    if st["user_idle_seconds"] < 180:
        print(f'SKIP  real-input tests: the PC was used {st["user_idle_seconds"]}s ago (need 180s idle)')
        return
    fg0, cur0 = st["foreground"]["hwnd"] if st["foreground"] else None, st["cursor"]
    try:
        check("open the test window in front", open_pad(quiet=False))
        # Target points come from winuse's accessibility rectangles (physical pixels), not from the
        # test window's own WinForms coordinates, which Windows virtualizes when scaling isn't 100%.
        text = call("snapshot", window=PAD)["text"]
        edit_pt = call("element", ref=ref_of(text, "Edit", "Input box"), action="point")["point"]
        button_pt = call("element", ref=ref_of(text, "Button", "Press me"), action="point")["point"]
        call("click", x=edit_pt[0], y=edit_pt[1], abs=True)
        call("type", text="real 输入 123")
        check("real click + typing reach the text box", wait_state(lambda s: s["text"] == "real 输入 123")["text"] == "real 输入 123")
        # click the button through screenshot coordinates: the path an agent uses with pixels
        shot = call("screenshot")
        ox, oy = shot["origin"]; sx, sy = shot["scale"]
        call("click", x=int((button_pt[0] - ox) / sx), y=int((button_pt[1] - oy) / sy), shot=shot["shot"])
        check("real click at screenshot coordinates presses the button",
              wait_state(lambda s: s["clicks"] == 1)["clicks"] == 1, f'scale {sx:.3g}')
        combo = ref_of(call("snapshot", window=PAD)["text"], "ComboBox", "Color")
        r = call("element", ref=combo, action="set", text="Blue")
        check("classic combo box chosen with borrowed mouse and keyboard",
              wait_state(lambda s: s["color"] == "Blue")["color"] == "Blue", f'via {r["via"]}')
        call("key", keys=["f5"])
        check("key press arrives", "F5" in wait_state(lambda s: "F5" in s["keys"])["keys"])
        shot = call("screenshot", window=PAD)
        check("screen capture of the window", not shot["blank"], cli.save_shot(shot, None))
    finally:
        close_pad()
        call("move", x=cur0[0], y=cur0[1], abs=True, force=True)
        if fg0:
            call("window", window=fg0, action="focus")
        st = call("status")
        check("focus and pointer put back", (st["foreground"] or {}).get("hwnd") == fg0 and st["cursor"] == cur0)


def main():
    try:
        close_pad()
        background_tests()
    finally:
        close_pad()
    if "--input" in sys.argv:
        input_tests()
    print(f"\n{sum(results)}/{len(results)} checks passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
