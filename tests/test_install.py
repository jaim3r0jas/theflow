"""theflow install sets the status line in the hub's Claude home, never over one of its own; the script renders.

Run from the repo root: python3 -m unittest discover -s tests
"""
import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import admin      # noqa: E402
import flowlib    # noqa: E402

SCRIPT = flowlib.FLOW / "lib" / "statusline.sh"


class InstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.home = self.tmp / "claude-home"
        self.settings = self.home / "settings.json"

    def install(self):
        """Run install against the temp Claude home (theflow already on PATH), returning what it printed."""
        out = io.StringIO()
        with mock.patch("admin.shutil.which", return_value="/usr/local/bin/theflow"), contextlib.redirect_stdout(out):
            admin.install([], SimpleNamespace(claude_home=self.home))
        return out.getvalue()

    def test_sets_the_status_line_and_the_guard(self):
        out = self.install()
        settings = json.loads(self.settings.read_text())
        self.assertEqual(settings["statusLine"], admin.STATUSLINE)
        self.assertEqual(settings["statusLine"]["refreshInterval"], 1)
        self.assertIn(str(SCRIPT), settings["statusLine"]["command"])
        self.assertTrue(admin.has_hook(settings, "PreToolUse", admin.PROTECT))
        self.assertIn("+ status line", out)

    def test_rerun_changes_nothing(self):
        self.install()
        before, mtime = self.settings.read_bytes(), self.settings.stat().st_mtime_ns
        out = self.install()
        self.assertEqual(self.settings.read_bytes(), before)
        self.assertEqual(self.settings.stat().st_mtime_ns, mtime)
        self.assertNotIn("status line", out)

    def test_keeps_a_status_line_of_its_own(self):
        own = {"type": "command", "command": "bash ~/my-line.sh"}
        self.home.mkdir(parents=True)
        self.settings.write_text(json.dumps({"statusLine": own, "theme": "dark"}))
        out = self.install()
        settings = json.loads(self.settings.read_text())
        self.assertEqual(settings["statusLine"], own)
        self.assertEqual(settings["theme"], "dark")
        self.assertIn("statusLine of its own: left alone", out)

    def write(self, status_line):
        self.home.mkdir(parents=True)
        self.settings.write_text(json.dumps({"statusLine": status_line}))

    def test_repoints_theflows_line_from_a_moved_checkout_keeping_what_was_tuned(self):
        self.write({"type": "command", "command": 'bash "/old/theflow/lib/statusline.sh"', "refreshInterval": 5})
        out = self.install()
        line = json.loads(self.settings.read_text())["statusLine"]
        self.assertEqual(line, {**admin.STATUSLINE, "refreshInterval": 5})
        self.assertIn("~ status line", out)

    def test_survives_a_status_line_that_is_not_an_object(self):
        self.write("bash ~/my-line.sh")
        out = self.install()
        self.assertEqual(json.loads(self.settings.read_text())["statusLine"], "bash ~/my-line.sh")
        self.assertIn("left alone", out)


@unittest.skipUnless(shutil.which("jq") and shutil.which("git"), "needs jq and git")
class ScriptTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.hub = self.tmp / "hub"
        (self.hub / "items" / "api" / "done").mkdir(parents=True)
        (self.hub / "flow.json").write_text(json.dumps({"name": "test-hub", "wip_limit": 4}))
        notes = {"retry-sync": "status: active", "rv-12": "kind: review\nstatus: active",
                 "triage": "kind: talk\nstatus: active", "old": "status: wrapping"}
        for name, extra in notes.items():
            (self.hub / "items" / "api" / f"{name}.md").write_text(f"---\nitem: {name}\n{extra}\n---\nwhat it is\n")
        (self.hub / "items" / "api" / "done" / "gone.md").write_text("---\nitem: gone\nstatus: done\n---\n")
        (self.hub / "items" / "api" / "claude-md-backlog.md").write_text("# lessons\nitem: not a note\n")
        self.tree = self.hub / "work" / "api" / "retry-sync"
        self.tree.mkdir(parents=True)
        git = lambda *a: subprocess.run(["git", "-C", str(self.tree), *a], check=True, capture_output=True)
        git("init", "-q", "-b", "feat/retry-sync")
        git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "start")

    def render(self, data, **env):
        run = subprocess.run(["bash", str(SCRIPT)], input=json.dumps(data), capture_output=True, text=True, timeout=10,
                             env={**os.environ, "COLUMNS": "200", "FLOW_HUB": "", **env})
        self.assertEqual(run.returncode, 0, run.stderr)
        return re.sub(r"\x1b\[[0-9;]*m", "", run.stdout).rstrip("\n")

    def test_renders_every_part(self):
        now = int(time.time())
        line = self.render({
            "workspace": {"current_dir": str(self.tree)},
            "model": {"display_name": "Opus 5.5 (1M context)"},
            "context_window": {"used_percentage": 42.4},
            "rate_limits": {"five_hour": {"used_percentage": 37, "resets_at": now + 7800},
                            "seven_day": {"used_percentage": 81.2, "resets_at": now + 300000}}})
        self.assertIn("/w/api/retry-sync", line)
        self.assertIn("feat/retry-sync", line)
        self.assertNotIn("±", line)
        self.assertIn("Opus 5.5 (1M)", line)
        self.assertIn("ctx 42%", line)
        self.assertIn("3 work, 1 talk of 4", line)
        self.assertRegex(line, r"5h 37% 2h\d\dm→\d\d:\d\d")
        self.assertRegex(line, r"7d 81% 3d\d+h→\w+ \d\d:\d\d")
        self.assertRegex(line, r"│ \d\d:\d\d$")
        self.assertEqual(len(line), 200 - 4)

    def test_dirty_tree_and_hub_from_the_environment(self):
        (self.tree / "new.txt").write_text("x")
        outside = self.tmp / "elsewhere"
        outside.mkdir()
        self.assertIn("±", self.render({"workspace": {"current_dir": str(self.tree)}}))
        self.assertIn("3 work, 1 talk of 4", self.render({"workspace": {"current_dir": str(outside)}},
                                                         FLOW_HUB=str(self.hub)))

    def test_outside_a_hub_and_with_no_data(self):
        line = self.render({})
        self.assertNotIn("work,", line)
        self.assertRegex(line, r"\d\d:\d\d$")

    def test_relative_dir_does_not_hang(self):
        self.assertRegex(self.render({"cwd": "some/dir"}), r"\d\d:\d\d$")

    def test_home_shows_as_tilde(self):
        self.assertIn(" ~/w/api/retry-sync ", self.render({"workspace": {"current_dir": str(self.tree)}},
                                                          HOME=str(self.hub)))

    def test_ships_nothing_personal(self):
        text = SCRIPT.read_text()
        self.assertNotIn("/Users/", text)
        self.assertNotIn("/home/", text)


if __name__ == "__main__":
    unittest.main()
