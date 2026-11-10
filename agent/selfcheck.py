"""On-box diagnostic: python selfcheck.py

Checks that the Windows API bindings load and reports what this process can see.
Run it over SSH to compare session 0 with what the agent reports from the desktop session.
"""
import ctypes
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

import winuse_core as core  # noqa: E402
import winuse_win as win  # noqa: E402

assert ctypes.sizeof(win.INPUT) == 40, f"INPUT is {ctypes.sizeof(win.INPUT)} bytes, expected 40 on x64"
print("winuse", core.VERSION, "python", sys.version.split()[0])
print("dpi:", win.init_dpi())
st = win.status()
print("status:", json.dumps(st, ensure_ascii=False))
print("windows visible:", len(win.list_windows()))
try:
    shot = win.screenshot()
    print(f"screenshot: {shot['width']}x{shot['height']} blank={shot['blank']} bytes={len(shot['image'])}")
except Exception as e:
    print(f"screenshot failed: {type(e).__name__}: {e}")
print("OK: bindings load" + ("" if st["session"] else " (session 0: no desktop here, as expected over SSH)"))
