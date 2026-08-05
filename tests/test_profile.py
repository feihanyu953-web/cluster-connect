"""Tests for profile schema validation (validate_profile).

Run: python -m unittest discover -s .claude/skills/cluster-connect/tests -v
"""
import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import cluster_lib as cl

VALID = {
    "profile_name": "t",
    "ssh_alias": "cluster",
    "scheduler": {"type": "slurm", "partition": "compute",
                  "gpu_partition": "compute", "default_account": None},
    "hardware": {"cores_per_node": 56, "gpus_per_node": 2},
    "paths": {"root_dir": "/home/u/scratch"},
    "apps": {},
}


def mutate(**changes):
    p = copy.deepcopy(VALID)
    for k, v in changes.items():
        p[k] = v
    return p


class TestValidateProfile(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(cl.validate_profile(VALID), [])

    def test_missing_name(self):
        p = copy.deepcopy(VALID)
        del p["profile_name"]
        self.assertTrue(cl.validate_profile(p))

    def test_missing_alias(self):
        p = copy.deepcopy(VALID)
        del p["ssh_alias"]
        self.assertTrue(cl.validate_profile(p))

    def test_bad_scheduler(self):
        p = copy.deepcopy(VALID)
        p["scheduler"]["type"] = "lsf"
        self.assertTrue(cl.validate_profile(p))

    def test_cores_zero(self):
        p = copy.deepcopy(VALID)
        p["hardware"]["cores_per_node"] = 0
        self.assertTrue(cl.validate_profile(p))

    def test_cores_bool_rejected(self):
        # bool is an int subclass in Python; must not pass as a core count
        p = copy.deepcopy(VALID)
        p["hardware"]["cores_per_node"] = True
        self.assertTrue(cl.validate_profile(p))

    def test_gpus_negative(self):
        p = copy.deepcopy(VALID)
        p["hardware"]["gpus_per_node"] = -1
        self.assertTrue(cl.validate_profile(p))

    def test_root_relative(self):
        p = copy.deepcopy(VALID)
        p["paths"]["root_dir"] = "scratch"
        self.assertTrue(cl.validate_profile(p))

    def test_root_slash_rejected(self):
        p = copy.deepcopy(VALID)
        p["paths"]["root_dir"] = "/"
        self.assertTrue(cl.validate_profile(p))


if __name__ == "__main__":
    unittest.main()
