"""Tests for root_dir sandbox path validation (resolve_under_root).

Run: python -m unittest discover -s .claude/skills/cluster-connect/tests -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import cluster_lib as cl

ROOT = "/home/u/scratch"


class TestSandboxResolve(unittest.TestCase):
    def test_absolute_under_root(self):
        self.assertEqual(cl.resolve_under_root("/home/u/scratch/sub/file", ROOT),
                         "/home/u/scratch/sub/file")

    def test_relative_under_root(self):
        self.assertEqual(cl.resolve_under_root("sub/file", ROOT),
                         "/home/u/scratch/sub/file")

    def test_dotdir_collapsed(self):
        self.assertEqual(cl.resolve_under_root("/home/u/scratch/./sub/../file", ROOT),
                         "/home/u/scratch/file")

    def test_absolute_outside_root_rejected(self):
        self.assertIsNone(cl.resolve_under_root("/etc/passwd", ROOT))

    def test_dotdot_escape_rejected(self):
        self.assertIsNone(cl.resolve_under_root("../etc/passwd", ROOT))

    def test_dotdot_in_absolute_rejected(self):
        self.assertIsNone(cl.resolve_under_root("/home/u/scratch/../etc/passwd", ROOT))

    def test_root_dir_itself_allowed(self):
        self.assertEqual(cl.resolve_under_root(".", ROOT), ROOT)
        self.assertEqual(cl.resolve_under_root(ROOT, ROOT), ROOT)

    def test_trailing_slash_normalized(self):
        self.assertEqual(cl.resolve_under_root("/home/u/scratch/sub/", ROOT),
                         "/home/u/scratch/sub")

    def test_empty_target_rejected(self):
        self.assertIsNone(cl.resolve_under_root("", ROOT))


if __name__ == "__main__":
    unittest.main()
