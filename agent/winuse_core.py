"""Pure helpers shared by the Windows agent and the tests. No Windows APIs here."""
from __future__ import annotations

from dataclasses import dataclass

VERSION = "0.1.0"
DEFAULT_PORT = 47833

_VK = {
    "backspace": 0x08, "tab": 0x09, "enter": 0x0D, "return": 0x0D,
    "shift": 0x10, "ctrl": 0x11, "control": 0x11, "alt": 0x12,
    "pause": 0x13, "capslock": 0x14, "esc": 0x1B, "escape": 0x1B, "space": 0x20,
    "pageup": 0x21, "pgup": 0x21, "pagedown": 0x22, "pgdn": 0x22,
    "end": 0x23, "home": 0x24, "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "printscreen": 0x2C, "prtsc": 0x2C, "insert": 0x2D, "ins": 0x2D,
    "delete": 0x2E, "del": 0x2E, "win": 0x5B, "super": 0x5B, "cmd": 0x5B,
    "apps": 0x5D, "menu": 0x5D, "numlock": 0x90,
    ";": 0xBA, "=": 0xBB, "plus": 0xBB, ",": 0xBC, "-": 0xBD, "minus": 0xBD,
    ".": 0xBE, "/": 0xBF, "`": 0xC0, "[": 0xDB, "\\": 0xDC, "]": 0xDD, "'": 0xDE,
}
for _i in range(26):
    _VK[chr(ord("a") + _i)] = 0x41 + _i
for _i in range(10):
    _VK[str(_i)] = 0x30 + _i
for _i in range(1, 25):
    _VK[f"f{_i}"] = 0x6F + _i

VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RETURN, VK_TAB = 0x10, 0x11, 0x12, 0x5B, 0x0D, 0x09
MODIFIERS = frozenset({VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN})
# Keys that need KEYEVENTF_EXTENDEDKEY so they are not read as numpad keys.
EXTENDED = frozenset({0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2C, 0x2D, 0x2E, 0x5B, 0x5D, 0x90})


def parse_combo(combo: str) -> list[int]:
    """'ctrl+shift+s' -> [0x11, 0x10, 0x53]. The '+' key itself is written 'plus'."""
    parts = [p.strip().lower() for p in combo.split("+")]
    if not combo.strip() or any(p == "" for p in parts):
        raise ValueError(f"bad key combo: {combo!r} (write the + key as 'plus')")
    vks = []
    for p in parts:
        if p not in _VK:
            raise ValueError(f"unknown key {p!r} in {combo!r}")
        vks.append(_VK[p])
    return vks


def key_events(vks: list[int]) -> list[tuple[int, bool]]:
    """Press in order, release in reverse: [(vk, is_down), ...]."""
    return [(vk, True) for vk in vks] + [(vk, False) for vk in reversed(vks)]


def parse_point(text: str) -> tuple[int, int]:
    """'640,360' or '640x360' or '640 360' -> (640, 360)."""
    parts = text.replace("x", ",").replace(" ", ",").split(",")
    parts = [p for p in parts if p != ""]
    if len(parts) != 2:
        raise ValueError(f"expected X,Y but got {text!r}")
    return int(float(parts[0])), int(float(parts[1]))


def fit_size(w: int, h: int, max_edge: int) -> tuple[int, int, float, float]:
    """Shrink (w, h) so the long edge is <= max_edge. Returns (nw, nh, sx, sy);
    sx/sy are screen pixels per image pixel."""
    if max_edge <= 0 or max(w, h) <= max_edge:
        return w, h, 1.0, 1.0
    ratio = max(w, h) / max_edge
    nw, nh = max(1, round(w / ratio)), max(1, round(h / ratio))
    return nw, nh, w / nw, h / nh


@dataclass(frozen=True)
class ShotMap:
    """Maps a pixel of a returned screenshot back to physical screen coordinates."""
    ox: int
    oy: int
    sx: float
    sy: float
    w: int
    h: int

    def to_screen(self, x: float, y: float) -> tuple[int, int]:
        if not (0 <= x < self.w and 0 <= y < self.h):
            raise ValueError(f"point {x},{y} is outside the {self.w}x{self.h} screenshot")
        return self.ox + int((x + 0.5) * self.sx), self.oy + int((y + 0.5) * self.sy)

    def rect_to_screen(self, x: float, y: float, w: float, h: float) -> tuple[int, int, int, int]:
        """Image-space rect -> screen-space (left, top, right, bottom)."""
        return (self.ox + int(x * self.sx), self.oy + int(y * self.sy),
                self.ox + int((x + w) * self.sx), self.oy + int((y + h) * self.sy))


def utf16_units(text: str) -> list[int]:
    """UTF-16 code units of text, for KEYEVENTF_UNICODE input."""
    raw = text.encode("utf-16-le")
    return [raw[i] | (raw[i + 1] << 8) for i in range(0, len(raw), 2)]


# ------------------------------------------------------- accessibility tree
CONTROL_TYPES = {
    50000: "Button", 50001: "Calendar", 50002: "CheckBox", 50003: "ComboBox", 50004: "Edit",
    50005: "Hyperlink", 50006: "Image", 50007: "ListItem", 50008: "List", 50009: "Menu",
    50010: "MenuBar", 50011: "MenuItem", 50012: "ProgressBar", 50013: "RadioButton", 50014: "ScrollBar",
    50015: "Slider", 50016: "Spinner", 50017: "StatusBar", 50018: "Tab", 50019: "TabItem",
    50020: "Text", 50021: "ToolBar", 50022: "ToolTip", 50023: "Tree", 50024: "TreeItem",
    50025: "Custom", 50026: "Group", 50027: "Thumb", 50028: "DataGrid", 50029: "DataItem",
    50030: "Document", 50031: "SplitButton", 50032: "Window", 50033: "Pane", 50034: "Header",
    50035: "HeaderItem", 50036: "Table", 50037: "TitleBar", 50038: "Separator", 50039: "SemanticZoom",
    50040: "AppBar",
}
INTERACTIVE_TYPES = frozenset({
    "Button", "CheckBox", "ComboBox", "Edit", "Hyperlink", "ListItem", "MenuItem", "RadioButton",
    "Slider", "Spinner", "SplitButton", "TabItem", "TreeItem", "DataItem", "Document", "HeaderItem"})
ACTION_PATTERNS = frozenset({"invoke", "toggle", "value", "select", "expand"})
SKIP_SUBTREE_TYPES = frozenset({"ScrollBar", "TitleBar", "ToolTip", "Thumb"})


def outline(root: dict, interactive_only: bool = False, max_nodes: int = 400,
            include_offscreen: bool = False) -> tuple[list[dict], bool]:
    """Reduce a raw accessibility tree to the nodes worth showing an agent.

    Keeps controls you can act on and anything with a name; unnamed containers are dropped
    and their children move up a level. Returns ([{ref, depth, node}], truncated)."""
    items: list[dict] = []
    truncated = False

    def walk(node: dict, depth: int, parent_name: str) -> None:
        nonlocal truncated
        t = node.get("type", "")
        if t in SKIP_SUBTREE_TYPES or (node.get("offscreen") and not include_offscreen):
            return
        name = " ".join((node.get("name") or "").split())
        interactive = t in INTERACTIVE_TYPES or bool(ACTION_PATTERNS & set(node.get("patterns") or ()))
        keep = interactive or (bool(name) and not interactive_only)
        if keep and t in ("Text", "Image") and name == parent_name:
            keep = False  # a label repeating its parent's name
        l, top, r, b = node.get("rect") or (0, 0, 0, 0)
        if keep and not interactive and (r - l <= 0 or b - top <= 0):
            keep = False
        child_depth, child_parent = depth, parent_name
        if keep:
            if len(items) >= max_nodes:
                truncated = True
                return
            items.append({"ref": f"e{len(items) + 1}", "depth": depth, "node": node})
            child_depth, child_parent = depth + 1, name
        for child in node.get("children") or ():
            walk(child, child_depth, child_parent)

    walk(root, 0, "")
    return items, truncated


def _clip(text: str, limit: int = 80) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def format_outline(items: list[dict]) -> str:
    """One line per element: '  @e3 Button "Save" [disabled]'."""
    lines = []
    for it in items:
        n = it["node"]
        line = "  " * it["depth"] + f'@{it["ref"]} {n.get("type", "?")}'
        name = _clip(n.get("name") or "")
        if name:
            line += f' "{name}"'
        value = n.get("value")
        if n.get("password"):
            line += ' value="(hidden)"'
        elif value is not None and _clip(value) != name:
            line += f' value="{_clip(value)}"'
        flags = []
        if n.get("enabled") is False:
            flags.append("disabled")
        if n.get("focused"):
            flags.append("focused")
        if n.get("toggle") is not None:
            flags.append({0: "unchecked", 1: "checked"}.get(n["toggle"], "mixed"))
        if n.get("selected"):
            flags.append("selected")
        if n.get("expand") in (0, 1):
            flags.append("expanded" if n["expand"] == 1 else "collapsed")
        if flags:
            line += " [" + " ".join(flags) + "]"
        lines.append(line)
    return "\n".join(lines)
