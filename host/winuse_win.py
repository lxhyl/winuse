"""Windows desktop primitives for the winuse host: capture, input, windows, clipboard.

Runs inside the logged-on user's desktop session. Standard user32/GDI calls only.
"""
from __future__ import annotations

import ctypes
import io
import subprocess
import time
from ctypes import wintypes as wt

from PIL import Image, ImageGrab

import winuse_core as core

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi")
wtsapi32 = ctypes.WinDLL("wtsapi32", use_last_error=True)

ULONG_PTR = ctypes.c_size_t


# ---------------------------------------------------------------- prototypes
def _proto(fn, restype, *argtypes):
    fn.restype = restype
    fn.argtypes = list(argtypes)


_proto(user32.GetSystemMetrics, ctypes.c_int, ctypes.c_int)
_proto(user32.GetForegroundWindow, wt.HWND)
_proto(user32.GetWindowTextLengthW, ctypes.c_int, wt.HWND)
_proto(user32.GetWindowTextW, ctypes.c_int, wt.HWND, wt.LPWSTR, ctypes.c_int)
_proto(user32.GetClassNameW, ctypes.c_int, wt.HWND, wt.LPWSTR, ctypes.c_int)
_proto(user32.GetWindowThreadProcessId, wt.DWORD, wt.HWND, ctypes.POINTER(wt.DWORD))
_proto(user32.IsWindow, wt.BOOL, wt.HWND)
_proto(user32.IsWindowVisible, wt.BOOL, wt.HWND)
_proto(user32.IsIconic, wt.BOOL, wt.HWND)
_proto(user32.IsZoomed, wt.BOOL, wt.HWND)
_proto(user32.GetWindowRect, wt.BOOL, wt.HWND, ctypes.POINTER(wt.RECT))
_proto(user32.GetWindowLongW, wt.LONG, wt.HWND, ctypes.c_int)
_proto(user32.ShowWindow, wt.BOOL, wt.HWND, ctypes.c_int)
_proto(user32.SetForegroundWindow, wt.BOOL, wt.HWND)
_proto(user32.BringWindowToTop, wt.BOOL, wt.HWND)
_proto(user32.SetWindowPos, wt.BOOL, wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
       ctypes.c_int, ctypes.c_int, wt.UINT)
_proto(user32.PostMessageW, wt.BOOL, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
_proto(user32.AttachThreadInput, wt.BOOL, wt.DWORD, wt.DWORD, wt.BOOL)
_proto(user32.SetCursorPos, wt.BOOL, ctypes.c_int, ctypes.c_int)
_proto(user32.GetCursorPos, wt.BOOL, ctypes.POINTER(wt.POINT))
_proto(user32.MapVirtualKeyW, wt.UINT, wt.UINT, wt.UINT)
_proto(user32.OpenInputDesktop, wt.HANDLE, wt.DWORD, wt.BOOL, wt.DWORD)
_proto(user32.CloseDesktop, wt.BOOL, wt.HANDLE)
_proto(user32.GetUserObjectInformationW, wt.BOOL, wt.HANDLE, ctypes.c_int, ctypes.c_void_p,
       wt.DWORD, ctypes.POINTER(wt.DWORD))
_proto(user32.OpenClipboard, wt.BOOL, wt.HWND)
_proto(user32.CloseClipboard, wt.BOOL)
_proto(user32.EmptyClipboard, wt.BOOL)
_proto(user32.GetClipboardData, wt.HANDLE, wt.UINT)
_proto(user32.SetClipboardData, wt.HANDLE, wt.UINT, wt.HANDLE)
_proto(kernel32.GlobalAlloc, wt.HGLOBAL, wt.UINT, ctypes.c_size_t)
_proto(kernel32.GlobalLock, ctypes.c_void_p, wt.HGLOBAL)
_proto(kernel32.GlobalUnlock, wt.BOOL, wt.HGLOBAL)
_proto(kernel32.GlobalFree, wt.HGLOBAL, wt.HGLOBAL)
_proto(kernel32.GetCurrentThreadId, wt.DWORD)
_proto(kernel32.GetCurrentProcessId, wt.DWORD)
_proto(kernel32.GetTickCount, wt.DWORD)
_proto(kernel32.OpenProcess, wt.HANDLE, wt.DWORD, wt.BOOL, wt.DWORD)
_proto(kernel32.CloseHandle, wt.BOOL, wt.HANDLE)
_proto(kernel32.QueryFullProcessImageNameW, wt.BOOL, wt.HANDLE, wt.DWORD, wt.LPWSTR,
       ctypes.POINTER(wt.DWORD))
_proto(kernel32.ProcessIdToSessionId, wt.BOOL, wt.DWORD, ctypes.POINTER(wt.DWORD))
_proto(shell32.ShellExecuteW, wt.HINSTANCE, wt.HWND, wt.LPCWSTR, wt.LPCWSTR, wt.LPCWSTR,
       wt.LPCWSTR, ctypes.c_int)
_proto(shell32.IsUserAnAdmin, wt.BOOL)
_proto(dwmapi.DwmGetWindowAttribute, ctypes.c_long, wt.HWND, wt.DWORD, ctypes.c_void_p, wt.DWORD)
_proto(wtsapi32.WTSQuerySessionInformationW, wt.BOOL, wt.HANDLE, wt.DWORD, ctypes.c_int,
       ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wt.DWORD))
_proto(wtsapi32.WTSFreeMemory, None, ctypes.c_void_p)

gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
_proto(user32.GetWindowDC, wt.HDC, wt.HWND)
_proto(user32.ReleaseDC, ctypes.c_int, wt.HWND, wt.HDC)
_proto(user32.PrintWindow, wt.BOOL, wt.HWND, wt.HDC, wt.UINT)
_proto(gdi32.CreateCompatibleDC, wt.HDC, wt.HDC)
_proto(gdi32.CreateCompatibleBitmap, wt.HBITMAP, wt.HDC, ctypes.c_int, ctypes.c_int)
_proto(gdi32.SelectObject, wt.HGDIOBJ, wt.HDC, wt.HGDIOBJ)
_proto(gdi32.DeleteObject, wt.BOOL, wt.HGDIOBJ)
_proto(gdi32.DeleteDC, wt.BOOL, wt.HDC)
_proto(gdi32.GetDIBits, ctypes.c_int, wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT, ctypes.c_void_p,
       ctypes.c_void_p, wt.UINT)

WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
_proto(user32.EnumWindows, wt.BOOL, WNDENUMPROC, wt.LPARAM)


# --------------------------------------------------------------- input types
class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
                ("dwFlags", wt.DWORD), ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wt.DWORD), ("wParamL", wt.WORD), ("wParamH", wt.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wt.DWORD), ("u", _INPUTUNION)]


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.UINT), ("dwTime", wt.DWORD)]


_proto(user32.SendInput, wt.UINT, wt.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
_proto(user32.GetLastInputInfo, wt.BOOL, ctypes.POINTER(LASTINPUTINFO))

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x1, 0x2, 0x4
MOUSEEVENTF_MOVE, MOUSEEVENTF_ABSOLUTE, MOUSEEVENTF_VIRTUALDESK = 0x1, 0x8000, 0x4000
MOUSEEVENTF_WHEEL, MOUSEEVENTF_HWHEEL = 0x0800, 0x1000
_BUTTON_FLAGS = {"left": (0x2, 0x4), "right": (0x8, 0x10), "middle": (0x20, 0x40)}


class InputBlocked(RuntimeError):
    """SendInput inserted nothing: the input desktop is not ours (lock screen, UAC prompt)."""


# -------------------------------------------------------------------- screen
try:
    shcore = ctypes.WinDLL("shcore")
    _proto(shcore.GetDpiForMonitor, ctypes.c_long, wt.HMONITOR, ctypes.c_int,
           ctypes.POINTER(wt.UINT), ctypes.POINTER(wt.UINT))
except (OSError, AttributeError):
    shcore = None


class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT),
                ("dwFlags", wt.DWORD), ("szDevice", wt.WCHAR * 32)]


MONITORENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HMONITOR, wt.HDC, ctypes.POINTER(wt.RECT), wt.LPARAM)
_proto(user32.EnumDisplayMonitors, wt.BOOL, wt.HDC, ctypes.POINTER(wt.RECT), MONITORENUMPROC, wt.LPARAM)
_proto(user32.GetMonitorInfoW, wt.BOOL, wt.HMONITOR, ctypes.POINTER(MONITORINFOEXW))


def init_dpi() -> str:
    """Make this process per-monitor DPI aware (v2), so every coordinate it sees or sends (screenshots,
    clicks, window and element rectangles) is in physical pixels on every monitor, whatever each
    monitor's scaling. Returns the awareness actually in effect, which status reports."""
    try:
        _proto(user32.SetProcessDpiAwarenessContext, wt.BOOL, ctypes.c_void_p)
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
    except (OSError, AttributeError):
        try:
            shcore.SetProcessDpiAwareness(2)  # Windows 8.1 fallback: per-monitor (v1)
        except (OSError, AttributeError):
            pass
    return dpi_awareness()


def dpi_awareness() -> str:
    try:
        _proto(user32.GetThreadDpiAwarenessContext, ctypes.c_void_p)
        _proto(user32.AreDpiAwarenessContextsEqual, wt.BOOL, ctypes.c_void_p, ctypes.c_void_p)
        _proto(user32.GetAwarenessFromDpiAwarenessContext, ctypes.c_int, ctypes.c_void_p)
        ctx = user32.GetThreadDpiAwarenessContext()
        if user32.AreDpiAwarenessContextsEqual(ctx, ctypes.c_void_p(-4)):
            return "per-monitor-v2"
        return {0: "unaware", 1: "system", 2: "per-monitor"}.get(
            user32.GetAwarenessFromDpiAwarenessContext(ctx), "unknown")
    except (OSError, AttributeError):
        return "unknown"


def ensure_thread_dpi() -> None:
    """Make the calling thread per-monitor DPI aware if the process could not be (an exe manifest can
    pin a process to system awareness, which would scale coordinates on non-100% monitors)."""
    if dpi_awareness() in ("per-monitor-v2", "per-monitor"):
        return
    try:
        _proto(user32.SetThreadDpiAwarenessContext, ctypes.c_void_p, ctypes.c_void_p)
        user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    except (OSError, AttributeError):
        pass


def _monitor_dpi(hmon) -> int:
    if shcore is not None:
        x, y = wt.UINT(), wt.UINT()
        if shcore.GetDpiForMonitor(hmon, 0, ctypes.byref(x), ctypes.byref(y)) == 0:  # MDT_EFFECTIVE_DPI
            return int(x.value)
    return 96


def monitors() -> list[dict]:
    """Monitors in physical pixels: index (1 = primary, then left to right), rect and work area as
    [left, top, right, bottom], effective DPI and scale (1.25 = 125%)."""
    found: list[dict] = []

    def cb(hmon, _hdc, _rect, _lparam):
        mi = MONITORINFOEXW()
        mi.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            r, w = mi.rcMonitor, mi.rcWork
            dpi = _monitor_dpi(hmon)
            found.append({"rect": [r.left, r.top, r.right, r.bottom], "work": [w.left, w.top, w.right, w.bottom],
                          "primary": bool(mi.dwFlags & 1), "device": mi.szDevice,
                          "dpi": dpi, "scale": round(dpi / 96, 2)})
        return True

    user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(cb), 0)
    found.sort(key=lambda m: (not m["primary"], m["rect"][0], m["rect"][1]))
    for i, m in enumerate(found, 1):
        m["index"] = i
    return found


def virtual_screen() -> tuple[int, int, int, int]:
    """(x, y, w, h) of the whole desktop in physical pixels: the union of all monitors."""
    mons = monitors()
    if mons:
        l, t, r, b = core.union_rect(m["rect"] for m in mons)
        return l, t, r - l, b - t
    gm = user32.GetSystemMetrics
    return gm(76), gm(77), gm(78), gm(79)


_shots: dict[str, core.ShotMap] = {}
_shot_seq = 0
_last_shot: str | None = None


def shot_map(shot_id: str | None = None) -> core.ShotMap:
    sid = shot_id or _last_shot
    if sid is None:
        raise ValueError("no screenshot yet: take one first, or pass absolute coordinates")
    if sid not in _shots:
        raise ValueError(f"unknown screenshot id {sid!r}")
    return _shots[sid]


def to_screen(x: float, y: float, absolute: bool = False, shot: str | None = None) -> tuple[int, int]:
    """Screenshot-space point -> physical screen point."""
    if absolute:
        return int(x), int(y)
    return shot_map(shot).to_screen(x, y)


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD), ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


def capture_window(hwnd) -> tuple[Image.Image, tuple[int, int, int, int]]:
    """Render one window off-screen (PrintWindow), so it works while the window is covered
    by others or sits outside the monitors. Returns (image, visible bounds on screen)."""
    if user32.IsIconic(hwnd):
        raise ValueError("the window is minimized; restore it first (window ... restore)")
    full = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(full))
    w, h = full.right - full.left, full.bottom - full.top
    if w <= 0 or h <= 0:
        raise ValueError("the window has no area")
    hdc_win = user32.GetWindowDC(hwnd)
    hdc_mem = gdi32.CreateCompatibleDC(hdc_win)
    bmp = gdi32.CreateCompatibleBitmap(hdc_win, w, h)
    try:
        old = gdi32.SelectObject(hdc_mem, bmp)
        ok = user32.PrintWindow(hwnd, hdc_mem, 2)  # PW_RENDERFULLCONTENT
        gdi32.SelectObject(hdc_mem, old)
        if not ok:
            raise OSError("PrintWindow failed")
        header = BITMAPINFOHEADER(biSize=ctypes.sizeof(BITMAPINFOHEADER), biWidth=w, biHeight=-h,
                                  biPlanes=1, biBitCount=32)
        buf = ctypes.create_string_buffer(w * h * 4)
        if not gdi32.GetDIBits(hdc_mem, bmp, 0, h, buf, ctypes.byref(header), 0):
            raise OSError("GetDIBits failed")
        img = Image.frombuffer("RGB", (w, h), buf, "raw", "BGRX", 0, 1).copy()
    finally:
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(hwnd, hdc_win)
    l, t, r, b = window_rect(hwnd)  # drop the invisible resize border
    img = img.crop((max(0, l - full.left), max(0, t - full.top), min(w, r - full.left), min(h, b - full.top)))
    return img, (l, t, r, b)


def screenshot(window=None, region=None, region_abs=False, max_edge=1280, fmt="png", quality=80,
               background=False, display=None) -> dict:
    global _shot_seq, _last_shot
    vx, vy, vw, vh = virtual_screen()
    kind = "screen"
    used_display = None
    img = None
    if background:
        img, bbox = capture_window(resolve_window(window))
        kind = "window-background"
    elif region:
        x, y, w, h = region
        bbox = (x, y, x + w, y + h) if region_abs else shot_map().rect_to_screen(x, y, w, h)
        kind = "region"
    elif window:
        bbox = window_rect(resolve_window(window))
        kind = "window"
    else:
        fg = user32.GetForegroundWindow()
        bbox, used_display = core.capture_rect(monitors(), core.parse_display(display),
                                               window_rect(fg) if fg else None)
        kind = "display" if used_display != "all" else "screen"
    if img is None:
        bbox = (max(bbox[0], vx), max(bbox[1], vy), min(bbox[2], vx + vw), min(bbox[3], vy + vh))
        if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
            raise ValueError(f"capture area {bbox} is empty or off screen")
        img = ImageGrab.grab(bbox=bbox, all_screens=True)
    w, h = img.size
    blank = img.getbbox() is None
    nw, nh, sx, sy = core.fit_size(w, h, max_edge)
    if (nw, nh) != (w, h):
        img = img.resize((nw, nh), Image.LANCZOS)
    buf = io.BytesIO()
    if fmt == "jpeg":
        img.convert("RGB").save(buf, "JPEG", quality=quality)
    else:
        img.save(buf, "PNG")
    _shot_seq += 1
    sid = f"s{_shot_seq}"
    _shots[sid] = core.ShotMap(bbox[0], bbox[1], sx, sy, nw, nh)
    while len(_shots) > 50:
        _shots.pop(next(iter(_shots)))
    _last_shot = sid
    cur = cursor_pos()
    return {"shot": sid, "kind": kind, "display": used_display, "width": nw, "height": nh,
            "origin": [bbox[0], bbox[1]], "source_size": [w, h], "scale": [sx, sy],
            "blank": blank, "cursor": list(cur), "format": fmt, "image": buf.getvalue()}


# --------------------------------------------------------------------- input
def _send(inputs: list[INPUT]) -> int:
    if not inputs:
        return 0
    arr = (INPUT * len(inputs))(*inputs)
    ctypes.set_last_error(0)
    n = user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))
    if n == 0:
        raise InputBlocked(f"SendInput inserted 0 of {len(inputs)} events "
                           f"(error {ctypes.get_last_error()})")
    return n


def _mouse(flags: int, dx: int = 0, dy: int = 0, data: int = 0) -> INPUT:
    return INPUT(type=INPUT_MOUSE, mi=MOUSEINPUT(dx, dy, data & 0xFFFFFFFF, flags, 0, 0))


def _key(vk: int, down: bool) -> INPUT:
    flags = 0 if down else KEYEVENTF_KEYUP
    if vk in core.EXTENDED:
        flags |= KEYEVENTF_EXTENDEDKEY
    scan = user32.MapVirtualKeyW(vk, 0)
    return INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(vk, scan, flags, 0, 0))


def _unicode(unit: int, down: bool) -> INPUT:
    flags = KEYEVENTF_UNICODE | (0 if down else KEYEVENTF_KEYUP)
    return INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(0, unit, flags, 0, 0))


def cursor_pos() -> tuple[int, int]:
    pt = wt.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def move_to(x: int, y: int) -> tuple[int, int]:
    """Move the pointer to a physical screen point. Returns where it ended up."""
    vx, vy, vw, vh = virtual_screen()
    nx = int((x - vx) * 65535 / max(1, vw - 1))
    ny = int((y - vy) * 65535 / max(1, vh - 1))
    _send([_mouse(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, nx, ny)])
    user32.SetCursorPos(x, y)  # pin the exact pixel; the SendInput above produced the move event
    return cursor_pos()


def click(x: int, y: int, button: str = "left", count: int = 1, modifiers: list[int] | None = None) -> tuple[int, int]:
    if button not in _BUTTON_FLAGS:
        raise ValueError(f"unknown button {button!r}")
    down, up = _BUTTON_FLAGS[button]
    mods = modifiers or []
    pos = move_to(x, y)
    time.sleep(0.03)
    _send([_key(vk, True) for vk in mods])
    try:
        for i in range(max(1, count)):
            _send([_mouse(down)])
            time.sleep(0.02)
            _send([_mouse(up)])
            if i + 1 < count:
                time.sleep(0.08)
    finally:
        if mods:
            _send([_key(vk, False) for vk in reversed(mods)])
    return pos


def drag(x1: int, y1: int, x2: int, y2: int, button: str = "left", duration: float = 0.4) -> None:
    down, up = _BUTTON_FLAGS[button]
    move_to(x1, y1)
    time.sleep(0.05)
    _send([_mouse(down)])
    try:
        steps = max(4, int(duration / 0.015))
        for i in range(1, steps + 1):
            move_to(round(x1 + (x2 - x1) * i / steps), round(y1 + (y2 - y1) * i / steps))
            time.sleep(duration / steps)
        time.sleep(0.05)
    finally:
        _send([_mouse(up)])


def scroll(dx: int = 0, dy: int = 0, at: tuple[int, int] | None = None) -> None:
    """dy > 0 scrolls up, dy < 0 down; dx > 0 right. Units are wheel notches."""
    if at:
        move_to(*at)
        time.sleep(0.03)
    for _ in range(abs(dy)):
        _send([_mouse(MOUSEEVENTF_WHEEL, data=120 if dy > 0 else -120)])
        time.sleep(0.01)
    for _ in range(abs(dx)):
        _send([_mouse(MOUSEEVENTF_HWHEEL, data=120 if dx > 0 else -120)])
        time.sleep(0.01)


def press(vks: list[int]) -> None:
    for vk, is_down in core.key_events(vks):
        _send([_key(vk, is_down)])
        time.sleep(0.01)


def type_text(text: str, delay: float = 0.0) -> int:
    """Type text as Unicode key events, so it does not go through the IME or keyboard layout."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    sent = 0
    batch: list[INPUT] = []

    def flush():
        nonlocal sent, batch
        if batch:
            _send(batch)
            batch = []
            time.sleep(0.005)

    for ch in text:
        if ch in ("\n", "\t"):
            flush()
            vk = core.VK_RETURN if ch == "\n" else core.VK_TAB
            _send([_key(vk, True), _key(vk, False)])
            time.sleep(0.01)
        else:
            for unit in core.utf16_units(ch):
                batch += [_unicode(unit, True), _unicode(unit, False)]
            if delay or len(batch) >= 40:
                flush()
                if delay:
                    time.sleep(delay)
        sent += 1
    flush()
    return sent


def last_input_tick() -> int:
    """Tick count (ms, 32-bit) of the most recent mouse/keyboard input, injected or real."""
    info = LASTINPUTINFO(cbSize=ctypes.sizeof(LASTINPUTINFO))
    user32.GetLastInputInfo(ctypes.byref(info))
    return info.dwTime


def idle_seconds() -> float:
    info = LASTINPUTINFO(cbSize=ctypes.sizeof(LASTINPUTINFO))
    if not user32.GetLastInputInfo(ctypes.byref(info)):
        return -1.0
    return ((kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000.0


# ------------------------------------------------------------------- windows
def window_title(hwnd) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def window_class(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def window_pid(hwnd) -> int:
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


_proto(advapi32.OpenProcessToken, wt.BOOL, wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.HANDLE))
_proto(advapi32.GetTokenInformation, wt.BOOL, wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD,
       ctypes.POINTER(wt.DWORD))


def host_elevated() -> bool:
    return bool(shell32.IsUserAnAdmin())


def process_elevated(pid: int) -> bool | None:
    """True if the process runs elevated (as administrator), False if not, None if unknown."""
    h = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return None
    try:
        tok = wt.HANDLE()
        if not advapi32.OpenProcessToken(h, 0x0008, ctypes.byref(tok)):  # TOKEN_QUERY
            # A non-elevated caller is denied the token of an elevated process.
            return True if ctypes.get_last_error() == 5 else None
        try:
            elevated, size = wt.DWORD(), wt.DWORD()
            if advapi32.GetTokenInformation(tok, 20, ctypes.byref(elevated), 4, ctypes.byref(size)):  # TokenElevation
                return bool(elevated.value)
            return None
        finally:
            kernel32.CloseHandle(tok)
    finally:
        kernel32.CloseHandle(h)


def uipi_blocked(hwnd) -> bool:
    """True when the winuse host is not elevated but the window's program is. Windows (UIPI) then silently
    drops input and accessibility actions coming from the lower-privilege host."""
    if not hwnd or host_elevated():
        return False
    return process_elevated(window_pid(hwnd)) is True


def process_path(pid: int) -> str:
    h = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wt.DWORD(1024)
        return buf.value if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)) else ""
    finally:
        kernel32.CloseHandle(h)


def process_name(pid: int) -> str:
    return process_path(pid).rsplit("\\", 1)[-1]


def window_rect(hwnd) -> tuple[int, int, int, int]:
    """Visible bounds (left, top, right, bottom), without the invisible resize border."""
    r = wt.RECT()
    if dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(r), ctypes.sizeof(r)) != 0:
        user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def _is_cloaked(hwnd) -> bool:
    v = wt.DWORD()
    return dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(v), ctypes.sizeof(v)) == 0 and v.value != 0


def window_info(hwnd) -> dict:
    l, t, r, b = window_rect(hwnd)
    pid = window_pid(hwnd)
    return {"hwnd": int(hwnd or 0), "title": window_title(hwnd), "class": window_class(hwnd),
            "pid": pid, "process": process_name(pid), "rect": [l, t, r - l, b - t],
            "minimized": bool(user32.IsIconic(hwnd)), "maximized": bool(user32.IsZoomed(hwnd)),
            "foreground": int(user32.GetForegroundWindow() or 0) == int(hwnd or 0)}


def foreground() -> dict | None:
    hwnd = user32.GetForegroundWindow()
    return window_info(hwnd) if hwnd else None


def list_windows(include_all: bool = False) -> list[dict]:
    """Top-level windows in z-order (front first)."""
    found: list[int] = []

    def cb(hwnd, _lparam):
        found.append(hwnd)
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    out = []
    for hwnd in found:
        if not user32.IsWindowVisible(hwnd):
            continue
        if not include_all:
            if not window_title(hwnd) or _is_cloaked(hwnd):
                continue
            if user32.GetWindowLongW(hwnd, -20) & 0x80:  # WS_EX_TOOLWINDOW
                continue
        info = window_info(hwnd)
        if not include_all and not info["minimized"] and (info["rect"][2] <= 1 or info["rect"][3] <= 1):
            continue
        out.append(info)
    return out


def resolve_window(spec) -> int:
    """'active', an hwnd (int or '0x..'), or text matched against title and process name."""
    if spec in (None, "", "active", "foreground"):
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            raise ValueError("there is no foreground window")
        return hwnd
    if isinstance(spec, int) or (isinstance(spec, str) and (spec.isdigit() or spec.lower().startswith("0x"))):
        hwnd = spec if isinstance(spec, int) else int(spec, 0)
        if not user32.IsWindow(hwnd):
            raise ValueError(f"no window with handle {spec}")
        return hwnd
    needle = str(spec).lower()
    wins = list_windows()
    for w in wins:
        if needle == w["title"].lower() or needle == w["process"].lower() or needle + ".exe" == w["process"].lower():
            return w["hwnd"]
    for w in wins:
        if needle in w["title"].lower():
            return w["hwnd"]
    raise ValueError(f"no window matches {spec!r}")


def focus_window(hwnd) -> bool:
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    if user32.GetForegroundWindow() == hwnd:
        return True
    user32.SetForegroundWindow(hwnd)
    if user32.GetForegroundWindow() != hwnd:
        # Windows only lets the foreground thread hand over focus, so borrow its input queue.
        fg = user32.GetForegroundWindow()
        fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
        me = kernel32.GetCurrentThreadId()
        if fg_tid and fg_tid != me and user32.AttachThreadInput(me, fg_tid, True):
            try:
                user32.BringWindowToTop(hwnd)
                user32.SetForegroundWindow(hwnd)
            finally:
                user32.AttachThreadInput(me, fg_tid, False)
    if user32.GetForegroundWindow() != hwnd:
        # Last resort: a tap of Alt makes this process the last input source.
        try:
            press([core.VK_MENU])
        except InputBlocked:
            pass
        user32.SetForegroundWindow(hwnd)
    time.sleep(0.15)
    return user32.GetForegroundWindow() == hwnd


def window_action(hwnd, action: str, x=None, y=None, w=None, h=None) -> dict:
    SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE = 0x1, 0x2, 0x4, 0x10
    if action == "focus":
        ok = focus_window(hwnd)
    elif action in ("min", "minimize"):
        ok = bool(user32.ShowWindow(hwnd, 6)) or True
    elif action in ("max", "maximize"):
        ok = bool(user32.ShowWindow(hwnd, 3)) or True
    elif action == "restore":
        ok = bool(user32.ShowWindow(hwnd, 9)) or True
    elif action == "close":
        ok = bool(user32.PostMessageW(hwnd, 0x0010, 0, 0))  # WM_CLOSE
        time.sleep(0.2)
        return {"done": ok, "window": window_info(hwnd) if user32.IsWindow(hwnd) else None}
    elif action == "back":  # behind every other window, without activating anything
        ok = bool(user32.SetWindowPos(hwnd, 1, 0, 0, 0, 0, SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE))
    elif action == "move":
        ok = bool(user32.SetWindowPos(hwnd, None, int(x), int(y), 0, 0, SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE))
    elif action == "resize":
        ok = bool(user32.SetWindowPos(hwnd, None, 0, 0, int(w), int(h), SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE))
    else:
        raise ValueError(f"unknown window action {action!r}")
    time.sleep(0.1)
    return {"done": ok, "window": window_info(hwnd)}


# ------------------------------------------------------------- session state
class _WTSINFOEX_LEVEL1_W(ctypes.Structure):
    _fields_ = [("SessionId", wt.ULONG), ("SessionState", ctypes.c_int), ("SessionFlags", wt.LONG),
                ("WinStationName", wt.WCHAR * 33), ("UserName", wt.WCHAR * 21), ("DomainName", wt.WCHAR * 18),
                ("LogonTime", ctypes.c_longlong), ("ConnectTime", ctypes.c_longlong),
                ("DisconnectTime", ctypes.c_longlong), ("LastInputTime", ctypes.c_longlong),
                ("CurrentTime", ctypes.c_longlong), ("IncomingBytes", wt.DWORD), ("OutgoingBytes", wt.DWORD),
                ("IncomingFrames", wt.DWORD), ("OutgoingFrames", wt.DWORD),
                ("IncomingCompressedBytes", wt.DWORD), ("OutgoingCompressedBytes", wt.DWORD)]


class _WTSINFOEXW(ctypes.Structure):
    _fields_ = [("Level", wt.DWORD), ("Data", _WTSINFOEX_LEVEL1_W)]


def session_id() -> int:
    sid = wt.DWORD()
    kernel32.ProcessIdToSessionId(kernel32.GetCurrentProcessId(), ctypes.byref(sid))
    return sid.value


def session_locked() -> bool | None:
    """True/False from the session manager, None if it cannot tell."""
    buf, size = ctypes.c_void_p(), wt.DWORD()
    if not wtsapi32.WTSQuerySessionInformationW(None, session_id(), 25, ctypes.byref(buf), ctypes.byref(size)):
        return None
    try:
        flags = ctypes.cast(buf, ctypes.POINTER(_WTSINFOEXW)).contents.Data.SessionFlags
    finally:
        wtsapi32.WTSFreeMemory(buf)
    return {0: True, 1: False}.get(flags)


def input_desktop() -> str | None:
    """Name of the desktop that currently receives input ('Default' normally).
    None means it cannot be opened: lock screen or a UAC prompt on the secure desktop."""
    h = user32.OpenInputDesktop(0, False, 0x0001)
    if not h:
        return None
    try:
        buf = ctypes.create_unicode_buffer(256)
        need = wt.DWORD()
        user32.GetUserObjectInformationW(h, 2, buf, ctypes.sizeof(buf), ctypes.byref(need))
        return buf.value
    finally:
        user32.CloseDesktop(h)


def status() -> dict:
    mons = monitors()
    vx, vy, vw, vh = virtual_screen()
    desk = input_desktop()
    prim = next((m for m in mons if m["primary"]), None)
    return {"session": session_id(), "elevated": bool(shell32.IsUserAnAdmin()),
            "locked": session_locked(), "input_desktop": desk, "can_interact": desk == "Default",
            "dpi_awareness": dpi_awareness(),
            "screen": {"origin": [vx, vy], "size": [vw, vh],
                       "primary": ([prim["rect"][2] - prim["rect"][0], prim["rect"][3] - prim["rect"][1]] if prim
                                   else [user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)]),
                       "monitors": len(mons) or user32.GetSystemMetrics(80)},
            "displays": mons,
            "cursor": list(cursor_pos()), "idle_seconds": round(idle_seconds(), 1),
            "foreground": foreground()}


# ----------------------------------------------------------------- clipboard
def _open_clipboard():
    for _ in range(20):
        if user32.OpenClipboard(None):
            return
        time.sleep(0.05)
    raise RuntimeError("clipboard is busy")


def clipboard_get() -> str:
    _open_clipboard()
    try:
        h = user32.GetClipboardData(13)  # CF_UNICODETEXT
        if not h:
            return ""
        p = kernel32.GlobalLock(h)
        if not p:
            return ""
        try:
            return ctypes.wstring_at(p)
        finally:
            kernel32.GlobalUnlock(h)
    finally:
        user32.CloseClipboard()


def clipboard_set(text: str) -> None:
    data = ctypes.create_unicode_buffer(text)
    size = ctypes.sizeof(data)
    h = kernel32.GlobalAlloc(0x0002, size)  # GMEM_MOVEABLE
    if not h:
        raise MemoryError("GlobalAlloc failed")
    p = kernel32.GlobalLock(h)
    ctypes.memmove(p, data, size)
    kernel32.GlobalUnlock(h)
    _open_clipboard()
    try:
        user32.EmptyClipboard()
        if not user32.SetClipboardData(13, h):
            kernel32.GlobalFree(h)
            raise RuntimeError("SetClipboardData failed")
    finally:
        user32.CloseClipboard()


# ------------------------------------------------------------ launch and run
def launch(target: str, args: list[str] | None = None, cwd: str | None = None,
           show: str = "normal", no_console: bool = False) -> int | None:
    """Open a program, document or URL on the desktop, the way Start > Run would.

    show: 'normal', 'noactivate' (appear without taking focus) or 'minimized'.
    no_console: start a console program (a script host, say) with no console window."""
    if no_console:
        p = subprocess.Popen([target] + list(args or []), cwd=cwd, creationflags=0x08000000,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return p.pid
    if show not in ("normal", "noactivate", "minimized"):
        raise ValueError(f"unknown show mode {show!r}")
    params = subprocess.list2cmdline(args) if args else None
    rc = shell32.ShellExecuteW(None, "open", target, params, cwd, {"normal": 1, "noactivate": 4, "minimized": 7}[show])
    if (rc or 0) <= 32:
        raise OSError(f"could not launch {target!r} (ShellExecute code {rc})")
    return None


def run_command(command: str, shell: str = "powershell", timeout: float = 60, cwd: str | None = None) -> dict:
    if shell == "powershell":
        argv = ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                "[Console]::OutputEncoding=[Text.Encoding]::UTF8; " + command]
        enc = "utf-8"
    elif shell == "cmd":
        argv = ["cmd", "/d", "/c", command]
        enc = "mbcs"
    else:
        raise ValueError(f"unknown shell {shell!r}")
    limit = 20000
    try:
        p = subprocess.run(argv, capture_output=True, timeout=timeout, cwd=cwd,
                           stdin=subprocess.DEVNULL, creationflags=0x08000000)  # CREATE_NO_WINDOW
    except subprocess.TimeoutExpired as e:
        return {"timed_out": True, "exit_code": None,
                "stdout": (e.stdout or b"").decode(enc, "replace")[-limit:],
                "stderr": (e.stderr or b"").decode(enc, "replace")[-limit:]}
    return {"timed_out": False, "exit_code": p.returncode,
            "stdout": p.stdout.decode(enc, "replace")[-limit:],
            "stderr": p.stderr.decode(enc, "replace")[-limit:]}
