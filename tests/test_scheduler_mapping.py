"""Tests for cluster_lib: scheduler mapping, state translation, escape-trap guard.

Run: python -m unittest discover -s .claude/skills/cluster-connect/tests -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import cluster_lib as cl


class TestSubmitCommand(unittest.TestCase):
    def test_slurm_basic(self):
        self.assertEqual(cl.build_submit_command("slurm", "job.sh"), "sbatch job.sh")

    def test_slurm_with_resources(self):
        cmd = cl.build_submit_command("slurm", "job.sh", nodes=2, gpus=4,
                                      time_limit="24:00:00", partition="gpu",
                                      account="pi", workdir="/home/u/scratch/run1")
        self.assertIn("sbatch", cmd)
        self.assertIn("--nodes=2", cmd)
        self.assertIn("--gres=gpu:4", cmd)
        self.assertIn("--time=24:00:00", cmd)
        self.assertIn("--partition=gpu", cmd)
        self.assertIn("--account=pi", cmd)
        self.assertIn("--chdir=/home/u/scratch/run1", cmd)
        self.assertTrue(cmd.endswith("job.sh"))

    def test_pbs_basic(self):
        self.assertEqual(cl.build_submit_command("pbs", "job.sh"), "qsub job.sh")

    def test_pbs_with_resources(self):
        cmd = cl.build_submit_command("pbs", "job.sh", nodes=2, gpus=4,
                                      time_limit="24:00:00", partition="gpuq",
                                      workdir="/home/u/scratch/run1")
        self.assertIn("qsub", cmd)
        self.assertIn("nodes=2", cmd)
        self.assertIn("gpus=4", cmd)
        self.assertIn("walltime=24:00:00", cmd)
        self.assertIn("-q gpuq", cmd)
        self.assertIn("-d /home/u/scratch/run1", cmd)

    def test_unknown_scheduler_raises(self):
        with self.assertRaises(ValueError):
            cl.build_submit_command("lsf", "job.sh")


class TestStateTranslation(unittest.TestCase):
    def test_slurm_running(self):
        self.assertEqual(cl.translate_state("slurm", "R"), "RUNNING")
        self.assertEqual(cl.translate_state("slurm", "RUNNING"), "RUNNING")

    def test_slurm_completing_is_running_not_failed(self):
        self.assertEqual(cl.translate_state("slurm", "CG"), "RUNNING")
        self.assertEqual(cl.translate_state("slurm", "COMPLETING"), "RUNNING")

    def test_slurm_pending(self):
        self.assertEqual(cl.translate_state("slurm", "PD"), "QUEUED")

    def test_slurm_timeout(self):
        self.assertEqual(cl.translate_state("slurm", "TIMEOUT"), "TIMEOUT")

    def test_pbs_exiting_is_running_not_failed(self):
        self.assertEqual(cl.translate_state("pbs", "E"), "RUNNING")

    def test_pbs_completed_not_cancelled(self):
        self.assertEqual(cl.translate_state("pbs", "C"), "COMPLETED")
        self.assertEqual(cl.translate_state("pbs", "W"), "CANCELLED")

    def test_unknown_state(self):
        self.assertEqual(cl.translate_state("slurm", "WEIRD"), "UNKNOWN")


class TestEscapeTrapGuard(unittest.TestCase):
    def test_rm_rf_root(self):
        self.assertTrue(cl.is_dangerous("rm -rf /")[0])

    def test_rm_rf_home(self):
        self.assertTrue(cl.is_dangerous("rm -rf ~")[0])
        self.assertTrue(cl.is_dangerous("rm -rf $HOME")[0])

    def test_rm_rf_subdir_under_root_is_safe(self):
        # rm -rf of a subdir is a legal operation, must NOT be trapped
        self.assertFalse(cl.is_dangerous("rm -rf /home/u/scratch/old_run")[0])

    def test_mkfs(self):
        self.assertTrue(cl.is_dangerous("mkfs.ext4 /dev/sda1")[0])

    def test_fork_bomb(self):
        self.assertTrue(cl.is_dangerous(":(){ :|:& };:")[0])

    def test_normal_command_safe(self):
        self.assertFalse(cl.is_dangerous("ls -la /home/u/scratch")[0])
        self.assertFalse(cl.is_dangerous("cat INCAR")[0])

    def test_balanced_quotes_safe(self):
        self.assertFalse(cl.is_dangerous("echo 'hello world'")[0])

    def test_unbalanced_quote_trapped(self):
        self.assertTrue(cl.is_dangerous("echo 'unclosed")[0])

    def test_ssh_escape_sequence_trapped(self):
        self.assertTrue(cl.is_dangerous("~.   disconnect")[0])

    def test_tilde_in_middle_safe(self):
        self.assertFalse(cl.is_dangerous("cd ~")[0])
        self.assertFalse(cl.is_dangerous("cp ~/.bashrc /tmp/")[0])


class TestStatusFormatAndParse(unittest.TestCase):
    def test_slurm_parse(self):
        out = "JOBID|STATE|PARTITION|NODES|TIME\n12345|R|compute|2|01:00:00\n12346|CG|compute|1|00:05:00\n"
        rows = cl.parse_status("slurm", out)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["jobid"], "12345")
        self.assertEqual(rows[0]["state"], "RUNNING")
        self.assertEqual(rows[1]["state"], "RUNNING")  # CG -> RUNNING

    def test_pbs_parse(self):
        out = ("Job id            Name             User              Time Use S Queue\n"
               "----------------  ---------------- ----------------  -------- - -----\n"
               "123.host          job              user              00:01:00 R workq\n")
        rows = cl.parse_status("pbs", out)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["jobid"], "123.host")
        self.assertEqual(rows[0]["state"], "RUNNING")
        self.assertEqual(rows[0]["queue"], "workq")


if __name__ == "__main__":
    unittest.main()
