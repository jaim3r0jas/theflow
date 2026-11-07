"""The first tab of a base workspace is a hub session too: which panes count (sync --hub-tab), what the SessionStart
hook says there, and that theflow status no longer lists such a Claude as an orphan. Against a fake herdr.

Run from the repo root: python3 -m unittest discover -s tests
"""
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import flowlib    # noqa: E402
import sync       # noqa: E402
from test_talk_tabs import FakeHerdr   # noqa: E402

spec = importlib.util.spec_from_file_location("session_start", flowlib.FLOW / "lib" / "session-start.py")
session_start = importlib.util.module_from_spec(spec)
spec.loader.exec_module(session_start)


class Fixture(unittest.TestCase):
    """A hub with one linked base x (a folder outside the hub, like ~/Personal) and a worktree folder."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.root = self.tmp / "hub"
        (self.root / "bases").mkdir(parents=True)
        (self.tmp / "folder").mkdir()
        (self.root / "bases" / "x").symlink_to(self.tmp / "folder")
        (self.root / "work" / "x" / "probe").mkdir(parents=True)
        (self.root / "flow.json").write_text(json.dumps({"name": "t"}))
        self.hub = flowlib.Hub(self.root)
        self.path = self.hub.base_path("x")


class HubTab(Fixture):
    def setUp(self):
        super().setUp()
        self.herdr = FakeHerdr(self.path)
        self.hub.herdr_try = self.herdr
        self.hub.herdr_running = lambda: True

    def hub_tab(self, pane, base="x"):
        """What `theflow sync --hub-tab [--base x]` prints for the pane."""
        out = io.StringIO()
        argv = ["sync.py", "--hub-tab"] + (["--base", base] if base else [])
        with mock.patch.object(sys, "argv", argv), mock.patch.dict(os.environ, {"HERDR_PANE_ID": pane}), \
                mock.patch("sync.flowlib.Hub", return_value=self.hub), contextlib.redirect_stdout(out):
            sync.main()
        return out.getvalue().strip()

    def test_the_first_base_tab_becomes_hub_base(self):
        (pane,) = self.herdr.tab("1", "claude")
        self.assertEqual(self.hub_tab(pane), "hub:x")
        self.assertEqual(self.herdr.label_of("w1:t1"), "hub:x")

    def test_a_first_tab_the_user_named_keeps_its_name(self):
        (pane,) = self.herdr.tab("notes", "claude")
        self.assertEqual(self.hub_tab(pane), "notes")
        self.assertEqual(self.herdr.label_of("w1:t1"), "notes")

    def test_a_later_base_tab_is_no_hub(self):
        self.herdr.tab("1", "shell")
        (pane,) = self.herdr.tab("2", "claude")
        self.assertEqual(self.hub_tab(pane), "")
        self.assertFalse(any(c[:2] == ("tab", "rename") for c in self.herdr.calls))

    def test_a_talk_tab_first_is_no_hub(self):
        (pane,) = self.herdr.tab("talk:probe", "claude:probe")
        self.assertEqual(self.hub_tab(pane), "")
        self.assertEqual(self.herdr.label_of("w1:t1"), "talk:probe")

    def test_a_pane_that_left_the_base_folder_is_no_hub(self):
        (pane,) = self.herdr.tab("1", "claude")
        self.herdr.panes[0]["cwd"] = str(self.root / "work" / "x" / "probe")
        self.herdr.panes.append({**self.herdr.panes[0], "pane_id": "w1:p9", "cwd": str(self.path)})
        self.assertEqual(self.hub_tab(pane), "")

    def test_the_first_tab_is_the_lowest_number_not_the_first_listed(self):
        (later,) = self.herdr.tab("2", "claude")
        (first,) = self.herdr.tab("1", "claude")
        self.herdr.tabs[0]["number"], self.herdr.tabs[1]["number"] = 2, 1
        self.assertEqual(self.hub_tab(later), "")
        self.assertEqual(self.hub_tab(first), "hub:x")

    def test_the_hub_root_keeps_its_random_slug(self):
        self.herdr.workspaces.append({"workspace_id": "w0", "label": "t"})
        (pane,) = self.herdr.tab("1", "claude", ws="w0")
        self.herdr.panes[0]["cwd"] = str(self.root)
        label = self.hub_tab(pane, base=None)
        self.assertRegex(label, r"^hub:[a-z]+-[a-z]+$")
        self.assertNotEqual(label, "hub:x")

    def status(self):
        s = sync.Sync(self.hub, status=True)
        s.orphans("x", "base:x", "w1", {"w1"})
        return "\n".join(row for _, row in s.rows)

    def test_status_lists_no_hub_tab_as_an_orphan(self):
        (hub,) = self.herdr.tab("hub:x", "claude")
        (other,) = self.herdr.tab("2", "claude")
        rows = self.status()
        self.assertNotIn(hub, rows)
        self.assertIn(f"no item: pane {other}", rows)


class Hook(Fixture):
    """lib/session-start.py: what it prints for a session starting in a folder, the sync calls faked."""

    def hook(self, cwd, pane="w1:p1", herdr=True, hub_tab="hub:x", flow_hub=True):
        calls = []

        def run(cmd, **kw):
            calls.append(cmd[2:])
            out = hub_tab if "--hub-tab" in cmd else "running  probe  probe\n1 active items (limit 6)"
            return subprocess.CompletedProcess(cmd, 0, out + "\n", "")

        env = {"HERDR_PANE_ID": pane or "", "FLOW_HUB": str(self.root) if flow_hub else ""}
        out = io.StringIO()
        with mock.patch.dict(os.environ, env), mock.patch("sys.stdin", io.StringIO(json.dumps({"cwd": str(cwd)}))), \
                mock.patch.object(flowlib.Hub, "herdr_running", return_value=herdr), \
                mock.patch.object(session_start.subprocess, "run", side_effect=run), contextlib.redirect_stdout(out):
            session_start.main()
        text = out.getvalue().strip()
        return (json.loads(text) if text else None), calls

    def test_the_hub_root_greets(self):
        said, calls = self.hook(self.root, hub_tab="hub:calm-otter")
        self.assertIn("👋 t: /work-on", said["systemMessage"])
        out = said["hookSpecificOutput"]
        self.assertEqual(out["sessionTitle"], "hub:calm-otter")
        self.assertIn("in the root of the theflow hub t", out["additionalContext"])
        self.assertIn(["--status", "--mark-seen"], calls)

    def test_the_hub_root_greets_outside_herdr(self):
        said, calls = self.hook(self.root, pane=None, herdr=False)
        self.assertEqual(said["hookSpecificOutput"]["sessionTitle"], "hub")
        self.assertEqual(calls, [])

    def test_the_first_base_tab_greets_with_the_same_message(self):
        root_said, _ = self.hook(self.root)
        said, calls = self.hook(self.path)
        self.assertEqual(said["systemMessage"], root_said["systemMessage"])
        out = said["hookSpecificOutput"]
        self.assertEqual(out["sessionTitle"], "hub:x")
        self.assertIn("first tab of base x's workspace", out["additionalContext"])
        self.assertIn(f"read {self.root}/CLAUDE.md", out["additionalContext"])
        self.assertIn("A request that names no base is for base x.", out["additionalContext"])
        self.assertIn("invoke the `work-on` skill", out["additionalContext"])
        self.assertEqual(calls[0], ["--hub-tab", "--base", "x"])

    def test_an_owned_base_inside_the_hub_already_has_the_hub_rules(self):
        (self.root / "bases" / "api").mkdir()
        said, _ = self.hook(self.root / "bases" / "api", hub_tab="hub:api")
        context = said["hookSpecificOutput"]["additionalContext"]
        self.assertIn("first tab of base api's workspace", context)
        self.assertNotIn("did not load", context)
        self.assertIn("did not load", self.hook(self.path)[0]["hookSpecificOutput"]["additionalContext"])

    def test_a_later_base_tab_is_silent_and_marks_nothing_seen(self):
        said, calls = self.hook(self.path, hub_tab="")
        self.assertIsNone(said)
        self.assertEqual(calls, [["--hub-tab", "--base", "x"]])

    def test_a_base_folder_outside_herdr_is_silent(self):
        self.assertEqual(self.hook(self.path, pane=None), (None, []))
        self.assertEqual(self.hook(self.path, herdr=False), (None, []))

    def test_a_worktree_is_silent(self):
        self.assertEqual(self.hook(self.root / "work" / "x" / "probe"), (None, []))

    def test_a_folder_outside_any_hub_is_silent(self):
        self.assertEqual(self.hook(self.tmp, flow_hub=False), (None, []))


if __name__ == "__main__":
    unittest.main()
