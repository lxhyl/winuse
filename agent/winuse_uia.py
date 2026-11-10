"""Accessibility tree (UI Automation): snapshots with element refs, and actions that call a
control directly instead of moving the mouse or taking keyboard focus."""
from __future__ import annotations

import sys

sys.coinit_flags = 0  # multithreaded COM apartment; must be set before comtypes is imported

import threading  # noqa: E402
import time  # noqa: E402
from collections import OrderedDict  # noqa: E402

import comtypes  # noqa: E402
import comtypes.client  # noqa: E402

import winuse_core as core  # noqa: E402

# UI Automation property ids
P_RECT, P_TYPE, P_NAME, P_FOCUS, P_ENABLED, P_AUTOID, P_CLASS, P_PASSWORD, P_OFFSCREEN = (
    30001, 30003, 30005, 30008, 30010, 30011, 30012, 30019, 30022)
P_HAS_EXPAND, P_HAS_INVOKE, P_HAS_SCROLL, P_HAS_SELECT, P_HAS_TOGGLE, P_HAS_VALUE = (
    30028, 30031, 30034, 30036, 30041, 30043)
P_VALUE, P_READONLY, P_EXPAND_STATE, P_SELECTED, P_TOGGLE_STATE = 30045, 30046, 30070, 30079, 30086
_CACHED = [P_RECT, P_TYPE, P_NAME, P_FOCUS, P_ENABLED, P_AUTOID, P_CLASS, P_PASSWORD, P_OFFSCREEN,
           P_HAS_EXPAND, P_HAS_INVOKE, P_HAS_SCROLL, P_HAS_SELECT, P_HAS_TOGGLE, P_HAS_VALUE,
           P_VALUE, P_READONLY, P_EXPAND_STATE, P_SELECTED, P_TOGGLE_STATE]
# pattern ids
PAT_INVOKE, PAT_VALUE, PAT_SCROLL, PAT_EXPAND, PAT_SELECT, PAT_TEXT, PAT_TOGGLE, PAT_SCROLL_ITEM, PAT_LEGACY = (
    10000, 10002, 10004, 10005, 10010, 10014, 10015, 10017, 10018)
E_ELEMENT_GONE = -2147220991  # UIA_E_ELEMENTNOTAVAILABLE

_lock = threading.Lock()
_tls = threading.local()
_uia = None
_UIA = None
_snaps: "OrderedDict[str, dict]" = OrderedDict()
_snap_seq = 0


def _com() -> None:
    if not getattr(_tls, "ready", False):
        try:
            comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
        except OSError:
            pass
        _tls.ready = True


def _client():
    global _uia, _UIA
    _com()
    with _lock:
        if _uia is None:
            comtypes.client.GetModule("UIAutomationCore.dll")
            from comtypes.gen import UIAutomationClient as UIA
            try:
                uia = comtypes.client.CreateObject(UIA.CUIAutomation8, interface=UIA.IUIAutomation2)
                uia.AutoSetFocus = False  # pattern calls must not pull keyboard focus
                uia.ConnectionTimeout = 3000
                uia.TransactionTimeout = 15000
            except Exception:
                uia = comtypes.client.CreateObject(UIA.CUIAutomation, interface=UIA.IUIAutomation)
            _uia, _UIA = uia, UIA
    return _uia, _UIA


def _cache_request(uia, UIA):
    cr = uia.CreateCacheRequest()
    for pid in _CACHED:
        cr.AddProperty(pid)
    cr.TreeScope = UIA.TreeScope_Subtree
    cr.TreeFilter = uia.ControlViewCondition
    return cr


def _node(el, count: list) -> dict:
    count[0] += 1
    get = el.GetCachedPropertyValue
    patterns = []
    has_value = bool(get(P_HAS_VALUE))
    if get(P_HAS_INVOKE):
        patterns.append("invoke")
    if get(P_HAS_TOGGLE):
        patterns.append("toggle")
    if has_value and not get(P_READONLY):
        patterns.append("value")
    if get(P_HAS_SELECT):
        patterns.append("select")
    if get(P_HAS_EXPAND):
        patterns.append("expand")
    if get(P_HAS_SCROLL):
        patterns.append("scroll")
    r = el.CachedBoundingRectangle
    password = bool(el.CachedIsPassword)
    value = None
    if has_value and not password:
        v = get(P_VALUE)
        value = "" if v is None else str(v)
    node = {
        "type": core.CONTROL_TYPES.get(el.CachedControlType, f"Type{el.CachedControlType}"),
        "name": el.CachedName or "", "value": value, "password": password,
        "rect": [r.left, r.top, r.right, r.bottom],
        "enabled": bool(el.CachedIsEnabled), "offscreen": bool(el.CachedIsOffscreen),
        "focused": bool(el.CachedHasKeyboardFocus),
        "toggle": get(P_TOGGLE_STATE) if "toggle" in patterns else None,
        "selected": bool(get(P_SELECTED)) if "select" in patterns else None,
        "expand": get(P_EXPAND_STATE) if "expand" in patterns else None,
        "auto_id": el.CachedAutomationId or "", "class": el.CachedClassName or "",
        "patterns": patterns, "el": el, "children": [],
    }
    kids = el.GetCachedChildren()
    if kids:
        for i in range(kids.Length):
            node["children"].append(_node(kids.GetElement(i), count))
    return node


def snapshot(hwnds: list[int], interactive_only: bool = False, max_nodes: int = 400,
             include_offscreen: bool = False) -> dict:
    """Read the accessibility tree of the given windows and number the useful elements."""
    global _snap_seq
    uia, UIA = _client()
    t0 = time.time()
    cr = _cache_request(uia, UIA)
    roots, failed, count = [], [], [0]
    for hwnd in hwnds:
        try:
            root = _node(uia.ElementFromHandleBuildCache(hwnd, cr), count)
            root["hwnd"] = hwnd
            root["offscreen"] = False  # a window parked off the monitors is still worth reading
            roots.append(root)
        except comtypes.COMError as e:
            failed.append({"hwnd": hwnd, "error": _describe(e)})
    desktop = {"type": "Desktop", "name": "", "children": roots}
    items, truncated = core.outline(desktop, interactive_only=interactive_only, max_nodes=max_nodes,
                                    include_offscreen=include_offscreen)
    hwnd_of: dict[int, int] = {}
    for root in roots:  # remember which window each element belongs to
        stack = [root]
        while stack:
            n = stack.pop()
            hwnd_of[id(n)] = root["hwnd"]
            stack.extend(n["children"])
    _snap_seq += 1
    sid = f"n{_snap_seq}"
    _snaps[sid] = {it["ref"]: (it["node"], hwnd_of.get(id(it["node"]))) for it in items}
    while len(_snaps) > 5:
        _snaps.popitem(last=False)
    return {"snapshot": sid, "elements": len(items), "raw_nodes": count[0], "truncated": truncated,
            "failed": failed, "ms": round((time.time() - t0) * 1000), "text": core.format_outline(items)}


def _resolve(ref: str, snap: str | None):
    if not _snaps:
        raise ValueError("no snapshot yet: run a snapshot first")
    sid = snap or next(reversed(_snaps))
    if sid not in _snaps:
        raise ValueError(f"snapshot {sid} has expired; take a new one")
    key = str(ref).lstrip("@")
    if key not in _snaps[sid]:
        raise ValueError(f"no element @{key} in snapshot {sid}; refs only last until the next snapshot")
    return _snaps[sid][key]


def _describe(e: Exception) -> str:
    if isinstance(e, comtypes.COMError):
        if e.hresult == E_ELEMENT_GONE:
            return "the element no longer exists; take a new snapshot"
        if (e.hresult & 0xFFFFFFFF) == 0x80070005:
            return ("access denied: the window probably belongs to a program running as administrator "
                    "while this agent is not elevated (Windows blocks that, UIPI)")
        return f"UI Automation error 0x{e.hresult & 0xFFFFFFFF:08X} {e.text or ''}".strip()
    return f"{type(e).__name__}: {e}"


def _timed(fn, timeout: float = 5.0) -> str:
    """Run a pattern call on its own thread. Some controls only return from Invoke after the
    dialog they opened is closed, so do not wait forever: report 'pending' instead."""
    box: dict = {}

    def run():
        _com()
        try:
            fn()
        except Exception as e:
            box["error"] = e

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return "pending"
    if "error" in box:
        raise box["error"]
    return "done"


def act(ref: str, action: str, text: str | None = None, snap: str | None = None, amount: int = 1) -> dict:
    """Act on an element through its accessibility interface (no mouse, no focus change).

    Returns {"via": <pattern>, "state": "done"|"pending"}; via is None when the control offers
    no way to do it in the background, in which case "point" is where a real click would go."""
    node, hwnd = _resolve(ref, snap)
    el = node["el"]
    _, UIA = _client()

    def pat(pattern_id: int, iface: str):
        p = el.GetCurrentPattern(pattern_id)
        return p.QueryInterface(getattr(UIA, iface)) if p else None

    def point() -> dict:
        r = el.CurrentBoundingRectangle
        if r.right <= r.left or r.bottom <= r.top:
            raise ValueError("the element has no visible area to click")
        return {"via": None, "hwnd": hwnd, "rect": [r.left, r.top, r.right, r.bottom],
                "point": [(r.left + r.right) // 2, (r.top + r.bottom) // 2]}

    def expand_toggle(p):
        return p.Collapse() if p.CurrentExpandCollapseState == 1 else p.Expand()

    steps = {
        "invoke": lambda: (pat(PAT_INVOKE, "IUIAutomationInvokePattern"), lambda p: p.Invoke()),
        "toggle": lambda: (pat(PAT_TOGGLE, "IUIAutomationTogglePattern"), lambda p: p.Toggle()),
        "select": lambda: (pat(PAT_SELECT, "IUIAutomationSelectionItemPattern"), lambda p: p.Select()),
        "expand": lambda: (pat(PAT_EXPAND, "IUIAutomationExpandCollapsePattern"), lambda p: p.Expand()),
        "collapse": lambda: (pat(PAT_EXPAND, "IUIAutomationExpandCollapsePattern"), lambda p: p.Collapse()),
        "expand_toggle": lambda: (pat(PAT_EXPAND, "IUIAutomationExpandCollapsePattern"), expand_toggle),
        "show": lambda: (pat(PAT_SCROLL_ITEM, "IUIAutomationScrollItemPattern"), lambda p: p.ScrollIntoView()),
    }

    def run(name: str) -> dict | None:
        p, call = steps[name]()
        if not p:
            return None
        return {"via": "expand" if name == "expand_toggle" else name, "state": _timed(lambda: call(p)), "hwnd": hwnd}

    try:
        if action == "click":
            for name in ("invoke", "toggle", "select", "expand_toggle"):
                r = run(name)
                if r:
                    return r
            p = pat(PAT_LEGACY, "IUIAutomationLegacyIAccessiblePattern")
            if p and p.CurrentDefaultAction:
                return {"via": "default-action", "state": _timed(p.DoDefaultAction), "hwnd": hwnd}
            return point()
        if action in steps and action != "expand_toggle":
            r = run(action)
            if r is None:
                raise ValueError(f"this {node['type']} does not support '{action}'")
            return r
        if action == "set":
            if text is None:
                raise ValueError("set needs the text")
            if node["type"] == "ComboBox":
                # Setting a combo box's value does not tell the app, so choose the item instead.
                ep = pat(PAT_EXPAND, "IUIAutomationExpandCollapsePattern")
                if ep:
                    ep.Expand()
                    time.sleep(0.3)
                    uia = _client()[0]
                    item = el.FindFirst(4, uia.CreatePropertyCondition(P_NAME, text))  # 4 = descendants
                    sp = item.GetCurrentPattern(PAT_SELECT) if item else None
                    if sp:
                        sp.QueryInterface(UIA.IUIAutomationSelectionItemPattern).Select()
                        try:
                            if ep.CurrentExpandCollapseState == 1:
                                ep.Collapse()
                        except comtypes.COMError:
                            pass
                        return {"via": "expand+select", "state": "done", "hwnd": hwnd}
                    try:
                        ep.Collapse()
                    except comtypes.COMError:
                        pass
                    if item is None or not sp:
                        raise ValueError(f"the combo box has no item named {text!r}")
                return {**point(), "combo": True}
            p = pat(PAT_VALUE, "IUIAutomationValuePattern")
            if not p or p.CurrentIsReadOnly:
                return point()
            state = _timed(lambda: p.SetValue(text))
            return {"via": "value", "state": state, "hwnd": hwnd,
                    "value_now": None if node["password"] else str(p.CurrentValue or "")}
        if action == "focus":
            return {"via": "focus", "state": _timed(el.SetFocus), "hwnd": hwnd}
        if action.startswith("scroll-"):
            p = pat(PAT_SCROLL, "IUIAutomationScrollPattern")
            if not p:
                raise ValueError(f"this {node['type']} is not scrollable through accessibility")
            big_dec, none, big_inc = 0, 2, 3
            h, v = {"scroll-up": (none, big_dec), "scroll-down": (none, big_inc),
                    "scroll-left": (big_dec, none), "scroll-right": (big_inc, none)}[action]
            for _ in range(max(1, amount)):
                p.Scroll(h, v)
            return {"via": "scroll", "state": "done", "hwnd": hwnd}
        if action == "point":
            return point()
        if action == "text":
            p = pat(PAT_TEXT, "IUIAutomationTextPattern")
            if p and not node["password"]:
                return {"via": "text", "text": p.DocumentRange.GetText(20000)}
            vp = pat(PAT_VALUE, "IUIAutomationValuePattern")
            if vp and not node["password"]:
                return {"via": "value", "text": str(vp.CurrentValue or "")}
            return {"via": "name", "text": el.CurrentName or ""}
        if action == "info":
            r = el.CurrentBoundingRectangle
            legacy = pat(PAT_LEGACY, "IUIAutomationLegacyIAccessiblePattern")
            return {"type": node["type"], "name": el.CurrentName or "", "auto_id": node["auto_id"],
                    "class": node["class"], "rect": [r.left, r.top, r.right, r.bottom],
                    "enabled": bool(el.CurrentIsEnabled), "offscreen": bool(el.CurrentIsOffscreen),
                    "focused": bool(el.CurrentHasKeyboardFocus), "patterns": node["patterns"],
                    "default_action": (legacy.CurrentDefaultAction or "") if legacy else "", "hwnd": hwnd}
    except comtypes.COMError as e:
        raise ValueError(_describe(e))
    except KeyError:
        pass
    raise ValueError(f"unknown element action {action!r}")
