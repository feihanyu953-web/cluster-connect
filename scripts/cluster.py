#!/usr/bin/env python3
"""cluster.py - CLI for the cluster-connect skill.

Subcommands: doctor, submit, status, delete, push, pull, log, exec.
Reads a profile from ~/.cluster-skill/<name>.json (default: profile.json).
All remote file operations are sandboxed to paths.root_dir; paths that escape
are rejected. exec/submit are guarded against destructive commands and SSH
escape sequences (see cluster_lib.is_dangerous).

Stdlib only. Requires ssh on PATH; push/pull use rsync when available and
fall back to scp when rsync is missing (e.g. stock Windows OpenSSH).

Usage:
    python cluster.py [--profile NAME] doctor [--fix]
    python cluster.py [--profile NAME] submit <script> [--nodes N] [--gpus G]
                       [--time T] [--partition P] [--account A]
                       [--workdir DIR] [--yes]
    python cluster.py [--profile NAME] status [jobid] [--all] [--watch]
    python cluster.py [--profile NAME] delete <jobid>
    python cluster.py [--profile NAME] push <local> <remote> [--yes]
    python cluster.py [--profile NAME] pull <remote> <local> [--yes]
    python cluster.py [--profile NAME] log [jobid] [-f] [--file FILE]
    python cluster.py [--profile NAME] exec <cmd...>
"""

import argparse
import json
import os
import posixpath
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cluster_lib as cl

PROFILE_DIR = os.path.join(os.path.expanduser("~"), ".cluster-skill")
CONNECT_TIMEOUT = 15


def die(msg, code=1):
    print("ERROR: " + msg, file=sys.stderr)
    sys.exit(code)


def confirm(prompt):
    try:
        return input("%s [y/N] " % prompt).strip().lower() in ("y", "yes")
    except EOFError:
        return False


def rsync_available():
    """Windows lacks rsync; push/pull fall back to scp when it is absent."""
    return shutil.which("rsync") is not None


def profile_path(name):
    if name == "default":
        return os.path.join(PROFILE_DIR, "profile.json")
    return os.path.join(PROFILE_DIR, name + ".json")


def load_profile(name):
    ppath = profile_path(name)
    if not os.path.exists(ppath):
        die("profile not found: %s\n  run: python scripts/setup.py --profile %s" % (ppath, name))
    try:
        with open(ppath) as f:
            profile = json.load(f)
    except json.JSONDecodeError as e:
        die("profile JSON parse error: %s" % e)
    errors = cl.validate_profile(profile)
    if errors:
        die("profile invalid:\n  " + "\n  ".join(errors))
    profile["_name"] = name
    return profile


def alias(profile):
    return profile["ssh_alias"]


def build_remote(profile, cmd):
    """ssh argv that runs cmd on the cluster with cwd at root_dir. The inner
    string is parsed by the remote shell, so $VAR expands remotely; is_dangerous
    guards exec/submit before this is called. No init.sh - job scripts manage
    their own module environment."""
    inner = "cd %s; %s" % (profile["paths"]["root_dir"], cmd)
    return ["ssh", alias(profile), inner]


def remote_exec(profile, cmd, check=False, capture=False):
    return subprocess.run(build_remote(profile, cmd), check=check,
                          capture_output=capture, text=True)


def control_check(profile):
    """Probe the ControlMaster socket with zero network (ssh -O check)."""
    try:
        r = subprocess.run(["ssh", "-O", "check", alias(profile)],
                           capture_output=True, text=True, timeout=10)
        return (r.returncode == 0,
                "alive" if r.returncode == 0 else "no master socket")
    except subprocess.TimeoutExpired:
        return False, "check timed out"
    except FileNotFoundError:
        return False, "ssh not on PATH"


# --------------------------------------------------------------------------
# subcommands

def cmd_doctor(profile, args):
    name = profile.get("profile_name", "?")
    print("== cluster-connect doctor: %s ==" % name)
    ok_all = True

    if not shutil.which("ssh"):
        print("[FAIL] ssh not on PATH")
        return 1
    print("[OK]   ssh client found")

    ok, msg = control_check(profile)
    print("[%s] control master: %s" % ("OK  " if ok else "WARN", msg))

    try:
        r = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=%d" % CONNECT_TIMEOUT,
             alias(profile), "true"],
            capture_output=True, text=True, timeout=CONNECT_TIMEOUT + 5)
        if r.returncode == 0:
            print("[OK]   ssh %s reachable (key auth)" % alias(profile))
        else:
            print("[FAIL] ssh %s unreachable: %s" % (alias(profile), r.stderr.strip()))
            print("       -> if password is required, run: setup.py --deploy-key")
            return 1
    except subprocess.TimeoutExpired:
        print("[FAIL] ssh timed out (VPN down? jumpbox off?)")
        return 1

    stype = profile["scheduler"]["type"]
    probe = "sinfo" if stype == "slurm" else "qstat"
    r = remote_exec(profile, "command -v %s" % probe, capture=True)
    if r.returncode == 0 and r.stdout.strip():
        print("[OK]   scheduler command '%s' found" % probe)
    else:
        print("[FAIL] scheduler command '%s' not found on cluster" % probe)
        ok_all = False

    rd = profile["paths"]["root_dir"]
    r = remote_exec(profile, "test -d %s && test -w %s && echo OK" % (rd, rd), capture=True)
    if "OK" in r.stdout:
        print("[OK]   root_dir %s exists and is writable" % rd)
    else:
        print("[FAIL] root_dir %s missing or not writable - check the path is correct" % rd)
        ok_all = False

    print("== doctor: %s ==" % ("all checks passed" if ok_all else "issues found"))
    return 0 if ok_all else 1


def cmd_submit(profile, args):
    rd = profile["paths"]["root_dir"]
    script = cl.resolve_under_root(args.script, rd)
    if script is None:
        die("submit: script path %r escapes root_dir %r" % (args.script, rd))
    workdir = cl.resolve_under_root(args.workdir or rd, rd)
    if workdir is None:
        die("submit: --workdir %r escapes root_dir %r" % (args.workdir, rd))

    sched = profile["scheduler"]
    partition = args.partition or sched["partition"]
    account = args.account or sched.get("default_account")
    cmd = cl.build_submit_command(
        sched["type"], script, nodes=args.nodes, gpus=args.gpus,
        time_limit=args.time, partition=partition, account=account, workdir=workdir)
    bad, reason = cl.is_dangerous(cmd)
    if bad:
        die("submit rejected: %s" % reason)

    print("Will run on cluster:\n  %s" % cmd)
    if not args.yes and not confirm("Submit?"):
        print("aborted")
        return 1
    r = remote_exec(profile, cmd, capture=True)
    sys.stdout.write(r.stdout)
    if r.stderr:
        sys.stderr.write(r.stderr)
    return r.returncode


def _print_status(profile, args):
    sched = profile["scheduler"]
    if args.jobid:
        if sched["type"] == "slurm":
            cmd = "sacct -j %s --format=JobID,State,Partition,Nodes,Elapsed" % args.jobid
        else:
            cmd = "qstat -f %s" % args.jobid
        r = remote_exec(profile, cmd, capture=True)
        sys.stdout.write(r.stdout)
        return r.returncode
    user = None
    if not args.all:
        ru = remote_exec(profile, "whoami", capture=True)
        user = ru.stdout.strip() or None
    query = cl.format_status_query(sched["type"], user=user)
    r = remote_exec(profile, query, capture=True)
    rows = cl.parse_status(sched["type"], r.stdout)
    if not rows:
        print("(no jobs)")
        return 0
    print("%-14s %-10s %-10s %-6s %-10s" % ("JOBID", "STATE", "QUEUE", "NODES", "TIME"))
    for row in rows:
        print("%-14s %-10s %-10s %-6s %-10s" % (
            row["jobid"], row["state"], row["queue"], row["nodes"], row["time"]))
    return 0


def cmd_status(profile, args):
    if args.watch:
        print("(watching every 30s; Ctrl+C to stop. In an AI session prefer the Monitor tool.)")
        try:
            while True:
                _print_status(profile, args)
                time.sleep(30)
        except KeyboardInterrupt:
            return 0
    return _print_status(profile, args)


def cmd_delete(profile, args):
    stype = profile["scheduler"]["type"]
    cmd = ("scancel %s" if stype == "slurm" else "qdel %s") % args.jobid
    r = remote_exec(profile, cmd, capture=True)
    sys.stdout.write(r.stdout)
    if r.stderr:
        sys.stderr.write(r.stderr)
    return r.returncode


def cmd_push(profile, args):
    rd = profile["paths"]["root_dir"]
    dest_remote = cl.resolve_under_root(args.remote, rd)
    if dest_remote is None:
        die("push: remote path %r escapes root_dir %r" % (args.remote, rd))
    if not os.path.exists(args.local):
        die("push: local path %r does not exist" % args.local)
    if rsync_available():
        argv = ["rsync", "-avz", "-e", "ssh", args.local,
                "%s:%s" % (alias(profile), dest_remote)]
    else:
        # scp fallback: rsync creates remote dirs, scp does not - mkdir first.
        pre = ["ssh", alias(profile), "mkdir -p %s" % posixpath.dirname(dest_remote)]
        print(" ".join(pre))
        if subprocess.run(pre).returncode != 0:
            return 1
        argv = ["scp", "-r", args.local, "%s:%s" % (alias(profile), dest_remote)]
    print(" ".join(argv))
    if not args.yes and not confirm("Transfer?"):
        return 1
    return subprocess.run(argv).returncode


def cmd_pull(profile, args):
    rd = profile["paths"]["root_dir"]
    src_remote = cl.resolve_under_root(args.remote, rd)
    if src_remote is None:
        die("pull: remote path %r escapes root_dir %r" % (args.remote, rd))
    if rsync_available():
        argv = ["rsync", "-avz", "-e", "ssh",
                "%s:%s" % (alias(profile), src_remote), args.local]
    else:
        # scp fallback: scp cannot create the local target dir - ensure it.
        local_dir = os.path.dirname(os.path.abspath(args.local))
        if local_dir and not os.path.isdir(local_dir):
            os.makedirs(local_dir)
        argv = ["scp", "-r", "%s:%s" % (alias(profile), src_remote), args.local]
    print(" ".join(argv))
    if not args.yes and not confirm("Transfer?"):
        return 1
    return subprocess.run(argv).returncode


def cmd_log(profile, args):
    rd = profile["paths"]["root_dir"]
    if args.file:
        path = cl.resolve_under_root(args.file, rd)
        if path is None:
            die("log: --file %r escapes root_dir %r" % (args.file, rd))
    elif args.jobid:
        if profile["scheduler"]["type"] == "slurm":
            path = cl.resolve_under_root("slurm-%s.out" % args.jobid, rd)
        else:
            # PBS: <jobname>.o<jobid> - jobname unknown; fall back to root_dir glob
            path = cl.resolve_under_root("*.o%s" % args.jobid.split(".")[0], rd)
        if path is None:
            die("log: derived log path escapes root_dir")
    else:
        path = cl.resolve_under_root("*.out", rd)
        if path is None:
            die("log: log path escapes root_dir")
    tail = "tail -F" if args.follow else "tail -n 200"
    return subprocess.run(build_remote(profile, "%s %s" % (tail, path))).returncode


def cmd_exec(profile, args):
    cmd = " ".join(args.cmd)
    bad, reason = cl.is_dangerous(cmd)
    if bad:
        die("exec rejected: %s" % reason)
    return subprocess.run(build_remote(profile, cmd)).returncode


def cmd_profiles(args):
    """List all configured profiles in ~/.cluster-skill/."""
    import glob
    files = sorted(glob.glob(os.path.join(PROFILE_DIR, "*.json")))
    if not files:
        print("(no profiles in %s - run setup.py first)" % PROFILE_DIR)
        return 0
    print("%-14s %-14s %-8s %s" % ("PROFILE", "SSH_ALIAS", "SCHED", "ROOT_DIR"))
    for f in files:
        name = os.path.splitext(os.path.basename(f))[0]
        if name == "profile":
            name = "default"
        try:
            with open(f) as fh:
                p = json.load(fh)
            print("%-14s %-14s %-8s %s" % (
                name, p.get("ssh_alias", "?"),
                (p.get("scheduler") or {}).get("type", "?"),
                (p.get("paths") or {}).get("root_dir", "?")))
        except (OSError, json.JSONDecodeError):
            print("%-14s (unreadable: %s)" % (name, f))
    return 0


def main():
    ap = argparse.ArgumentParser(prog="cluster.py", description="cluster-connect CLI")
    ap.add_argument("--profile", default="default")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("doctor")

    p = sub.add_parser("submit")
    p.add_argument("script")
    p.add_argument("--nodes", type=int)
    p.add_argument("--gpus", type=int)
    p.add_argument("--time", dest="time_limit")
    p.add_argument("--partition")
    p.add_argument("--account")
    p.add_argument("--workdir")
    p.add_argument("--yes", action="store_true")

    p = sub.add_parser("status")
    p.add_argument("jobid", nargs="?")
    p.add_argument("--all", action="store_true")
    p.add_argument("--watch", action="store_true")

    p = sub.add_parser("delete")
    p.add_argument("jobid")

    p = sub.add_parser("push")
    p.add_argument("local")
    p.add_argument("remote")
    p.add_argument("--yes", action="store_true")

    p = sub.add_parser("pull")
    p.add_argument("remote")
    p.add_argument("local")
    p.add_argument("--yes", action="store_true")

    p = sub.add_parser("log")
    p.add_argument("jobid", nargs="?")
    p.add_argument("-f", "--follow", action="store_true")
    p.add_argument("--file")

    p = sub.add_parser("exec")
    p.add_argument("cmd", nargs=argparse.REMAINDER)

    sub.add_parser("profiles")

    args = ap.parse_args()
    if args.command == "profiles":
        sys.exit(cmd_profiles(args) or 0)
    profile = load_profile(args.profile)
    dispatch = {
        "doctor": cmd_doctor, "submit": cmd_submit, "status": cmd_status,
        "delete": cmd_delete, "push": cmd_push, "pull": cmd_pull,
        "log": cmd_log, "exec": cmd_exec,
    }
    sys.exit(dispatch[args.command](profile, args) or 0)


if __name__ == "__main__":
    main()
