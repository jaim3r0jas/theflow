"""theflow pull brings a base's default branch up to date with origin before a worktree is cut from it: an owned base
is reset, a linked one (your own checkout) only fast-forwarded, never with your changes lost. Real git repos in a
temp folder.

Run from the repo root: python3 -m unittest discover -s tests
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import flowlib    # noqa: E402


def git(where, *args):
    return subprocess.run(["git", "-C", str(where), *args], check=True, capture_output=True, text=True).stdout.strip()


def commit(where, name, text):
    (Path(where) / name).write_text(text)
    git(where, "add", name)
    git(where, "commit", "-qm", f"{name}: {text.strip()}")
    return git(where, "rev-parse", "HEAD")


class PullTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        gitconfig = self.tmp / "gitconfig"
        gitconfig.write_text("[user]\n\tname = Test\n\temail = test@example.com\n[init]\n\tdefaultBranch = main\n")
        env = mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(gitconfig), "GIT_CONFIG_NOSYSTEM": "1"})
        env.start()
        self.addCleanup(env.stop)
        self.origin = self.tmp / "origin.git"
        git(self.tmp, "init", "-q", "--bare", "-b", "main", str(self.origin))
        self.upstream = self.clone("upstream")          # someone else pushing to origin
        commit(self.upstream, "a.txt", "one\n")
        git(self.upstream, "push", "-q", "origin", "main")
        self.root = self.tmp / "hub"
        (self.root / "bases").mkdir(parents=True)
        (self.root / "flow.json").write_text('{"name": "test"}')
        self.mine = self.clone("mine")
        (self.root / "bases" / "x").symlink_to(Path("..", "..", "mine"))
        self.hub = flowlib.Hub(self.root)

    def clone(self, name):
        git(self.tmp, "clone", "-q", str(self.origin), name)
        return self.tmp / name

    def push_upstream(self, name="b.txt", text="two\n"):
        sha = commit(self.upstream, name, text)
        git(self.upstream, "push", "-q", "origin", "main")
        return sha

    def test_behind_and_checked_out_is_fast_forwarded(self):
        sha = self.push_upstream()
        ok, msg, start = self.hub.pull("x")
        self.assertTrue(ok, msg)
        self.assertIn("fast-forwarded 1 commit(s)", msg)
        self.assertEqual(start, "main")
        self.assertEqual(git(self.mine, "rev-parse", "main"), sha)
        self.assertEqual(git(self.mine, "status", "--porcelain"), "")

    def test_uncommitted_changes_that_do_not_overlap_are_kept(self):
        sha = self.push_upstream()
        (self.mine / "a.txt").write_text("mine\n")
        (self.mine / "new.txt").write_text("untracked\n")
        ok, msg, start = self.hub.pull("x")
        self.assertTrue(ok, msg)
        self.assertEqual(git(self.mine, "rev-parse", "main"), sha)
        self.assertEqual((self.mine / "a.txt").read_text(), "mine\n")
        self.assertEqual((self.mine / "new.txt").read_text(), "untracked\n")

    def test_uncommitted_changes_in_the_way_stop_it_untouched(self):
        self.push_upstream("a.txt", "theirs\n")
        before = git(self.mine, "rev-parse", "main")
        (self.mine / "a.txt").write_text("mine\n")
        ok, msg, start = self.hub.pull("x")
        self.assertFalse(ok)
        self.assertIn("not fast-forwarded", msg)
        self.assertEqual(git(self.mine, "rev-parse", "main"), before)
        self.assertEqual((self.mine / "a.txt").read_text(), "mine\n")

    def test_local_commits_alone_are_kept_and_not_pushed(self):
        sha = commit(self.mine, "local.txt", "landed by wrap-up\n")
        ok, msg, start = self.hub.pull("x")
        self.assertTrue(ok, msg)
        self.assertIn("up to date", msg)
        self.assertIn("1 local commit(s) not on origin", msg)
        self.assertEqual(git(self.mine, "rev-parse", "main"), sha)
        self.assertNotEqual(git(self.origin, "rev-parse", "main"), sha)
        self.assertEqual(start, "main")   # merge: local: the worktree holds what wrap-up landed

    def test_local_commits_stay_out_of_a_pr_base_worktree(self):
        (self.root / "flow.json").write_text('{"name": "test", "bases": {"x": {"merge": "pr"}}}')
        commit(self.mine, "mine.txt", "the user's own, unpushed\n")
        ok, msg, start = flowlib.Hub(self.root).pull("x")
        self.assertTrue(ok, msg)
        self.assertEqual(start, "origin/main")

    def test_no_local_default_branch_starts_from_origin(self):
        git(self.mine, "checkout", "-qb", "side")
        git(self.mine, "branch", "-qD", "main")
        self.push_upstream()
        ok, msg, start = self.hub.pull("x")
        self.assertTrue(ok, msg)
        self.assertEqual(start, "origin/main")
        self.assertEqual(git(self.mine, "branch", "--list", "main"), "")

    def test_diverged_stops_it_untouched(self):
        sha = commit(self.mine, "local.txt", "landed by wrap-up\n")
        self.push_upstream()
        ok, msg, start = self.hub.pull("x")
        self.assertFalse(ok)
        self.assertIn("diverged (1 local, 1 on origin)", msg)
        self.assertEqual(git(self.mine, "rev-parse", "main"), sha)

    def test_default_branch_not_checked_out_is_fast_forwarded_by_ref(self):
        git(self.mine, "checkout", "-qb", "side")
        sha = self.push_upstream()
        ok, msg, start = self.hub.pull("x")
        self.assertTrue(ok, msg)
        self.assertEqual(git(self.mine, "rev-parse", "main"), sha)
        self.assertEqual(git(self.mine, "branch", "--show-current"), "side")

    def test_default_branch_checked_out_in_another_worktree_is_left_alone(self):
        git(self.mine, "checkout", "-qb", "side")
        git(self.mine, "worktree", "add", "-q", str(self.tmp / "wt"), "main")
        before = git(self.mine, "rev-parse", "main")
        self.push_upstream()
        ok, msg, start = self.hub.pull("x")
        self.assertFalse(ok)
        self.assertEqual(git(self.mine, "rev-parse", "main"), before)

    def test_missing_origin_head_is_set(self):
        git(self.mine, "remote", "set-head", "origin", "-d")
        git(self.mine, "checkout", "-qb", "side")
        sha = self.push_upstream()
        ok, msg, start = self.hub.pull("x")
        self.assertTrue(ok, msg)
        self.assertEqual(git(self.mine, "symbolic-ref", "--short", "refs/remotes/origin/HEAD"), "origin/main")
        self.assertEqual(git(self.mine, "rev-parse", "main"), sha)

    def test_no_origin_starts_from_the_local_default(self):
        git(self.mine, "remote", "remove", "origin")
        ok, msg, start = self.hub.pull("x")
        self.assertTrue(ok, msg)
        self.assertIn("no origin, start from the local main", msg)

    def test_owned_base_is_reset_to_origin(self):
        git(self.tmp, "clone", "-q", str(self.origin), str(self.root / "bases" / "own"))
        own = self.root / "bases" / "own"
        commit(own, "stray.txt", "should go\n")
        (own / "junk.txt").write_text("untracked\n")
        sha = self.push_upstream()
        ok, msg, start = self.hub.pull("own")
        self.assertTrue(ok, msg)
        self.assertEqual(git(own, "rev-parse", "main"), sha)
        self.assertFalse((own / "junk.txt").exists())

    def test_writable_owned_base_is_only_fast_forwarded(self):
        own = self.root / "bases" / "own"
        git(self.tmp, "clone", "-q", str(self.origin), str(own))
        (self.root / "flow.json").write_text('{"name": "test", "bases": {"own": {"writable": true}}}')
        sha = self.push_upstream()
        (own / "a.txt").write_text("edited by a talk session\n")
        (own / "notes.txt").write_text("untracked\n")
        ok, msg, start = flowlib.Hub(self.root).pull("own")
        self.assertTrue(ok, msg)
        self.assertEqual(git(own, "rev-parse", "main"), sha)
        self.assertEqual((own / "a.txt").read_text(), "edited by a talk session\n")
        self.assertTrue((own / "notes.txt").exists())

    def test_folder_without_git_has_nothing_to_pull(self):
        (self.root / "bases" / "notes").mkdir()
        self.assertEqual(self.hub.pull("notes"), (True, "notes: not git, nothing to pull", None))


if __name__ == "__main__":
    unittest.main()
