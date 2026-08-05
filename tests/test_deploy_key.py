"""Tests for setup.py: --deploy-key command construction and password hygiene.

Verifies that the password never reaches the persisted profile/config (the
deploy_key_core return value and write_profile output must not contain it).

Run: python -m unittest discover -s .claude/skills/cluster-connect/tests -v
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import setup as su


class TestBuildDeployCmd(unittest.TestCase):
    def test_basic(self):
        cmd = su.build_deploy_key_cmd("10.0.0.1", "22", "u", "~/.ssh/id_ed25519.pub")
        self.assertEqual(cmd[0], "ssh-copy-id")
        self.assertIn("-i", cmd)
        self.assertIn("u@10.0.0.1", cmd)

    def test_no_port_flag_for_22(self):
        self.assertNotIn("-p", su.build_deploy_key_cmd("h", "22", "u", "k.pub"))

    def test_custom_port(self):
        cmd = su.build_deploy_key_cmd("h", "2022", "u", "k.pub")
        self.assertIn("-p", cmd)
        self.assertIn("2022", cmd)


class TestDeployKeyCore(unittest.TestCase):
    def test_result_has_no_password(self):
        def runner(cmd):
            return 0
        res = su.deploy_key_core("10.0.0.1", "22", "u", "k.pub", "SECRET123",
                                 runner, platform="linux")
        self.assertNotIn("SECRET123", str(res))

    def test_linux_invokes_ssh_copy_id(self):
        seen = {}
        def runner(cmd):
            seen["cmd"] = cmd
            return 0
        su.deploy_key_core("h", "22", "u", "k.pub", "pw", runner, platform="linux")
        self.assertIn("ssh-copy-id", seen["cmd"])

    def test_windows_returns_manual_without_running(self):
        called = {"n": 0}
        def runner(cmd):
            called["n"] += 1
            return 0
        res = su.deploy_key_core("h", "22", "u", "k.pub", "pw", runner, platform="win32")
        self.assertEqual(called["n"], 0)
        self.assertEqual(res["status"], "manual")
        self.assertIn("instructions", res)
        self.assertNotIn("pw", str(res))


class TestWriteProfileNoPassword(unittest.TestCase):
    def test_password_stripped_from_persisted_profile(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "profile.json")
            profile = {
                "profile_name": "t", "ssh_alias": "c",
                "scheduler": {"type": "slurm", "partition": "c",
                              "gpu_partition": "c", "default_account": None},
                "hardware": {"cores_per_node": 1, "gpus_per_node": 0},
                "paths": {"root_dir": "/h/scratch"}, "apps": {},
                "_password": "SHOULD_BE_STRIPPED",
            }
            su.write_profile(profile, p)
            with open(p) as f:
                text = f.read()
            self.assertNotIn("SHOULD_BE_STRIPPED", text)
            self.assertNotIn("password", text.lower())


if __name__ == "__main__":
    unittest.main()
