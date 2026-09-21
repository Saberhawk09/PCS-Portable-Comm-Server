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


def pi_password_hash(root: Path) -> str | None:
    shadow = root / "etc/shadow"
    if not shadow.exists() and not shadow.is_symlink():
        return None
    lines = source_file(root, "etc/shadow").read_text().splitlines()
    matches = [line.split(":", 2)[1] for line in lines if line.startswith("pi:")]
    if len(matches) != 1 or not matches[0] or ":" in matches[0] or len(matches[0]) > 512:
        raise ValueError("saved shadow file does not contain one valid pi password hash")
    return matches[0]


def restore_pi_password(root: Path, destination: Path) -> bool:
    saved = pi_password_hash(root)
    if saved is None:
        return False
    target = destination / "etc/shadow"
    if target.is_symlink() or not target.is_file():
        raise ValueError("live /etc/shadow is unavailable or unsafe")
    lines = target.read_text().splitlines()
    indexes = [index for index, line in enumerate(lines) if line.startswith("pi:")]
    if len(indexes) != 1:
        raise ValueError("live shadow file does not contain exactly one pi account")
    fields = lines[indexes[0]].split(":")
    fields[1] = saved
    lines[indexes[0]] = ":".join(fields)
    temporary = target.with_name(".shadow.pcs-private-restore")
    if temporary.exists() or temporary.is_symlink():
        raise ValueError("stale shadow restore temporary file")
    info = target.stat()
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, stat.S_IMODE(info.st_mode))
    with os.fdopen(fd, "w") as stream:
        stream.write("\n".join(lines) + "\n")
    os.chown(temporary, info.st_uid, info.st_gid)
    os.replace(temporary, target)
    return True


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
    elif component == "private":
        def optional(relative: str, mode: int = 0o600, group: str = "root") -> None:
            if (root / relative).exists() or (root / relative).is_symlink():
                plan.append((relative, mode, group))

        def optional_tree(relative: str, mode: int = 0o600, group: str = "root") -> None:
            directory = root / relative
            if directory.is_symlink():
                raise ValueError("backup paths must not contain symlinks")
            if not directory.exists():
                return
            for path in sorted(directory.rglob("*")):
                if path.is_dir():
                    if path.is_symlink():
                        raise ValueError("backup paths must not contain symlinks")
                    continue
                item = path.relative_to(root).as_posix()
                optional(item, mode, group)

        for path in sorted((root / "etc/ssh").glob("ssh_host_*")) if (root / "etc/ssh").is_dir() else ():
            optional(path.relative_to(root).as_posix(), 0o644 if path.name.endswith(".pub") else 0o600)
        optional_tree("home/pi/.ssh", 0o600, "pi")
        optional_tree("home/pi/Projects/PCS-Portable-Comm-Server/private-config", 0o600, "pi")
        optional_tree("etc/pcs-control-panel")
        optional("etc/pcs-backup/config.json")
        optional("etc/pcs/meshtastic.env")
        optional("etc/pcs/meshtastic-mqtt.env")
        optional("etc/pcs/starlink.json")
        optional_tree("etc/pcs/pistar-shutdown")
        optional_tree("etc/wireguard")
        optional("etc/direwolf.conf", 0o640, "direwolf")
        optional_tree("var/lib/samba/private")
        optional_tree("var/lib/bluetooth")
        optional("var/lib/graywolf/graywolf.db")
        optional("var/lib/alsa/asound.state")
    elif component == "exact":
        required = (
            "etc/pcs-control-panel/admin.json",
            "etc/pcs-backup/config.json",
            "etc/pcs/meshtastic.env",
            "etc/pcs/meshtastic-mqtt.env",
            "etc/pcs/pistar-shutdown/id_ed25519",
            "etc/pcs/pistar-shutdown/known_hosts",
            "etc/direwolf.conf",
            "etc/shadow",
        )
        plan = [(relative, 0o600, "root") for relative in required]
        ssh_keys = sorted((root / "etc/ssh").glob("ssh_host_*_key")) if (root / "etc/ssh").is_dir() else []
        samba_files = sorted(path for path in (root / "var/lib/samba/private").rglob("*") if path.is_file()) \
            if (root / "var/lib/samba/private").is_dir() else []
        wifi_files = []
        network = root / "etc/NetworkManager/system-connections"
        if network.is_dir():
            for path in sorted(network.glob("*")):
                config = configparser.ConfigParser(interpolation=None)
                config.read(path)
                if config.get("connection", "type", fallback="") in {"wifi", "802-11-wireless"}:
                    wifi_files.append(path)
        if not ssh_keys or not samba_files or not wifi_files:
            raise ValueError("exact recovery requires SSH host keys, Samba credentials, and a Wi-Fi profile")
        plan.extend((path.relative_to(root).as_posix(), 0o600, "root") for path in ssh_keys + samba_files + wifi_files)
        # Reuse the strict all-or-nothing validation for WireGuard and API identity.
        plan.extend(plan_restore(root, "network"))
        api = plan_restore(root, "api")
        if not api or not any(path == "etc/pcs/wireguard/private.key" for path, _, _ in plan):
            raise ValueError("exact recovery requires complete WireGuard and API identities")
        plan.extend(api)
    else:
        raise ValueError("unknown recovery component")
    for relative, _, _ in plan:
        source_file(root, relative)
    if component in {"private", "exact"}:
        pi_password_hash(root)
    return plan


def restore(root: Path, component: str, destination: Path = Path("/"), replace: bool = False) -> int:
    import grp
    plan = plan_restore(root, component)
    if component == "exact":
        raise ValueError("exact is a validation-only recovery component")
    # Preflight every destination before copying any file. Existing identical
    # state is safe to retry; a different identity requires explicit recovery.
    for relative, _, _ in plan:
        target = destination / relative
        for parent in (target, *target.parents):
            if parent.is_symlink():
                raise ValueError("live recovery paths must not contain symlinks")
            if parent == destination:
                break
        if component != "private" and target.exists() and not replace \
                and (not target.is_file() or target.read_bytes() != source_file(root, relative).read_bytes()):
            raise ValueError(f"refusing to overwrite different live file: {relative}")
        if component == "private" and target.exists() and not target.is_file():
            raise ValueError(f"refusing to replace non-file recovery target: {relative}")
    groups = {group: grp.getgrnam(group).gr_gid for _, _, group in plan}
    for relative, mode, group in plan:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
        gid = groups[group]
        if component == "private" or (replace and target.exists()):
            temporary = target.with_name(target.name + ".pcs-private-restore")
            if temporary.exists() or temporary.is_symlink():
                raise ValueError(f"stale private restore temporary file: {relative}")
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
            with os.fdopen(fd, "wb") as stream:
                stream.write(source_file(root, relative).read_bytes())
            os.chown(temporary, 0 if group != "pi" else __import__("pwd").getpwnam("pi").pw_uid, gid)
            os.chmod(temporary, mode)
            os.replace(temporary, target)
        elif not target.exists():
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
            with os.fdopen(fd, "wb") as stream:
                stream.write(source_file(root, relative).read_bytes())
        os.chown(target, 0 if group != "pi" else __import__("pwd").getpwnam("pi").pw_uid, gid)
        os.chmod(target, mode)
    if component == "api" and plan:
        os.chown(destination / "etc/pcs-stats-api/tls", 0, grp.getgrnam("pcs-api").gr_gid)
        os.chmod(destination / "etc/pcs-stats-api/tls", 0o750)
        os.chmod(destination / "etc/pcs-stats-api", 0o755)
    if component == "network" and (destination / "etc/pcs/wireguard-management.conf").exists():
        os.chmod(destination / "etc/pcs", 0o755)
        os.chmod(destination / "etc/pcs/wireguard", 0o700)
    if component == "private":
        import pwd
        pi = pwd.getpwnam("pi")
        for directory in (destination / "home/pi/.ssh", destination / "home/pi/Projects/PCS-Portable-Comm-Server/private-config"):
            if directory.exists():
                for child in (directory, *(path for path in directory.rglob("*") if path.is_dir())):
                    os.chown(child, pi.pw_uid, pi.pw_gid)
                    os.chmod(child, 0o700)
        return len(plan) + int(restore_pi_password(root, destination))
    return len(plan)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("component", choices=("network", "api", "private", "exact"))
    parser.add_argument("directory", type=Path)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--replace", action="store_true", help="Replace generated identity files after exact-bundle validation")
    args = parser.parse_args()
    try:
        root = args.directory
        if os.geteuid() != 0 or not root.is_absolute() or root.is_symlink():
            raise ValueError("use sudo and an absolute root-owned recovery directory")
        info = root.stat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
            raise ValueError("recovery directory must be root-owned mode 0700")
        if args.check and args.replace:
            raise ValueError("--replace cannot be combined with --check")
        count = len(plan_restore(root, args.component)) if args.check else restore(root, args.component, replace=args.replace)
        print(f"Recovery {args.component}: {count} files {'validated' if args.check else 'restored'}")
    except (OSError, ValueError, KeyError, configparser.Error) as exc:
        parser.exit(1, f"ERROR: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
