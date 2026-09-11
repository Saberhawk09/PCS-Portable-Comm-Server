#!/usr/bin/env python3
"""Restore selected identity/network files from an explicitly trusted backup.

Never decrypt archives, generate credentials, follow symlinks, overwrite
different live files, or restore radio/application state outside this scope.
"""
import argparse
import configparser
import os
from pathlib import Path
import re
import shlex
import stat


def source_file(root: Path, relative: str) -> Path:
    path = root / relative
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError("backup paths must not contain symlinks")
        if part == root:
            break
    if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"missing backup file: {relative}")
    return path


def plan_restore(root: Path, component: str) -> list[tuple[str, int, str]]:
    plan: list[tuple[str, int, str]] = []
    if component == "network":
        directory = root / "etc/NetworkManager/system-connections"
        if directory.is_symlink():
            raise ValueError("backup paths must not contain symlinks")
        for path in sorted(directory.glob("*")):
            relative = path.relative_to(root).as_posix()
            source_file(root, relative)
            config = configparser.ConfigParser(interpolation=None)
            config.read(path)
            if config.get("connection", "type", fallback="") in {"wifi", "802-11-wireless"}:
                plan.append((relative, 0o600, "root"))
        policy = "etc/pcs/wireguard-management.conf"
        if (root / policy).exists() or (root / policy).is_symlink():
            values = {}
            for line in source_file(root, policy).read_text().splitlines():
                if not line.strip() or line.lstrip().startswith("#"):
                    continue
                match = re.fullmatch(r"(PCS_WG_[A-Z_]+)=(.*)", line)
                if not match or any(char in match[2] for char in "`$;<>|&()"):
                    raise ValueError("unsafe saved WireGuard policy")
                value = shlex.split(match[2])
                if len(value) > 1 or match[1] in values:
                    raise ValueError("ambiguous saved WireGuard policy")
                values[match[1]] = value[0] if value else ""
            if not values.get("PCS_WG_ALLOWED_IPS") or not values.get("PCS_WG_ADMIN_SOURCES"):
                raise ValueError("saved policy is missing its management client list")
            plan += [(policy, 0o644, "root"), ("etc/pcs/wireguard/private.key", 0o600, "root")]
            if values.get("PCS_WG_USE_PRESHARED_KEY") == "yes":
                plan.append(("etc/pcs/wireguard/preshared.key", 0o600, "root"))
    elif component == "api":
        directory = root / "etc/pcs-stats-api"
        if directory.exists() or directory.is_symlink():
            plan = [("etc/pcs-stats-api/" + name, mode, group) for name, mode, group in (
                ("policy.conf", 0o644, "root"),
                ("tls/server.crt", 0o644, "pcs-api"),
                ("tls/server.key", 0o640, "pcs-api"),
                ("api-read-tokens.json", 0o640, "pcs-api"),
            )]
    else:
        raise ValueError("unknown recovery component")
    for relative, _, _ in plan:
        source_file(root, relative)
    return plan


def restore(root: Path, component: str, destination: Path = Path("/")) -> int:
    import grp
    plan = plan_restore(root, component)
    # Preflight every destination before copying any file. Existing identical
    # state is safe to retry; a different identity requires explicit recovery.
    for relative, _, _ in plan:
        target = destination / relative
        for parent in (target, *target.parents):
            if parent.is_symlink():
                raise ValueError("live recovery paths must not contain symlinks")
            if parent == destination:
                break
        if target.exists() and (not target.is_file() or target.read_bytes() != source_file(root, relative).read_bytes()):
            raise ValueError(f"refusing to overwrite different live file: {relative}")
    for relative, mode, group in plan:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
        gid = grp.getgrnam(group).gr_gid
        if not target.exists():
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
            with os.fdopen(fd, "wb") as stream:
                stream.write(source_file(root, relative).read_bytes())
        os.chown(target, 0, gid)
        os.chmod(target, mode)
    if component == "api" and plan:
        os.chown(destination / "etc/pcs-stats-api/tls", 0, grp.getgrnam("pcs-api").gr_gid)
        os.chmod(destination / "etc/pcs-stats-api/tls", 0o750)
        os.chmod(destination / "etc/pcs-stats-api", 0o755)
    if component == "network" and (destination / "etc/pcs/wireguard-management.conf").exists():
        os.chmod(destination / "etc/pcs", 0o755)
        os.chmod(destination / "etc/pcs/wireguard", 0o700)
    return len(plan)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("component", choices=("network", "api"))
    parser.add_argument("directory", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        root = args.directory
        if os.geteuid() != 0 or not root.is_absolute() or root.is_symlink():
            raise ValueError("use sudo and an absolute root-owned recovery directory")
        info = root.stat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
            raise ValueError("recovery directory must be root-owned mode 0700")
        count = len(plan_restore(root, args.component)) if args.check else restore(root, args.component)
        print(f"Recovery {args.component}: {count} files {'validated' if args.check else 'restored'}")
    except (OSError, ValueError, KeyError, configparser.Error) as exc:
        parser.exit(1, f"ERROR: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
