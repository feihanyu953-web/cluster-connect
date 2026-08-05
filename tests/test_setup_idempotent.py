"""Tests for setup.py: ssh config block generation + idempotent replacement.

Run: python -m unittest discover -s .claude/skills/cluster-connect/tests -v
"""
import copy
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import setup as su

HOPS = [
    {"alias": "jumpbox", "host": "10.0.0.1", "port": "22",
     "user": "u", "key": "~/.ssh/id_ed25519", "proxy_jump": None},
    {"alias": "cluster", "host": "192.168.1.10", "port": "22",
     "user": "yanli", "key": "~/.ssh/id_ed25519", "proxy_jump": "jumpbox"},
]


class TestHostBlock(unittest.TestCase):
    def test_contains_markers_and_controlmaster(self):
        block = su.build_host_block("default", HOPS)
        self.assertIn("# BEGIN cluster-connect:default", block)
        self.assertIn("# END cluster-connect:default", block)
        self.assertIn("ControlMaster auto", block)
        self.assertIn("ControlPersist 12h", block)
        self.assertIn("ProxyJump jumpbox", block)

    def test_no_port_22_line(self):
        block = su.build_host_block("default", HOPS)
        self.assertNotIn("    Port 22", block)

    def test_custom_port_written(self):
        hops = [dict(HOPS[0], port="2022")]
        self.assertIn("    Port 2022", su.build_host_block("default", hops))


class TestReplaceBlock(unittest.TestCase):
    def test_first_write_appends(self):
        existing = "Host other\n    User x\n"
        new, replaced = su.replace_block(existing, "default", su.build_host_block("default", HOPS))
        self.assertFalse(replaced)
        self.assertIn("Host other", new)
        self.assertIn("BEGIN cluster-connect:default", new)

    def test_second_write_replaces_in_place(self):
        block1 = su.build_host_block("default", HOPS)
        existing = "Host keep\n    User x\n" + block1
        hops2 = copy.deepcopy(HOPS)
        hops2[1] = dict(hops2[1], host="192.168.1.99")
        new, replaced = su.replace_block(existing, "default", su.build_host_block("default", hops2))
        self.assertTrue(replaced)
        self.assertIn("Host keep", new)          # outside preserved
        self.assertIn("192.168.1.99", new)       # new hop in
        self.assertNotIn("192.168.1.10", new)    # old hop gone
        self.assertEqual(new.count("BEGIN cluster-connect:default"), 1)  # no dup


class TestUpdateSshConfig(unittest.TestCase):
    def test_idempotent_writes_and_backup(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = os.path.join(d, "config")
            # first write
            su.update_ssh_config("default", HOPS, cfg)
            with open(cfg) as f:
                t1 = f.read()
            self.assertIn("BEGIN cluster-connect:default", t1)
            self.assertEqual(t1.count("BEGIN cluster-connect:default"), 1)

            # inject outside content + re-run with a changed hop
            with open(cfg, "w") as f:
                f.write("Host keep\n    User x\n" + t1)
            hops2 = copy.deepcopy(HOPS)
            hops2[1] = dict(hops2[1], host="192.168.1.99")
            su.update_ssh_config("default", hops2, cfg)
            with open(cfg) as f:
                t2 = f.read()
            self.assertEqual(t2.count("BEGIN cluster-connect:default"), 1)  # still one block
            self.assertIn("Host keep", t2)         # outside preserved
            self.assertIn("192.168.1.99", t2)      # updated
            self.assertTrue(os.path.exists(cfg + ".bak"))


if __name__ == "__main__":
    unittest.main()
