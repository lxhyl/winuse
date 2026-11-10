import io
import os
import sys
import time
import unittest
from contextlib import redirect_stdout

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "agent"))
import winuse_py  # noqa: E402

ITEMS = [
    {"ref": "e1", "depth": 0, "type": "Window", "name": "Demo"},
    {"ref": "e2", "depth": 1, "type": "Button", "name": "Save", "enabled": False},
    {"ref": "e3", "depth": 1, "type": "CheckBox", "name": "Remember me", "toggle": 1},
    {"ref": "e4", "depth": 1, "type": "Edit", "name": "User", "value": "bob"},
    {"ref": "e5", "depth": 1, "type": "Button", "name": "Cancel"},
]


class FakeDispatch:
    def __init__(self):
        self.calls = []

    def __call__(self, method, params):
        self.calls.append((method, dict(params)))
        if method == "snapshot":
            n = sum(1 for m, _ in self.calls if m == "snapshot")
            return {"ok": True, "snapshot": f"n{n}", "text": "outline", "windows": [{"title": "Demo"}],
                    "items": [dict(i) for i in ITEMS]}
        if method == "element":
            if params.get("action") == "text":
                return {"ok": True, "via": "value", "text": "bob"}
            return {"ok": True, "via": "invoke", "state": "done"}
        if method == "click":
            return {"ok": False, "error": "blocked", "message": "cannot send input: the desktop is not interactive (locked)"}
        if method == "screenshot":
            return {"ok": True, "shot": "s1", "width": 640, "height": 360, "scale": [2.0, 2.0],
                    "origin": [0, 0], "format": "png", "image": "aGVsbG8="}
        if method == "run":
            return {"ok": True, "exit_code": 0, "stdout": "hello\n", "stderr": "", "timed_out": False}
        return {"ok": True}


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.d = FakeDispatch()
        self.e = winuse_py.Engine(self.d)

    def run_code(self, code, **kw):
        return self.e.run(code, **kw)

    def test_namespace_persists_between_calls(self):
        self.assertIsNone(self.run_code("x = 20")["error"])
        self.assertEqual(self.run_code("print(x + 1)")["output"], "21\n")

    def test_reset_starts_afresh(self):
        self.run_code("x = 1")
        r = self.run_code("print(x)", reset=True)
        self.assertIn("NameError", r["error"])

    def test_sessions_are_separate(self):
        self.run_code("x = 1", session="a")
        self.assertIn("NameError", self.run_code("x", session="b")["error"])

    def test_only_printed_output_and_last_expression(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            r = self.run_code("print('a')\ny = 3\ny * 2")
        self.assertEqual(r["output"], "a\n6\n")
        self.assertEqual(buf.getvalue(), "")  # nothing leaks to the agent's own stdout
        self.assertEqual(self.run_code("None")["output"], "")

    def test_error_names_the_script_line(self):
        r = self.run_code("a = 1\nb = undefined_name\nc = 3")
        self.assertTrue(r["error"].startswith("line 2: b = undefined_name"), r["error"])
        self.assertIn("NameError", r["error"])

    def test_syntax_error(self):
        self.assertTrue(self.run_code("x = (1,\n")["error"].startswith("line "))

    def test_failed_action_raises_with_its_line(self):
        r = self.run_code("print('before')\nclick(10, 20)\nprint('never')")
        self.assertEqual(r["output"], "before\n")
        self.assertTrue(r["error"].startswith("line 2: click(10, 20)"), r["error"])
        self.assertIn("ActionError: click: blocked", r["error"])

    def test_failed_action_can_be_caught(self):
        r = self.run_code("try:\n    click(1, 2)\nexcept ActionError as e:\n    print('handled', 'locked' in str(e))")
        self.assertEqual(r["output"], "handled True\n")

    def test_timeout_stops_a_busy_loop(self):
        t0 = time.monotonic()
        r = self.run_code("print('start')\nwhile True:\n    pass", timeout=0.5)
        self.assertLess(time.monotonic() - t0, 4)
        self.assertEqual(r["output"], "start\n")
        self.assertIn("ScriptTimeout", r["error"])
        self.assertTrue(r["error"].startswith("line "), r["error"])
        # the session is usable again afterwards
        self.assertEqual(self.run_code("print('again')")["output"], "again\n")

    def test_sleep_respects_the_timeout(self):
        t0 = time.monotonic()
        r = self.run_code("sleep(30)", timeout=0.4)
        self.assertLess(time.monotonic() - t0, 4)
        self.assertIn("ScriptTimeout", r["error"])

    def test_find_filters_without_printing_the_tree(self):
        r = self.run_code("bs = find(type='Button')\nprint(len(bs), bs[0])\nprint(find('remember')[0])")
        self.assertEqual(r["output"], '2 @e2 Button "Save" [disabled]\n@e3 CheckBox "Remember me" [checked]\n')
        self.assertEqual(sum(1 for m, _ in self.d.calls if m == "snapshot"), 1)  # find reuses the snapshot

    def test_find_by_value_and_find1(self):
        r = self.run_code("print(find(value='^bob$')[0].ref)\nfind1('nothing here')")
        self.assertEqual(r["output"], "e4\n")
        self.assertIn("ActionError: no element matches /nothing here/", r["error"])

    def test_element_actions_carry_the_snapshot_id(self):
        r = self.run_code("s = snap('Demo')\nprint(find1('Cancel').click())\nprint(fill('@e4', 'alice'))")
        self.assertEqual(r["output"], "invoke\ninvoke\n")
        el = [p for m, p in self.d.calls if m == "element"]
        self.assertEqual(el[0], {"ref": "e5", "snap": "n1", "action": "click"})
        self.assertEqual(el[1], {"ref": "e4", "action": "set", "text": "alice"})
        snapcall = next(p for m, p in self.d.calls if m == "snapshot")
        self.assertTrue(snapcall["structured"])

    def test_wait_for_resnapshots_until_found(self):
        r = self.run_code("print(wait_for('Save', timeout=2))")
        self.assertEqual(r["output"], '@e2 Button "Save" [disabled]\n')

    def test_screenshots_come_back_as_images(self):
        r = self.run_code("info = shot()\nprint(info['size'], info['scale'])")
        self.assertEqual(r["output"], "(640, 360) 2.0\n")
        self.assertEqual(len(r["images"]), 1)
        self.assertEqual(r["images"][0]["image"], "aGVsbG8=")

    def test_launch_options_pass_through(self):
        self.run_code("launch('powershell', '-File', 'x.ps1', no_console=True, wait=0.5)")
        p = next(p for m, p in self.d.calls if m == "launch")
        self.assertEqual(p, {"target": "powershell", "args": ["-File", "x.ps1"], "wait": 0.5, "no_console": True})

    def test_run_returns_text(self):
        self.assertEqual(self.run_code("r = run('echo hello')\nprint(r.ok, r)")["output"], "True hello\n")

    def test_output_is_truncated(self):
        r = self.run_code("print('x' * 50000)")
        self.assertLess(len(r["output"]), winuse_py.MAX_OUTPUT + 200)
        self.assertIn("output truncated", r["output"])

    def test_builtins_set_and_type_are_not_shadowed(self):
        self.assertEqual(self.run_code("print(sorted(set([2, 1, 2])), type(1).__name__)")["output"], "[1, 2] int\n")


if __name__ == "__main__":
    unittest.main()
