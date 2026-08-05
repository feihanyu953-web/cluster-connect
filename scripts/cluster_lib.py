#!/usr/bin/env python3
"""cluster_lib.py - pure functions for the cluster-connect skill.

Scheduler command mapping (Slurm / PBS-Torque), state-code translation,
resource-flag rendering, SSH escape-trap guarding, status-query formatting,
status parsing, and root_dir sandbox path validation.

Stdlib only. No network, no filesystem writes. Safe to unit-test offline.

See references/schedulers.md for the human-readable mapping table; this module
is the executable ground truth that must stay in sync with it.
"""

import posixpath
import re

SCHEDULERS = ("slurm", "pbs")

# Normalized labels: QUEUED / RUNNING / COMPLETED / FAILED / TIMEOUT / CANCELLED / HELD / UNKNOWN
# Slurm: COMPLETING is NOT failed (still finalizing). PBS: E (Exiting) is NOT Error,
# C is Completed (not Cancelled), W is the deleted/cancelled marker.
SLURM_STATES = {
    "PENDING": "QUEUED", "PD": "QUEUED",
    "RUNNING": "RUNNING", "R": "RUNNING",
    "COMPLETING": "RUNNING", "CG": "RUNNING",
    "COMPLETED": "COMPLETED", "CD": "COMPLETED",
    "FAILED": "FAILED", "F": "FAILED",
    "TIMEOUT": "TIMEOUT", "TO": "TIMEOUT",
    "CANCELLED": "CANCELLED", "CA": "CANCELLED",
    "HELD": "HELD", "SUSPENDED": "HELD", "S": "HELD",
}

PBS_STATES = {
    "Q": "QUEUED",
    "R": "RUNNING",
    "E": "RUNNING",
    "C": "COMPLETED",
    "F": "FAILED",
    "H": "HELD",
    "W": "CANCELLED",
    "T": "QUEUED",
    "S": "HELD",
}

# Destructive command patterns. Note: rm -rf of a subdir UNDER root_dir is legal
# and must NOT be trapped - we only catch rm -rf of / , ~ , $HOME.
DANGEROUS_PATTERNS = [
    re.compile(r"\brm\s+-rf?\s+/(?:\s|$)"),
    re.compile(r"\brm\s+-rf?\s+~(?:\s|$)"),
    re.compile(r"\brm\s+-rf?\s+\$HOME\b"),
    re.compile(r"\bmkfs\b"),
    re.compile(r"\bdd\b.*of=/dev/"),
    re.compile(r":\s*\(\s*\)\s*\{"),  # fork bomb :(){}
]

# SSH client escape sequences (only matter at line start of an interactive
# session, but we block them anywhere a line starts with one).
SSH_ESCAPE_PREFIX = ("~.", "~^Z", "~-", "~&", "~#", "~C", "~R", "~s", "~Z", "~?")


def is_dangerous(cmd):
    """Return (bool, reason). Detects destructive commands, unbalanced single
    quotes (which break the ``bash -lc '...'`` wrapper), and SSH escape
    sequences. Used to guard ``exec``/``submit`` before any remote call."""
    if not cmd:
        return False, ""
    for pat in DANGEROUS_PATTERNS:
        if pat.search(cmd):
            return True, "destructive pattern: " + pat.pattern
    if cmd.count("'") % 2 != 0:
        return True, "unbalanced single quote (breaks bash -lc wrapper)"
    for line in cmd.splitlines():
        stripped = line.lstrip()
        for esc in SSH_ESCAPE_PREFIX:
            if stripped.startswith(esc):
                return True, "SSH escape sequence: " + esc
    return False, ""


def render_resource_flags(scheduler, nodes=None, gpus=None, time_limit=None,
                          partition=None, account=None):
    """Render scheduler-specific resource declaration flags as a list."""
    if scheduler == "slurm":
        flags = []
        if nodes:
            flags.append("--nodes=%s" % nodes)
        if gpus:
            flags.append("--gres=gpu:%s" % gpus)
        if time_limit:
            flags.append("--time=%s" % time_limit)
        if partition:
            flags.append("--partition=%s" % partition)
        if account:
            flags.append("--account=%s" % account)
        return flags
    if scheduler == "pbs":
        selects = []
        if nodes:
            selects.append("nodes=%s" % nodes)
        if gpus:
            selects.append("gpus=%s" % gpus)
        if time_limit:
            selects.append("walltime=%s" % time_limit)
        flags = []
        if selects:
            flags.append("-l " + ":".join(selects))
        if partition:
            flags.append("-q %s" % partition)
        if account:
            flags.append("-A %s" % account)
        return flags
    raise ValueError("unknown scheduler: %s" % scheduler)


def build_submit_command(scheduler, script, nodes=None, gpus=None,
                         time_limit=None, partition=None, account=None,
                         workdir=None):
    """Build the full submit command string (sbatch / qsub)."""
    flags = render_resource_flags(scheduler, nodes, gpus, time_limit, partition, account)
    if scheduler == "slurm":
        parts = ["sbatch"] + flags
        if workdir:
            parts.append("--chdir=%s" % workdir)
        parts.append(script)
        return " ".join(parts)
    if scheduler == "pbs":
        parts = ["qsub"] + flags
        if workdir:
            parts.append("-d %s" % workdir)
        parts.append(script)
        return " ".join(parts)
    raise ValueError("unknown scheduler: %s" % scheduler)


def translate_state(scheduler, raw):
    """Translate a raw scheduler state code to a normalized label.
    Exact match only - scheduler state codes are a fixed enumeration, so
    substring fallback would let a single-char key like 'R' match 'WEIRD'."""
    table = SLURM_STATES if scheduler == "slurm" else PBS_STATES
    raw = (raw or "").strip().upper()
    return table.get(raw, "UNKNOWN")


def format_status_query(scheduler, user=None):
    """Return the status-query command. Slurm uses a pipe-delimited --format so
    parse_status can split reliably; PBS uses the default qstat table."""
    if scheduler == "slurm":
        cmd = "squeue --format='%i|%T|%P|%D|%M'"
        if user:
            cmd += " -u %s" % user
        return cmd
    if scheduler == "pbs":
        cmd = "qstat"
        if user:
            cmd += " -u %s" % user
        return cmd
    raise ValueError("unknown scheduler: %s" % scheduler)


def parse_status(scheduler, text):
    """Parse scheduler status output into unified dicts:
    {jobid, state, raw_state, queue, nodes, time}."""
    rows = []
    if scheduler == "slurm":
        for line in (text or "").splitlines():
            line = line.strip()
            if not line or "|" not in line or line.lower().startswith("jobid"):
                continue
            parts = line.split("|")
            if len(parts) < 5:
                continue
            jobid, state, partition, nodes, time = parts[0], parts[1], parts[2], parts[3], parts[4]
            rows.append({
                "jobid": jobid,
                "state": translate_state("slurm", state),
                "raw_state": state,
                "queue": partition,
                "nodes": nodes,
                "time": time,
            })
    elif scheduler == "pbs":
        for line in (text or "").splitlines():
            line = line.strip()
            if not line or line.startswith("Job id"):
                continue
            if set(line) <= set("- "):
                continue
            parts = line.split()
            if len(parts) < 6:
                continue
            jobid, time, state, queue = parts[0], parts[3], parts[4], parts[5]
            rows.append({
                "jobid": jobid,
                "state": translate_state("pbs", state),
                "raw_state": state,
                "queue": queue,
                "nodes": "",
                "time": time,
            })
    return rows


def resolve_under_root(target, root_dir):
    """Lexically resolve ``target`` relative to ``root_dir`` (POSIX semantics,
    no filesystem access). Return the normalized absolute path if it stays
    under root_dir, else None. Blocks ``..`` escapes and absolute-path
    overreach. root_dir must be an absolute POSIX path."""
    if not target or not root_dir:
        return None
    root_dir = posixpath.normpath(root_dir)
    if posixpath.isabs(target):
        cand = posixpath.normpath(target)
    else:
        cand = posixpath.normpath(posixpath.join(root_dir, target))
    if root_dir == "/":
        return cand
    if cand == root_dir:
        return cand
    if cand.startswith(root_dir + "/"):
        return cand
    return None


def validate_profile(profile):
    """Return a list of error strings; empty list means valid. Schema check
    only - does not verify the cluster is reachable."""
    if not isinstance(profile, dict):
        return ["profile must be a JSON object"]
    errors = []
    if not profile.get("profile_name"):
        errors.append("profile_name missing")
    if not profile.get("ssh_alias"):
        errors.append("ssh_alias missing")
    sched = profile.get("scheduler") or {}
    if sched.get("type") not in SCHEDULERS:
        errors.append("scheduler.type must be one of %s" % (SCHEDULERS,))
    if not sched.get("partition"):
        errors.append("scheduler.partition missing")
    hw = profile.get("hardware") or {}
    cores = hw.get("cores_per_node")
    if not isinstance(cores, int) or isinstance(cores, bool) or cores <= 0:
        errors.append("hardware.cores_per_node must be a positive integer")
    gpus = hw.get("gpus_per_node")
    if not isinstance(gpus, int) or isinstance(gpus, bool) or gpus < 0:
        errors.append("hardware.gpus_per_node must be a non-negative integer")
    rd = (profile.get("paths") or {}).get("root_dir")
    if not rd or not posixpath.isabs(rd):
        errors.append("paths.root_dir must be an absolute POSIX path")
    elif rd == "/":
        errors.append("paths.root_dir must not be '/' (sandbox too broad)")
    return errors
