import importlib.machinery
import importlib.util
import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "agent"))
import winuse_core as core  # noqa: E402


def load_cli():
    path = os.path.join(REPO, "bin", "winuse")
    loader = importlib.machinery.SourceFileLoader("winuse_cli", path)
    spec = importlib.util.spec_from_loader("winuse_cli", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class KeyTests(unittest.TestCase):
    def test_combo(self):
        self.assertEqual(core.parse_combo("ctrl+shift+s"), [0x11, 0x10, 0x53])
        self.assertEqual(core.parse_combo("Win+R"), [0x5B, 0x52])
        self.assertEqual(core.parse_combo("alt+f4"), [0x12, 0x73])
        self.assertEqual(core.parse_combo("enter"), [0x0D])
        self.assertEqual(core.parse_combo("ctrl+plus"), [0x11, 0xBB])
        self.assertEqual(core.parse_combo("f1"), [0x70])
        self.assertEqual(core.parse_combo("f12"), [0x7B])

    def test_combo_errors(self):
        for bad in ("", "ctrl+", "ctrl++", "ctrl+nosuchkey", "+"):
            with self.assertRaises(ValueError, msg=bad):
                core.parse_combo(bad)

    def test_events_release_in_reverse(self):
        self.assertEqual(core.key_events([0x11, 0x53]),
                         [(0x11, True), (0x53, True), (0x53, False), (0x11, False)])

    def test_utf16(self):
        self.assertEqual(core.utf16_units("a"), [0x61])
        self.assertEqual(core.utf16_units("你"), [0x4F60])
        self.assertEqual(core.utf16_units("😀"), [0xD83D, 0xDE00])  # surrogate pair


class GeometryTests(unittest.TestCase):
    def test_parse_point(self):
        self.assertEqual(core.parse_point("640,360"), (640, 360))
        self.assertEqual(core.parse_point("640 360"), (640, 360))
        self.assertEqual(core.parse_point("640, 360"), (640, 360))
        self.assertEqual(core.parse_point("10.6,3"), (10, 3))
        with self.assertRaises(ValueError):
            core.parse_point("640")

    def test_fit_size(self):
        self.assertEqual(core.fit_size(2560, 1440, 1280), (1280, 720, 2.0, 2.0))
        self.assertEqual(core.fit_size(800, 600, 1280), (800, 600, 1.0, 1.0))
        self.assertEqual(core.fit_size(2560, 1440, 0), (2560, 1440, 1.0, 1.0))
        nw, nh, sx, sy = core.fit_size(1440, 2560, 1280)
        self.assertEqual((nw, nh), (720, 1280))

    def test_shot_map_full_screen(self):
        m = core.ShotMap(0, 0, 2.0, 2.0, 1280, 720)
        self.assertEqual(m.to_screen(0, 0), (1, 1))          # centre of the 2x2 block
        self.assertEqual(m.to_screen(640, 360), (1281, 721))
        self.assertEqual(m.to_screen(1279, 719), (2559, 1439))  # stays on screen
        with self.assertRaises(ValueError):
            m.to_screen(1280, 10)

    def test_shot_map_window_and_negative_origin(self):
        m = core.ShotMap(-1920, 100, 1.0, 1.0, 800, 600)  # window on a monitor left of the primary
        self.assertEqual(m.to_screen(10, 20), (-1910, 120))
        self.assertEqual(m.rect_to_screen(0, 0, 100, 50), (-1920, 100, -1820, 150))

    def test_region_zoom_rect(self):
        m = core.ShotMap(0, 0, 2.0, 2.0, 1280, 720)
        self.assertEqual(m.rect_to_screen(100, 50, 320, 180), (200, 100, 840, 460))


def sample_tree():
    return {"type": "Window", "name": "Demo", "rect": [0, 0, 800, 600], "children": [
        {"type": "TitleBar", "name": "Demo", "rect": [0, 0, 800, 30],
         "children": [{"type": "Button", "name": "Close", "rect": [770, 0, 800, 30]}]},
        {"type": "Pane", "name": "", "rect": [0, 30, 800, 600], "children": [
            {"type": "Button", "name": "Save", "rect": [10, 40, 90, 70], "enabled": False, "patterns": ["invoke"],
             "children": [{"type": "Text", "name": "Save", "rect": [12, 42, 88, 68]}]},
            {"type": "Edit", "name": "User", "value": "bob", "rect": [10, 80, 300, 110], "focused": True},
            {"type": "Edit", "name": "Password", "value": "hunter2", "password": True, "rect": [10, 120, 300, 150]},
            {"type": "CheckBox", "name": "Remember", "toggle": 1, "rect": [10, 160, 200, 180]},
            {"type": "Text", "name": "Zero area", "rect": [0, 0, 0, 0]},
            {"type": "Text", "name": "Scrolled away", "offscreen": True, "rect": [0, 900, 10, 910]},
            {"type": "Text", "name": "Hello  there\nworld", "rect": [10, 200, 300, 220]},
            {"type": "Custom", "name": "", "patterns": ["invoke"], "rect": [10, 240, 40, 270]},
        ]}]}


class OutlineTests(unittest.TestCase):
    def test_outline_keeps_useful_nodes(self):
        items, truncated = core.outline(sample_tree())
        self.assertFalse(truncated)
        self.assertEqual(core.format_outline(items).splitlines(), [
            '@e1 Window "Demo"',
            '  @e2 Button "Save" [disabled]',          # title bar dropped, duplicate label dropped
            '  @e3 Edit "User" value="bob" [focused]',  # unnamed pane flattened away
            '  @e4 Edit "Password" value="(hidden)"',
            '  @e5 CheckBox "Remember" [checked]',
            '  @e6 Text "Hello there world"',
            '  @e7 Custom',                              # unnamed but clickable
        ])

    def test_password_value_never_printed(self):
        items, _ = core.outline(sample_tree())
        self.assertNotIn("hunter2", core.format_outline(items))

    def test_interactive_only(self):
        items, _ = core.outline(sample_tree(), interactive_only=True)
        self.assertEqual([i["node"]["type"] for i in items], ["Button", "Edit", "Edit", "CheckBox", "Custom"])
        self.assertTrue(all(i["depth"] == 0 for i in items))

    def test_offscreen_and_limit(self):
        items, _ = core.outline(sample_tree(), include_offscreen=True)
        self.assertIn("Scrolled away", core.format_outline(items))
        items, truncated = core.outline(sample_tree(), max_nodes=3)
        self.assertEqual(len(items), 3)
        self.assertTrue(truncated)

    def test_refs_are_sequential(self):
        items, _ = core.outline(sample_tree())
        self.assertEqual([i["ref"] for i in items], [f"e{n}" for n in range(1, len(items) + 1)])


MONS = [  # primary 2560x1440 at 125%, a 1920x1080 monitor to its LEFT (negative x), slightly lower
    {"index": 1, "rect": [0, 0, 2560, 1440], "primary": True, "scale": 1.25},
    {"index": 2, "rect": [-1920, 200, 0, 1280], "primary": False, "scale": 1.0},
]


class DisplayTests(unittest.TestCase):
    def test_union_is_the_virtual_desktop(self):
        self.assertEqual(core.union_rect(m["rect"] for m in MONS), (-1920, 0, 2560, 1440))

    def test_display_for_window(self):
        self.assertEqual(core.display_for_rect(MONS, (100, 100, 900, 700)), 1)
        self.assertEqual(core.display_for_rect(MONS, (-1500, 300, -500, 900)), 2)
        # straddling: centre decides
        self.assertEqual(core.display_for_rect(MONS, (-300, 300, 100, 700)), 2)
        # off every monitor: primary
        self.assertEqual(core.display_for_rect(MONS, (5000, 5000, 5100, 5100)), 1)
        self.assertEqual(core.display_for_rect(MONS, None), 1)

    def test_parse_display(self):
        self.assertIsNone(core.parse_display(None))
        self.assertEqual(core.parse_display("all"), "all")
        self.assertEqual(core.parse_display("2"), 2)
        for bad in ("0", "-1", "left"):
            with self.assertRaises(ValueError):
                core.parse_display(bad)

    def test_capture_rect(self):
        one = MONS[:1]
        self.assertEqual(core.capture_rect(one, None, None), ((0, 0, 2560, 1440), 1))
        self.assertEqual(core.capture_rect(MONS, "all", None), ((-1920, 0, 2560, 1440), "all"))
        self.assertEqual(core.capture_rect(MONS, 2, None), ((-1920, 200, 0, 1280), 2))
        # automatic with two monitors follows the foreground window
        self.assertEqual(core.capture_rect(MONS, None, (-1000, 400, -200, 900)), ((-1920, 200, 0, 1280), 2))
        with self.assertRaises(ValueError):
            core.capture_rect(MONS, 3, None)

    def test_click_mapping_on_a_left_monitor(self):
        # a screenshot of display 2 downscaled from 1920 to 1280 maps back to negative x
        nw, nh, sx, sy = core.fit_size(1920, 1080, 1280)
        m = core.ShotMap(-1920, 200, sx, sy, nw, nh)
        x, y = m.to_screen(640, 360)
        self.assertTrue(-1920 <= x < 0 and 200 <= y < 1280)
        self.assertEqual((x, y), (-960, 740))


class CliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cli = load_cli()

    def parse(self, *argv):
        return self.cli.build_parser().parse_args(list(argv))

    def test_click_forms(self):
        self.assertEqual(self.parse("click", "640,360").point, ["640,360"])
        a = self.parse("click", "640", "360", "--right", "--mod", "ctrl")
        self.assertEqual(core.parse_point(" ".join(a.point)), (640, 360))
        self.assertTrue(a.right)

    def test_launch_keeps_program_arguments(self):
        a = self.parse("launch", "notepad", "C:\\a b.txt", "--flag")
        self.assertEqual((a.target, a.args), ("notepad", ["C:\\a b.txt", "--flag"]))
        a = self.parse("launch", "--no-activate", "notepad", "--flag")
        self.assertEqual((a.target, a.args, a.no_activate), ("notepad", ["--flag"], True))

    def test_refs_versus_points(self):
        self.assertTrue(self.cli.is_ref("@e12"))
        self.assertTrue(self.cli.is_ref("e3"))
        self.assertFalse(self.cli.is_ref("640,360"))
        self.assertFalse(self.cli.is_ref("edit"))

    def test_scroll_default_amount(self):
        a = self.parse("scroll", "down")
        self.assertEqual((a.direction, a.amount), ("down", 3))

    def test_reports_nobody_signed_in(self):
        cli = load_cli()
        cli.ensure_tunnel = cli.reset_tunnel = lambda: None
        cli.healthy = lambda timeout=3: False
        cli.signed_in_users = lambda: ""
        with self.assertRaises(cli.NotSignedIn):
            cli.call("status")
        cli.build_parser().parse_args(["status"])
        self.assertEqual(cli.main(["status"]), 5)

    def test_call_drops_unset_params(self):
        seen = {}
        self.cli.ensure_tunnel = lambda: None
        self.cli.healthy = lambda timeout=3: True
        self.cli._post = lambda method, params, timeout: seen.update(method=method, params=params) or {"ok": True}
        self.cli.call("click", {"x": 1, "y": 2, "shot": None, "abs": None})
        self.assertEqual(seen, {"method": "click", "params": {"x": 1, "y": 2}})


if __name__ == "__main__":
    unittest.main()
