#!/usr/bin/env python3
"""Validate and extract a PCS private-state tarball without trusting tar paths."""
import argparse
import os
from pathlib import Path, PurePosixPath
import stat
import tarfile


ALLOWED_ROOTS = (
    "home/pi/Projects/PCS-Portable-Comm-Server/config/pcs-install.conf",
    "home/pi/Projects/PCS-Portable-Comm-Server/private-config",
    "home/pi/.ssh",
    "etc/pcs",
    "etc/pcs-control-panel",
    "etc/pcs-backup/config.json",
    "etc/pcs-stats-api",
    "etc/direwolf.conf",
    "etc/wireguard",
    "etc/ssh",
    "etc/shadow",
    "etc/samba",
    "etc/NetworkManager/system-connections",
    "var/lib/pcs-aprs-agent",
    "var/lib/graywolf/graywolf.db",
    "var/lib/alsa/asound.state",
    "var/lib/samba/private",
    "var/lib/bluetooth",
)


def normalized(name: str) -> str:
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or any(part in ("", ".", "..") for part in path.parts):
        raise ValueError(f"unsafe archive path: {name}")
    value = path.as_posix().rstrip("/")
    if not any(value == root or value.startswith(root + "/") or root.startswith(value + "/") for root in ALLOWED_ROOTS):
        raise ValueError(f"archive path is outside PCS recovery scope: {name}")
    return value


def members(archive: Path) -> list[tuple[tarfile.TarInfo, str]]:
    result = []
    seen = set()
    total_size = 0
    with tarfile.open(archive, "r:gz") as stream:
        for member in stream.getmembers():
            name = normalized(member.name)
            if not (member.isdir() or member.isfile()):
                raise ValueError(f"archive links/devices are forbidden: {member.name}")
            if name in seen:
                raise ValueError(f"duplicate archive path: {member.name}")
            seen.add(name)
            payload_path = any(name == root or name.startswith(root + "/") for root in ALLOWED_ROOTS)
            if member.isfile() and not payload_path:
                raise ValueError(f"archive ancestor must be a directory: {member.name}")
            if member.size > 256 * 1024 * 1024:
                raise ValueError(f"archive member is unreasonably large: {member.name}")
            total_size += member.size
            if total_size > 512 * 1024 * 1024:
                raise ValueError("archive expands beyond the PCS recovery size limit")
            result.append((member, name))
    if not result:
        raise ValueError("archive contains no PCS recovery state")
    return result


def extract(archive: Path, destination: Path) -> int:
    selected = members(archive)
    info = destination.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
        raise ValueError("destination must be a root-owned mode 0700 directory")
    with tarfile.open(archive, "r:gz") as stream:
        for member, name in selected:
            target = destination / name
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True, mode=0o700)
                os.chmod(target, 0o700)
                continue
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if target.exists() or target.is_symlink():
                raise ValueError(f"refusing to overwrite extraction target: {name}")
            source = stream.extractfile(member)
            if source is None:
                raise ValueError(f"could not read archive member: {name}")
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as output:
                while chunk := source.read(1024 * 1024):
                    output.write(chunk)
    return sum(member.isfile() for member, _ in selected)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        if os.geteuid() != 0 or not args.archive.is_absolute() or not args.destination.is_absolute():
            raise ValueError("run with sudo and absolute paths")
        count = len(members(args.archive)) if args.check else extract(args.archive, args.destination)
        print(f"Private recovery archive: {count} entries {'validated' if args.check else 'extracted'}")
    except (OSError, ValueError, tarfile.TarError) as exc:
        parser.exit(1, f"ERROR: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
