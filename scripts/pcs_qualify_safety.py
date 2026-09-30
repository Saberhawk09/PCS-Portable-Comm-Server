"""Qualification leases and independent, strictly owned artifact cleanup."""
import ipaddress
import json
import os
from pathlib import Path
import re
import time

from pcs_qualify_observe import command
from pcs_qualify_state import (HarnessError, RUNTIME, SESSIONS, atomic_json, boot_id,
                               finite, identifier, lock, private_dir, read_json, report, stamp)

UNIT = 'pcs-qualify-expiry'
CAMPAIGN = 'pcs-qualify-campaign.service'
MAX_LEASE = 360


def route(address):
    address = ipaddress.ip_address(address)
    rows = json.loads(command(['/usr/sbin/ip', '-j', '-6' if address.version == 6 else '-4',
                               'route', 'get', str(address)]))
    if not isinstance(rows, list) or len(rows) != 1:
        raise HarnessError('ambiguous_control_route')
    row = rows[0]
    dev = row.get('dev')
    if not isinstance(dev, str) or not dev or len(dev) > 15 or '/' in dev or dev.startswith('-'):
        raise HarnessError('unknown_control_route')
    if row.get('type', 'unicast') != 'unicast' or row.get('nexthops'):
        raise HarnessError('ambiguous_control_route')
    return dev


def control_path(environment=None):
    environment = os.environ if environment is None else environment
    result = {'transport': 'unknown', 'underlay': 'unknown', 'verified': False,
              'network_mutation_allowed': False}
    connection = environment.get('SSH_CONNECTION', '').split()
    if not connection:
        # A tty/SSH variable absence cannot prove a physical console (sudo/tmux).
        return result
    try:
        if len(connection) != 4:
            return result
        ipaddress.ip_address(connection[2])
        if any(not p.isdigit() or not 1 <= int(p) <= 65535 for p in (connection[1], connection[3])):
            return result
        device = route(connection[0])
        result['transport'] = 'ssh'
        if (Path('/sys/class/net') / device / 'ifindex').read_text().strip().isdigit() is False:
            return result
        if device in ('wg-pcs', 'wg-direct'):
            result['transport'] = 'wireguard'
            lines = command(['/usr/bin/wg', 'show', device, 'endpoints']).splitlines()
            if len(lines) != 1 or len(lines[0].split()) != 2:
                return result
            endpoint = lines[0].split()[1]
            host, port = endpoint.rsplit(':', 1)
            if not port.isdigit():
                return result
            device = route(host.strip('[]'))
            if device.startswith('wg'):
                return result
        result['underlay'] = 'lan' if device == 'eth0' else 'loopback' if device == 'lo' else 'other'
        result['verified'] = True
    except (OSError, ValueError, TypeError, HarnessError):
        pass
    return result


def preflight():
    if os.geteuid() != 0 or not Path('/run/systemd/system').is_dir():
        raise HarnessError('root_and_systemd_required')
    if command(['/usr/bin/systemctl', 'is-enabled', 'pcs-qualify-cleanup.service']).strip() != 'enabled':
        raise HarnessError('boot_cleanup_not_enabled')
    return control_path()


def arm(session, seconds, runtime=RUNTIME):
    identifier(session)
    if type(seconds) is not int or not 5 <= seconds <= MAX_LEASE:
        raise HarnessError('invalid_lease_duration')
    private_dir(runtime)
    with lock(runtime / 'mutation.lock', blocking=True):
        if any((runtime / name).exists() for name in ('active.json', 'marker', 'wan.json')):
            raise HarnessError('lease_already_present')
        # PREPARED persisted before registration, and no effect before timer confirmation.
        atomic_json(runtime / 'active.json', {'version': 1, 'session': session,
                    'boot_id': boot_id(), 'deadline': time.monotonic() + seconds,
                    'state': 'prepared', 'effect': 'marker',
                    'invocation': os.environ.get('INVOCATION_ID')})
        try:
            command(['/usr/bin/systemctl', 'stop', UNIT + '.timer'])
        except HarnessError:
            pass  # A never-created transient timer is absent, verified below by creation.
        try:
            command(['/usr/bin/systemd-run', '--quiet', '--collect', '--unit=' + UNIT,
                     '--on-active=' + str(seconds) + 's', '--timer-property=AccuracySec=100ms',
                     '--timer-property=RandomizedDelaySec=0', '--property=Type=oneshot',
                     '--property=TimeoutStartSec=20s', '--property=MemoryMax=64M',
                     '--property=TasksMax=16', '--property=CPUQuota=20%',
                     '/usr/local/sbin/pcs-qualify', 'expire', session])
            if command(['/usr/bin/systemctl', 'is-active', UNIT + '.timer']).strip() != 'active':
                raise HarnessError('expiry_not_armed')
            atomic_json(runtime / 'marker', {'version': 1, 'session': session})
            value = read_json(runtime / 'active.json')
            value['state'] = 'active'
            atomic_json(runtime / 'active.json', value)
        except BaseException:
            (runtime / 'marker').unlink(missing_ok=True)
            (runtime / 'active.json').unlink(missing_ok=True)
            raise


def lease_valid(session, runtime=RUNTIME):
    try:
        value = read_json(runtime / 'active.json', 4096)
        return (value.get('version') == 1 and value.get('session') == session and
                value.get('boot_id') == boot_id() and value.get('state') == 'active' and
                value.get('effect') in ('marker', 'nft-v4') and finite(value.get('deadline')) and
                time.monotonic() < value['deadline'] <= time.monotonic() + MAX_LEASE)
    except (OSError, AttributeError, HarnessError):
        return False


def restore(runtime=RUNTIME, expected_session=None):
    """Remove fixed ephemeral files and the exactly owned WAN table when recorded.

    No manifest-provided path, command, PID, interface or service is acted upon.
    Independent from the campaign lock. Unlink never follows a symlink.
    """
    private_dir(runtime)
    def remove():
        with lock(runtime / 'mutation.lock'):
            if expected_session is not None:
                try:
                    current = read_json(runtime / 'active.json', 4096)
                except FileNotFoundError:
                    return
                if not isinstance(current, dict) or current.get('session') != expected_session:
                    return  # A delayed old expiry must not revoke a newer lease.
            from pcs_qualify_fault import cleanup_record
            cleanup_record(runtime)
            for name in ('marker', 'active.json'):
                (runtime / name).unlink(missing_ok=True)
    try:
        remove()
    except HarnessError as exc:
        if str(exc) != 'campaign_busy':
            raise
        # A stopped runner cannot veto independent expiry. Only its exact systemd
        # invocation may be killed; never trust a manifest PID or executable path.
        value = read_json(runtime / 'active.json', 4096)
        if not isinstance(value, dict):
            raise HarnessError('invalid_lease_record')
        if expected_session is not None and value.get('session') != expected_session:
            return
        invocation = value.get('invocation')
        if not isinstance(invocation, str) or not re.fullmatch('[0-9a-f]{32}', invocation):
            raise HarnessError('cleanup_lock_owner_unknown') from None
        actual = command(['/usr/bin/systemctl', 'show', '--value', '--property=InvocationID', CAMPAIGN]).strip()
        fragment = command(['/usr/bin/systemctl', 'show', '--value', '--property=FragmentPath', CAMPAIGN]).strip()
        if actual != invocation or fragment != '/run/systemd/transient/' + CAMPAIGN:
            raise HarnessError('cleanup_lock_owner_changed') from None
        command(['/usr/bin/systemctl', 'kill', '--signal=SIGKILL', '--kill-whom=all', CAMPAIGN])
        deadline = time.monotonic() + 3
        while True:
            try:
                remove()
                break
            except HarnessError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)


def boot_cleanup(root=SESSIONS, runtime=RUNTIME):
    private_dir(runtime)
    # Prevent a manually started boot cleanup from aborting a live campaign.
    with lock(runtime / 'campaign.lock'):
        restore(runtime)
        if runtime == RUNTIME:
            from pcs_qualify_fault import cleanup_orphan
            cleanup_orphan(runtime)
        if not root.exists():
            return
        private_dir(root)
        entries = list(root.iterdir())
        if len(entries) > 32:
            raise HarnessError('session_limit')
        for path in entries:
            identifier(path.name)
            if path.is_symlink() or not path.is_dir():
                raise HarnessError('unsafe_session_storage')
            corrupt = False
            try:
                value = read_json(path / 'session.json')
                if (not isinstance(value, dict) or value.get('session') != path.name or
                        value.get('scenario') not in ('FQ-001', 'FQ-002', 'FQ-301-v4', 'FQ-302', 'unknown')):
                    raise HarnessError('invalid_session_record')
            except (OSError, HarnessError):
                # Replace only the broken manifest with a fixed recovery record;
                # retaining arbitrary corrupt text would violate the export allowlist.
                if (path / 'session.json').is_symlink():
                    (path / 'session.json').unlink()
                value = {'version': 1, 'session': path.name, 'scenario': 'unknown',
                         'start': stamp(), 'complete': False}
                corrupt = True
            if value.get('complete') is not True:
                result = 'HARNESS ERROR' if corrupt else 'ABORTED'
                reason = 'corrupt_session_record' if corrupt else 'boot_or_interrupted_cleanup'
                value.update(result=result, reason=reason, end=stamp(), complete=False)
                atomic_json(path / 'session.json', value)
                atomic_json(path / 'recovery.jsonl', {'event': 'recovery', 'session': path.name,
                            'result': result, 'reason': reason, **stamp()})
                report(path, pending=True)
                value['complete'] = True
                atomic_json(path / 'session.json', value)
