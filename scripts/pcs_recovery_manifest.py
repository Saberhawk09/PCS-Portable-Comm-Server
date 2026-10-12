#!/usr/bin/env python3
"""Versioned private backup inventory. No services or target configuration touched."""
import argparse
import configparser
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tarfile
import tempfile
import uuid

from pcs_reinstall_archive import members

MANIFEST = 'etc/pcs/recovery-manifest.json'
NM = 'etc/NetworkManager/system-connections/'
MODE = 'etc/pcs/network-mode'
CONFIGS = {
    'etc/pcs/uplinks.json': ('uplinks', ['network-manager', 'python3-dbus']),
    'etc/pcs/uplink-management.json': ('management', ['nftables']),
    'etc/pcs/bandwidth.yaml': ('bandwidth', ['iproute2', 'nftables', 'python3-yaml']),
    'etc/pcs/switch-monitor.json': ('snmp', ['snmp', 'acl']),
    'etc/pcs/switch-monitor.community': ('snmp-credential', []),
    'etc/pcs/power-monitor.json': ('power', ['python3-smbus']),
}
EXCLUSIONS = [
    'No automatic activation or complete appliance recovery is promised.',
    'Runtime counters, boot/session ownership, snapshots and timers are not replayed.',
    'External switch/AP/radio configuration and physical wiring require manual recovery.',
    'Service enablement, generated units/firewalls and GPIO/LCD software require installation.',
    'Calibration values are captured settings, not proof of physical measurement.',
    'Other captured private identities remain subject to the existing recovery helper.',
]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def json_bytes(value):
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + '\n').encode()


def parse_json(data):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate JSON key')
            result[key] = value
        return result
    def invalid(_):
        raise ValueError('non-finite JSON number')
    try:
        return json.loads(data, object_pairs_hook=unique, parse_constant=invalid)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError('invalid JSON encoding or syntax') from exc


def profile(data, path):
    """Only identity/topology fields; never return SSID, APN, passwords or keys."""
    cfg = configparser.ConfigParser(interpolation=None, strict=True)
    try:
        cfg.read_string(data.decode('utf-8'))
        if cfg.defaults():
            raise ValueError('keyfile defaults are unsupported')
        identity = str(uuid.UUID(cfg.get('connection', 'uuid')))
        kind = cfg.get('connection', 'type')
        interface = cfg.get('connection', 'interface-name', fallback='')
        if interface and not re.fullmatch(r'[a-zA-Z0-9_.-]{1,15}', interface):
            raise ValueError('invalid interface')
        return dict(path=path, uuid=identity, type=kind, interface=interface,
                    parent=cfg.get('vlan', 'parent', fallback=''),
                    vlan_id=cfg.getint('vlan', 'id', fallback=0),
                    ipv4=cfg.get('ipv4', 'method', fallback=''),
                    ipv6=cfg.get('ipv6', 'method', fallback=''))
    except (ValueError, configparser.Error, UnicodeError) as exc:
        raise ValueError('invalid NetworkManager identity metadata') from exc


def read_archive(path):
    selected = members(Path(path))
    records, payload = [], {}
    with tarfile.open(path, 'r:gz') as stream:
        for info, name in selected:
            # The old archive validator already enforces allowlist, sizes and types.
            if info.isfile():
                data = stream.extractfile(info).read()
                if len(data) != info.size:
                    raise ValueError('truncated backup member')
                payload[name] = data
                if name != MANIFEST:
                    records.append(dict(path=name, size=info.size, sha256=digest(data),
                                        mode=info.mode & 0o777, uid=info.uid, gid=info.gid))
    return sorted(records, key=lambda row: row['path']), payload


def inventory(records, payload):
    profiles, invalid = [], []
    for path, data in sorted(payload.items()):
        if path.startswith(NM):
            try:
                profiles.append(profile(data, path))
            except ValueError:
                # A malformed config can still be backed up, but never silently staged.
                invalid.append(path)
    mode = payload.get(MODE, b'').decode('ascii', errors='replace').strip()
    if mode not in ('legacy', 'vlan'):
        mode = 'unknown' if MODE not in payload else 'invalid'
    dependencies = {'python3', 'network-manager'}
    if mode == 'vlan':
        dependencies.update(('nftables', 'iproute2'))
    if any(p['type'] == 'gsm' for p in profiles):
        dependencies.add('modemmanager')
    configurations = []
    for path, (component, packages) in CONFIGS.items():
        if path in payload:
            dependencies.update(packages)
            configurations.append(dict(path=path, component=component,
                                       disposition='requires-plan-validation'))
    return dict(files=records, profiles=profiles, invalid_profiles=invalid,
                topology=dict(mode=mode, authority='captured-marker' if MODE in payload else 'unknown'),
                configurations=configurations, dependencies=sorted(dependencies),
                exclusions=EXCLUSIONS)


def inspect(path, expected_sha256=None):
    archive_hash = file_digest(path)
    if expected_sha256 is not None and archive_hash != expected_sha256:
        raise ValueError('archive SHA-256 mismatch')
    records, payload = read_archive(path)
    observed = inventory(records, payload)
    manifest = None
    if MANIFEST in payload:
        manifest = parse_json(payload[MANIFEST])
        if not isinstance(manifest, dict) or set(manifest) != {'schema', 'created_utc', 'source', 'inventory'}:
            raise ValueError('invalid recovery manifest schema')
        if type(manifest['schema']) is not int or manifest['schema'] != 1:
            raise ValueError('unsupported recovery manifest version')
        try:
            stamp = datetime.fromisoformat(manifest['created_utc'])
            if stamp.tzinfo is None:
                raise ValueError('missing timezone')
        except (TypeError, ValueError) as exc:
            raise ValueError('invalid manifest timestamp') from exc
        source = manifest['source']
        if (not isinstance(source, dict) or set(source) != {'commit', 'release', 'dirty', 'machine_id_sha256'}
                or not re.fullmatch('[0-9a-f]{40}', str(source['commit']))
                or not isinstance(source['release'], str)
                or not re.fullmatch(r'[A-Za-z0-9_.+-]{1,100}', source['release'])
                or type(source['dirty']) is not bool
                or source['machine_id_sha256'] is not None and not re.fullmatch('[0-9a-f]{64}', str(source['machine_id_sha256']))):
            raise ValueError('invalid manifest source identity')
        if manifest['inventory'] != observed:
            raise ValueError('manifest does not match archive contents')
    return dict(schema=1, archive_sha256=archive_hash,
                manifest_status='verified' if manifest else 'absent-legacy',
                manifest=manifest, inventory=observed), payload


def seal(source, output, commit, release, dirty=False, machine_id=None):
    """Add an inventory to a captured tar. Existing manifest is replaced, never trusted."""
    records, payload = read_archive(source)
    if not records:
        raise ValueError('no captured payload files')
    manifest = dict(schema=1, created_utc=datetime.now(timezone.utc).isoformat(),
                    source=dict(commit=commit, release=release, dirty=dirty,
                                machine_id_sha256=digest(machine_id) if machine_id else None),
                    inventory=inventory(records, payload))
    try:
        # Exclusive creation; never clobber a prior backup.
        with open(output, 'xb') as dest:
            os.chmod(output, 0o600)
            with tarfile.open(fileobj=dest, mode='w:gz') as stream:
                # Keep original directory/file metadata (including GNU tar PAX
                # ACL/xattr records); inactive staging deliberately does not replay it.
                for info, name in members(Path(source)):
                    if name != MANIFEST:
                        stream.addfile(info, io.BytesIO(payload[name]) if info.isfile() else None)
                data = json_bytes(manifest)
                info = tarfile.TarInfo(MANIFEST)
                info.mode, info.size = 0o600, len(data)
                stream.addfile(info, io.BytesIO(data))
            dest.flush()
            inspect(output)
    except FileExistsError:
        raise
    except Exception:
        Path(output).unlink(missing_ok=True)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    check = commands.add_parser('inspect')
    check.add_argument('archive', type=Path)
    check.add_argument('--sha256')
    seal_cmd = commands.add_parser('seal-stream', help='Internal export filter: gzip tar in/out; errors on stderr')
    seal_cmd.add_argument('--commit', required=True)
    seal_cmd.add_argument('--release', required=True)
    seal_cmd.add_argument('--dirty', action='store_true')
    seal_cmd.add_argument('--machine-id-file', type=Path)
    args = parser.parse_args()
    try:
        if args.action == 'inspect':
            result, _ = inspect(args.archive, args.sha256)
            print(json.dumps(result, indent=2))
        else:
            with tempfile.TemporaryDirectory(prefix='pcs-manifest-') as temp:
                root = Path(temp)
                source, output = root / 'input.tar.gz', root / 'output.tar.gz'
                with source.open('wb') as dest:
                    # Bound compressed input as well as expanded members.
                    total = 0
                    while chunk := sys.stdin.buffer.read(1024 * 1024):
                        total += len(chunk)
                        if total > 512 * 1024 * 1024:
                            raise ValueError('compressed backup exceeds limit')
                        dest.write(chunk)
                identity = args.machine_id_file.read_bytes() if args.machine_id_file else None
                seal(source, output, args.commit, args.release, args.dirty, identity)
                with output.open('rb') as src:
                    shutil.copyfileobj(src, sys.stdout.buffer)
    except (OSError, ValueError, tarfile.TarError):
        # Do not echo arbitrary config values or paths from exceptions.
        parser.exit(1, 'ERROR: recovery manifest processing failed; no valid export promised.\n')


if __name__ == '__main__':
    main()
