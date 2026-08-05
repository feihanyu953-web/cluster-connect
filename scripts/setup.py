#!/usr/bin/env python3
"""setup.py - first-time configuration for the cluster-connect skill.

Two modes:
  default       interactive full setup: topology -> ssh config -> scheduler
                probe -> profile.json + init.sh
  --deploy-key  one-time public-key deployment (password login once ->
                ssh-copy-id -> verify passwordless). Password is kept in
                memory only; never written to profile/config/logs.

Stdlib only. Requires ssh on PATH (and ssh-copy-id or sshpass for --deploy-key
on Linux/macOS; on Windows the manual authorized_keys step is printed).

Usage:
    python setup.py [--profile NAME] [--reprobe] [--force]
    python setup.py --deploy-key [--profile NAME]
"""

import argparse
import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cluster_lib as cl

PROFILE_DIR = os.path.join(os.path.expanduser("~"), ".cluster-skill")
SSH_CONFIG = os.path.join(os.path.expanduser("~"), ".ssh", "config")

BEGIN_FMT = "# BEGIN cluster-connect:%s"
END_FMT = "# END cluster-connect:%s"


# --------------------------------------------------------------------------
# pure functions (unit-tested)

def block_markers(profile_name):
    return BEGIN_FMT % profile_name, END_FMT % profile_name


def build_host_block(profile_name, hops):
    """hops: list of dicts with keys alias/host/port/user/key/proxy_jump.
    Returns marker-delimited ssh config text (markers included)."""
    begin, end = block_markers(profile_name)
    lines = [begin]
    for h in hops:
        lines.append("Host %s" % h["alias"])
        lines.append("    HostName %s" % h["host"])
        if h.get("port") and str(h["port"]) != "22":
            lines.append("    Port %s" % h["port"])
        lines.append("    User %s" % h["user"])
        if h.get("key"):
            lines.append("    IdentityFile %s" % h["key"])
        if h.get("proxy_jump"):
            lines.append("    ProxyJump %s" % h["proxy_jump"])
        lines.append("    ControlMaster auto")
        lines.append("    ControlPersist 12h")
        lines.append("    ServerAliveInterval 30")
    lines.append(end)
    return "\n".join(lines) + "\n"


def replace_block(existing, profile_name, new_block):
    """Replace the marked block in existing text with new_block. If markers are
    absent, append. Returns (new_text, replaced_bool)."""
    begin, end = block_markers(profile_name)
    if begin in existing and end in existing:
        start = existing.index(begin)
        end_idx = existing.index(end, start) + len(end)
        if end_idx < len(existing) and existing[end_idx] == "\n":
            end_idx += 1
        return existing[:start] + new_block + existing[end_idx:], True
    if not existing:
        return new_block, False
    sep = "" if existing.endswith("\n") else "\n"
    return existing + sep + new_block, False


def update_ssh_config(profile_name, hops, config_path):
    """Idempotently update the marked block in config_path. Backs up to .bak
    when content changes. Returns (replaced_bool). Creates parent dir."""
    existing = ""
    if os.path.exists(config_path):
        with open(config_path) as f:
            existing = f.read()
    new_block = build_host_block(profile_name, hops)
    new_text, replaced = replace_block(existing, profile_name, new_block)
    if existing and existing != new_text:
        with open(config_path + ".bak", "w") as f:
            f.write(existing)
    os.makedirs(os.path.dirname(config_path) or ".", exist_ok=True)
    with open(config_path, "w") as f:
        f.write(new_text)
    return replaced


def build_deploy_key_cmd(host, port, user, pubkey):
    """ssh-copy-id command (no password in the argv; password is supplied via
    sshpass/expect/tty, never as a persisted file)."""
    cmd = ["ssh-copy-id", "-i", pubkey]
    if port and str(port) != "22":
        cmd += ["-p", str(port)]
    cmd.append("%s@%s" % (user, host))
    return cmd


def deploy_key_core(host, port, user, pubkey, password, runner, platform=None):
    """Deploy pubkey to a remote host. password is used in-memory only.
    runner(cmd_argv) executes the deploy; on Windows (no ssh-copy-id) runner
    is not called and manual instructions are returned instead.
    Returns a dict with status/instructions; NEVER includes the password."""
    if platform is None:
        platform = sys.platform
    result = {"host": host, "user": user}
    if platform.startswith("win"):
        manual = (
            "Windows has no ssh-copy-id. Run manually:\n"
            "  1. ssh -p {port} {user}@{host}   (enter password when prompted)\n"
            "  2. on the remote: mkdir -p ~/.ssh && chmod 700 ~/.ssh\n"
            "  3. append your public key:\n"
            "       echo '{pub}' >> ~/.ssh/authorized_keys\n"
            "       chmod 600 ~/.ssh/authorized_keys\n"
            "  4. verify: ssh -p {port} {user}@{host} true  (must not ask for password)"
        ).format(port=port or 22, user=user, host=host, pub=_read_pubkey(pubkey))
        result["status"] = "manual"
        result["instructions"] = manual
        return result
    # Linux/macOS: use sshpass to feed the password to ssh-copy-id.
    base = build_deploy_key_cmd(host, port, user, pubkey)
    cmd = ["sshpass", "-p", password] + base if shutil.which("sshpass") else base
    rc = runner(cmd)
    result["status"] = "ok" if rc == 0 else "failed"
    result["rc"] = rc
    return result


def _read_pubkey(pubkey):
    try:
        with open(os.path.expanduser(pubkey)) as f:
            return f.read().strip()
    except OSError:
        return "<unreadable>"


def write_profile(profile, path):
    """Write profile.json. The profile dict must not contain a password field;
    this function strips any '_password' key defensively."""
    safe = {k: v for k, v in profile.items() if "password" not in k.lower()}
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump(safe, f, indent=2)
        f.write("\n")


def profile_path(name):
    if name == "default":
        return os.path.join(PROFILE_DIR, "profile.json")
    return os.path.join(PROFILE_DIR, name + ".json")


# --------------------------------------------------------------------------
# remote probes (need a live cluster; not unit-tested)

def ssh(alias, cmd, timeout=20):
    return subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
                           alias, cmd], capture_output=True, text=True, timeout=timeout)


def detect_scheduler(alias):
    """Probe the cluster for Slurm vs PBS. Returns 'slurm' | 'pbs' | None."""
    for stype, probe in (("slurm", "sinfo"), ("pbs", "qstat")):
        try:
            r = ssh(alias, "command -v %s" % probe, timeout=15)
            if r.returncode == 0 and r.stdout.strip():
                return stype
        except subprocess.TimeoutExpired:
            continue
    return None


# --------------------------------------------------------------------------
# interactive helpers

def ask(prompt, default=""):
    suffix = " [%s] " % default if default else ": "
    try:
        val = input(prompt + suffix).strip()
    except EOFError:
        val = ""
    return val or default


def confirm(prompt):
    try:
        return input("%s [y/N] " % prompt).strip().lower() in ("y", "yes")
    except EOFError:
        return False


# --------------------------------------------------------------------------
# modes

def run_deploy_key(args):
    print("== cluster-connect: deploy-key (password -> passwordless) ==")
    host = ask("Target host")
    if not host:
        print("aborted"); return 1
    port = ask("SSH port", "22")
    user = ask("Remote user")
    key = os.path.expanduser(ask("Your private key", "~/.ssh/id_ed25519"))
    pubkey = key + ".pub"
    if not os.path.exists(pubkey):
        print("public key not found: %s - generate with: ssh-keygen -t ed25519 -f %s" % (pubkey, key))
        return 1
    if shutil.which("sshpass") is None and not sys.platform.startswith("win"):
        print("WARNING: sshpass not found. Install it (apt install sshpass / brew install hudochenkov/sshpass/sshpass) "
              "for automated deploy, or follow the manual steps below.")
    password = ""
    if sys.platform.startswith("win") or shutil.which("sshpass"):
        try:
            import getpass
            password = getpass.getpass("Password for %s@%s (kept in memory only): " % (user, host))
        except EOFError:
            password = ""

    def runner(cmd):
        return subprocess.run(cmd).returncode

    result = deploy_key_core(host, port, user, pubkey, password, runner)
    if result.get("status") == "manual":
        print(result["instructions"])
        print("\nAfter completing the manual steps, re-run setup.py to continue.")
        return 0
    if result.get("status") == "ok":
        # verify passwordless
        verify = subprocess.run(["ssh", "-o", "BatchMode=yes", "-p", str(port),
                                 "%s@%s" % (user, host), "true"],
                                capture_output=True, text=True)
        if verify.returncode == 0:
            print("OK: passwordless login verified for %s@%s" % (user, host))
            return 0
        print("WARN: deploy reported ok but passwordless verify failed")
        return 1
    print("FAIL: deploy failed (rc=%s). Check host/user/password." % result.get("rc"))
    return 1


def run_setup(args):
    print("== cluster-connect setup (profile: %s) ==" % args.profile)
    if not shutil.which("ssh"):
        print("ssh not on PATH - install OpenSSH client first."); return 1

    ppath = profile_path(args.profile)
    existing_profile = None
    if os.path.exists(ppath) and not args.force:
        with open(ppath) as f:
            existing_profile = json.load(f)

    # 1. profile name
    profile_name = (existing_profile or {}).get("profile_name") or ask("Profile name", args.profile)

    # 2. topology
    hops = []
    n = int(ask("How many hops to the cluster (1=direct, 2=one jumpbox, 3=two jumpboxes)", "2"))
    for i in range(n):
        is_last = (i == n - 1)
        label = "cluster" if is_last else ("jumpbox" if i == 0 else "jump%d" % i)
        alias_v = ask("  hop %d alias" % (i + 1), label)
        host = ask("  hop %d host" % (i + 1))
        port = ask("  hop %d port" % (i + 1), "22")
        user = ask("  hop %d user" % (i + 1))
        key = ask("  hop %d identity key" % (i + 1), "~/.ssh/id_ed25519")
        proxy = hops[i - 1]["alias"] if i > 0 else None
        hops.append({"alias": alias_v, "host": host, "port": port,
                     "user": user, "key": key, "proxy_jump": proxy})
    final_alias = hops[-1]["alias"]

    # 3. write ssh config (idempotent)
    update_ssh_config(profile_name, hops, SSH_CONFIG)
    print("updated %s (block: %s)" % (SSH_CONFIG, profile_name))

    # 4. connectivity
    try:
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
                            final_alias, "true"], capture_output=True, text=True, timeout=20)
        if r.returncode != 0:
            print("ssh %s failed: %s" % (final_alias, r.stderr.strip()))
            print("-> if password is required, run: python setup.py --deploy-key")
            return 1
        print("ssh %s OK (passwordless)" % final_alias)
    except subprocess.TimeoutExpired:
        print("ssh %s timed out (VPN down? jumpbox off?)" % final_alias); return 1

    # 5. scheduler probe
    stype = detect_scheduler(final_alias)
    if not stype:
        stype = ask("Could not auto-detect scheduler. Enter manually", "slurm").lower()
        if stype not in cl.SCHEDULERS:
            print("scheduler must be one of %s" % (cl.SCHEDULERS,)); return 1
    else:
        print("detected scheduler: %s" % stype)

    partition = ask("partition/queue", "compute")
    gpu_partition = ask("GPU partition (same as above if no separate GPU queue)", partition)
    account = ask("default account (blank if none)", "")

    # 6. root_dir + apps
    default_root = "/home/%s/scratch" % hops[-1]["user"]
    root_dir = ask("root_dir (sandbox root on cluster - must already exist)", default_root)
    if root_dir == "/":
        print("root_dir must not be '/'"); return 1
    # Do NOT create root_dir. Check it exists; if not, warn the user to verify
    # (cluster-connect never creates the sandbox root - user must provide it).
    try:
        r = ssh(final_alias, "test -d %s && echo OK" % root_dir, timeout=15)
        if "OK" not in r.stdout:
            print("WARNING: root_dir %s does not exist on the cluster - doctor will FAIL." % root_dir)
            print("         cluster-connect does not create it; check the path or create it yourself.")
    except subprocess.TimeoutExpired:
        print("WARNING: could not verify root_dir (ssh timed out).")

    apps = {}
    print("Common app paths (blank to skip):")
    for app in ("vasp_std", "lammps", "cp2k"):
        p = ask("  %s path" % app, "")
        if p:
            apps[app] = p

    profile = {
        "profile_name": profile_name,
        "ssh_alias": final_alias,
        "scheduler": {
            "type": stype, "partition": partition,
            "gpu_partition": gpu_partition,
            "default_account": account or None,
        },
        "hardware": {"cores_per_node": 0, "gpus_per_node": 0},
        "paths": {"root_dir": root_dir},
        "apps": apps,
    }
    errors = cl.validate_profile(profile)
    if errors:
        print("profile invalid: %s" % errors); return 1

    # 7. confirm
    print("\n--- profile summary ---")
    print(json.dumps(profile, indent=2))
    if not confirm("Write profile?"):
        print("aborted"); return 1

    write_profile(profile, ppath)
    print("wrote %s" % ppath)
    print("\nNext: verify with:\n"
          "  python scripts/cluster.py --profile %s doctor" % args.profile)
    return 0


def main():
    ap = argparse.ArgumentParser(prog="setup.py", description="cluster-connect first-time setup")
    ap.add_argument("--profile", default="default")
    ap.add_argument("--reprobe", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--deploy-key", action="store_true")
    args = ap.parse_args()
    if args.deploy_key:
        sys.exit(run_deploy_key(args) or 0)
    sys.exit(run_setup(args) or 0)


if __name__ == "__main__":
    main()
