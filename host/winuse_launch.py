"""Open programs the way the person at the PC would.

The host usually runs elevated, and whatever it starts itself inherits that admin token: the
program runs in a different security context from everything else on the desktop (Chrome, for
one, restarts itself and picks a profile on its own; drag and drop from the user's apps stops
working). So winuse hands every launch to Explorer: the program starts from explorer.exe with
the desktop user's normal rights and environment, exactly as a click in Start or on the taskbar
would start it. This is Raymond Chen's "launch an unelevated process from an elevated one":
find the desktop's shell view, take its Shell.Application object (which lives in explorer.exe)
and call ShellExecute on it.

App names are looked up the way a person finds an app: taskbar pins first, then Start (every
installed app, Win32 or Store, under the name Start shows), then desktop shortcuts. When the app
already has a window, that window is brought forward instead of starting a second copy.

Which account or profile an app opens is the app's own business; the agent checks the screen.
"""
from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import threading
import time
import winreg
from ctypes import c_uint, c_void_p
from ctypes import wintypes as wt

import comtypes
import comtypes.client
from comtypes import GUID, HRESULT, POINTER, STDMETHOD, IUnknown
from comtypes.automation import IDispatch, VARIANT

import winuse_core as core
import winuse_win as win

_tls = threading.local()
_cache: dict = {"at": 0.0, "apps": []}
_cache_lock = threading.Lock()
CACHE_SECONDS = 60

SHOW = {"normal": 1, "noactivate": 4, "minimized": 7}
CLSID_ShellWindows = GUID("{9BA05972-F6A8-11CF-A442-00A0C90A8F39}")
SID_STopLevelBrowser = GUID("{4C96BE40-915C-11CF-99D3-00AA004AE837}")
SWC_DESKTOP, SWFO_NEEDDISPATCH, SVGIO_BACKGROUND = 8, 1, 0
CSIDL_DESKTOP, CSIDL_COMMON_DESKTOPDIRECTORY = 0, 0x19

kernel32 = win.kernel32
user32 = win.user32
_shell32 = ctypes.WinDLL("shell32")
_ole32 = ctypes.WinDLL("ole32")
kernel32.GetApplicationUserModelId.argtypes = [wt.HANDLE, ctypes.POINTER(ctypes.c_uint32), wt.LPWSTR]
kernel32.GetApplicationUserModelId.restype = wt.LONG
user32.EnumChildWindows.argtypes = [wt.HWND, win.WNDENUMPROC, wt.LPARAM]
user32.EnumChildWindows.restype = wt.BOOL
_shell32.SHGetKnownFolderPath.argtypes = [ctypes.POINTER(GUID), wt.DWORD, wt.HANDLE, ctypes.POINTER(ctypes.c_wchar_p)]
_shell32.SHGetKnownFolderPath.restype = HRESULT


class ShellUnavailable(RuntimeError):
    """Explorer (the desktop shell) is not running, so there is nothing to launch through."""


class IShellView(IUnknown):
    _iid_ = GUID("{000214E3-0000-0000-C000-000000000046}")
    # IOleWindow's 2 methods and the 10 IShellView methods before GetItemObject; never called.
    _methods_ = [STDMETHOD(HRESULT, f"_unused{i}", []) for i in range(12)] + [
        STDMETHOD(HRESULT, "GetItemObject", [c_uint, POINTER(GUID), POINTER(c_void_p)])]


class IShellBrowser(IUnknown):
    _iid_ = GUID("{000214E2-0000-0000-C000-000000000046}")
    _methods_ = [STDMETHOD(HRESULT, f"_unused{i}", []) for i in range(12)] + [
        STDMETHOD(HRESULT, "QueryActiveShellView", [POINTER(POINTER(IShellView))])]


def _com() -> None:
    if not getattr(_tls, "ready", False):
        try:
            comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
        except OSError:
            pass  # the thread already has COM in another apartment; these calls work from either
        _tls.ready = True


def user_shell():
    """Explorer's own Shell.Application object; its ShellExecute runs inside explorer.exe."""
    _com()
    shdocvw = comtypes.client.GetModule("shdocvw.dll")
    try:
        windows = comtypes.client.CreateObject(CLSID_ShellWindows, interface=shdocvw.IShellWindows)
        _hwnd, desktop = windows.FindWindowSW(VARIANT(CSIDL_DESKTOP), VARIANT(), SWC_DESKTOP, SWFO_NEEDDISPATCH)
        if not desktop:
            raise ShellUnavailable("Explorer has no desktop window")
        browser = desktop.QueryInterface(comtypes.IServiceProvider).QueryService(SID_STopLevelBrowser, IShellBrowser)
        view = POINTER(IShellView)()
        browser.QueryActiveShellView(ctypes.byref(view))
        background = c_void_p()
        view.GetItemObject(SVGIO_BACKGROUND, ctypes.byref(IDispatch._iid_), ctypes.byref(background))
        return comtypes.client.dynamic.Dispatch(ctypes.cast(background, POINTER(IDispatch))).Application
    except (comtypes.COMError, OSError) as e:
        raise ShellUnavailable(f"could not reach Explorer: {e}") from e


def shell_execute_as_user(file: str, params: str = "", cwd: str = "", show: str = "normal") -> None:
    user_shell().ShellExecute(file, params or "", cwd or "", "open", SHOW[show])


# --------------------------------------------------------------------- app list
def _known_folder(path: str | None) -> str | None:
    """Start lists some programs as {known-folder-guid}\\rest; turn that into a real path."""
    if not path or not path.startswith("{") or "}" not in path:
        return path
    guid, _, rest = path.partition("}")
    out = ctypes.c_wchar_p()
    try:
        _shell32.SHGetKnownFolderPath(ctypes.byref(GUID(guid + "}")), 0, None, ctypes.byref(out))
    except OSError:
        return path
    try:
        return out.value.rstrip("\\") + rest
    finally:
        _ole32.CoTaskMemFree(out)


def _shortcuts(folder: str | None, source: str, wsh) -> list[dict]:
    found: list[dict] = []
    try:
        names = sorted(os.listdir(folder)) if folder else []
    except OSError:
        return found
    for name in names:
        if not name.lower().endswith(".lnk"):
            continue
        path = os.path.join(folder, name)
        try:
            link = wsh.CreateShortcut(path)
            exe, args, cwd = link.TargetPath or None, link.Arguments or "", link.WorkingDirectory or ""
        except Exception:
            exe, args, cwd = None, "", ""
        found.append({"name": name[:-4], "id": "", "exe": exe, "args": args, "cwd": cwd,
                      "open": path, "source": source})
    return found


def apps(refresh: bool = False) -> list[dict]:
    """Every app the user can start by name: taskbar pins, Start, desktop shortcuts."""
    with _cache_lock:
        if not refresh and _cache["apps"] and time.time() - _cache["at"] < CACHE_SECONDS:
            return _cache["apps"]
        _com()
        wsh = comtypes.client.CreateObject("WScript.Shell", dynamic=True)
        shell = comtypes.client.CreateObject("Shell.Application", dynamic=True)
        found = _shortcuts(os.path.join(os.environ.get("APPDATA", ""),
                                        r"Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar"), "taskbar", wsh)
        items = shell.NameSpace("shell:AppsFolder").Items()
        for i in range(items.Count):
            item = items.Item(i)
            app_id = item.Path or ""
            try:
                exe = item.ExtendedProperty("System.Link.TargetParsingPath")
            except Exception:
                exe = None
            if not exe and app_id.lower().endswith(".exe"):
                exe = app_id
            found.append({"name": item.Name, "id": app_id, "exe": _known_folder(exe) or None, "args": "",
                          "cwd": "", "open": "shell:AppsFolder\\" + app_id, "source": "start"})
        for csidl in (CSIDL_DESKTOP, CSIDL_COMMON_DESKTOPDIRECTORY):
            try:
                found += _shortcuts(shell.NameSpace(csidl).Self.Path, "desktop", wsh)
            except Exception:
                pass
        _cache.update(at=time.time(), apps=found)
        return found


def find_apps(query: str = "", limit: int = 30) -> list[dict]:
    q = query.strip().lower()
    hits = [a for a in apps() if not q or q in a["name"].lower() or q in (a.get("exe") or "").lower()
            or q in (a.get("id") or "").lower()]
    return hits[:limit]


# ------------------------------------------------------------ windows of an app
def _process_app_id(pid: int) -> str:
    """The packaged app id (AUMID) of a Store/MSIX process; '' for an ordinary program."""
    h = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        n = ctypes.c_uint32(512)
        buf = ctypes.create_unicode_buffer(512)
        return buf.value if kernel32.GetApplicationUserModelId(h, ctypes.byref(n), buf) == 0 else ""
    finally:
        kernel32.CloseHandle(h)


def _frame_child_pid(hwnd: int, frame_pid: int) -> int:
    """A Store app's window is a frame owned by ApplicationFrameHost; the app is a child window."""
    found: list[int] = []

    def cb(child, _lparam):
        pid = win.window_pid(child)
        if pid and pid != frame_pid:
            found.append(pid)
            return False
        return True

    user32.EnumChildWindows(hwnd, win.WNDENUMPROC(cb), 0)
    return found[0] if found else 0


def _exe_name(path: str | None) -> str:
    return (path or "").replace("/", "\\").rsplit("\\", 1)[-1].lower()


def app_windows(entry: dict) -> list[dict]:
    """The app's open top-level windows, front first."""
    exe, app_id = _exe_name(entry.get("exe")), (entry.get("id") or "").lower()
    packaged = "!" in app_id
    out = []
    for w in win.list_windows():
        pid, proc = w["pid"], (w["process"] or "").lower()
        if proc == "applicationframehost.exe":
            child = _frame_child_pid(w["hwnd"], pid)
            if child:
                pid, proc = child, win.process_name(child).lower()
        if (exe and proc == exe) or (packaged and _process_app_id(pid).lower() == app_id):
            out.append(w)
    return out


# ------------------------------------------------------------------- launching
def _app_path(name: str) -> bool:
    exe = name if name.lower().endswith(".exe") else name + ".exe"
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            winreg.CloseKey(winreg.OpenKey(root, r"Software\Microsoft\Windows\CurrentVersion\App Paths\\" + exe))
            return True
        except OSError:
            pass
    return False


def check_target(target: str, cwd: str | None = None) -> None:
    """Refuse what Explorer could not open: it would put an error dialog on the user's screen."""
    scheme = core.url_scheme(target)
    if scheme:
        if scheme in ("http", "https", "file", "shell"):
            return
        try:
            winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, scheme))
            return
        except OSError:
            raise ValueError(f"nothing on the PC opens {scheme}: links") from None
    path = os.path.expandvars(target)
    if os.path.exists(path) or (cwd and os.path.exists(os.path.join(cwd, path))):
        return
    if not core.is_direct_target(target) and (shutil.which(path) or _app_path(path)):
        return
    if core.is_direct_target(target) and shutil.which(path):
        return
    raise ValueError(f"no app, program or file called {target!r} on the PC "
                     f"(`winuse apps <part of the name>` lists what Start and the taskbar offer)")


def _changed_windows(before: dict[int, str], wait: float) -> list[dict]:
    """Wait until a window appears or retitles (a new tab, say); report those windows."""
    deadline = time.time() + max(0.0, wait)
    while True:
        changed = [w for w in win.list_windows() if before.get(w["hwnd"]) != w["title"]]
        if changed or time.time() >= deadline:
            if changed:
                time.sleep(0.4)  # let the first title settle
                changed = [w for w in win.list_windows() if before.get(w["hwnd"]) != w["title"]] or changed
            return changed
        time.sleep(0.25)


def launch(target: str, args: list[str] | None = None, cwd: str | None = None, show: str = "normal",
           no_console: bool = False, admin: bool = False, new: bool = False, wait: float | None = None) -> dict:
    """Start `target` as the desktop user would and report what opened.

    target: an app name as Start or the taskbar shows it ('Google Chrome', 'chrome', '记事本'),
    a link or Settings page ('https://...', 'ms-settings:sound'), or a file or program path.
    new: start another copy even if the app already has a window.
    admin: start it with the host's admin rights instead (only when the host is elevated)."""
    target = (target or "").strip()
    if not target:
        raise ValueError("nothing to launch")
    show = show or "normal"
    if show not in SHOW:
        raise ValueError(f"unknown show mode {show!r}")
    args = [str(a) for a in (args or [])]
    wait = 5.0 if wait is None else float(wait)
    before = {w["hwnd"]: w["title"] for w in win.list_windows()}
    result: dict = {"launched": target, "app": None, "as": "user", "reused": False}

    if admin:
        if not win.host_elevated():
            raise ValueError("the host is not elevated, so an admin launch would wait on a UAC prompt "
                             "nobody can answer remotely")
        check_target(target, cwd)
        win.launch(target, args=args, cwd=cwd, show=show, no_console=no_console)
        result["as"] = "admin"
    else:
        entry = None
        if not no_console and not core.is_direct_target(target):
            entry = core.match_app(apps(), target)
        if entry:
            result.update(app=entry["name"], source=entry["source"])
            if not new and not args:
                existing = app_windows(entry)
                if existing:
                    w = existing[0]
                    if show == "normal":
                        result["focused"] = win.focus_window(w["hwnd"])
                    result.update(reused=True, window=win.window_info(w["hwnd"]), foreground=win.foreground())
                    return result
        if no_console:
            # conhost --headless runs a console program with no console window at all.
            file, params = "conhost.exe", "--headless " + subprocess.list2cmdline([target] + args)
            check_target(target, cwd)
        elif entry and not args:
            file, params = entry["open"], ""  # the shortcut or Start entry itself, with its own arguments
        elif entry and entry.get("exe") and os.path.isabs(entry["exe"]):
            file = entry["exe"]
            params = " ".join(x for x in (entry.get("args"), subprocess.list2cmdline(args)) if x)
            cwd = cwd or entry.get("cwd") or None
        else:
            check_target(target, cwd)
            file, params = target, subprocess.list2cmdline(args)
        try:
            shell_execute_as_user(file, params, cwd or "", show)
        except ShellUnavailable as e:
            rc = win.shell32.ShellExecuteW(None, "open", file, params or None, cwd or None, SHOW[show])
            if (rc or 0) <= 32:
                raise OSError(f"could not launch {target!r} ({e}; ShellExecute code {rc})") from e
            result.update({"as": "host", "note": f"{e}; started it from the host, with the host's rights"})
    opened = _changed_windows(before, wait)
    result["opened"] = opened[:3]
    result["pid"] = opened[0]["pid"] if opened else None
    result["foreground"] = win.foreground()
    return result
