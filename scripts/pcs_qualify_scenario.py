"""One fixed WAN scenario; private preflight facts never enter exported evidence."""
import ipaddress
import json
import os
from pathlib import Path
import time

from pcs_qualify_observe import command, checkpoint, assess, sanitize_uplink, UPLINK
from pcs_qualify_state import HarnessError, boot_id, read_json
from pcs_qualify_wan import ethernet_identity, direct_lan_control, control_query
from pcs_qualify_fault import arm_wan, table
from pcs_qualify_wan import owned_handle
from pcs_qualify_safety import restore, lease_valid
from pcs_qualify_lan import Receiver
from pcs_qualify_rf import RFBlocked, require_safe


class EvidenceUnavailable(HarnessError):
    pass


class UnsafePath(HarnessError):
    pass


def one(argv):
    rows = json.loads(command(argv))
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise HarnessError('wan_snapshot_ambiguous')
    return rows[0]


def ipv4_address(device):
    row = one(['/usr/sbin/ip', '-j', '-4', 'address', 'show', 'dev', device])
    addresses = row.get('addr_info')
    if (not isinstance(addresses, list) or len(addresses) != 1 or
            addresses[0].get('family') != 'inet' or addresses[0].get('scope') != 'global'):
        raise HarnessError('wan_addresses_ambiguous')
    address = addresses[0]
    return ipaddress.IPv4Interface(str(address['local']) + '/' + str(address['prefixlen']))


def snapshot():
    """Read-only. Never invokes NetworkManager.observe or adjusts routes/rp_filter."""
    connection = os.environ.get('SSH_CONNECTION', '')
    query = control_query(connection)
    lan, wan = ipv4_address('eth0'), ipv4_address('eth1')
    if str(lan.ip) != query[-1]:
        raise HarnessError('ssh_server_not_lan_address')
    direct_lan_control(connection, json.loads(command(query)),
                       json.loads(command(['/usr/sbin/ip', '-j', '-4', 'rule', 'show'])), str(lan.network))
    link = one(['/usr/sbin/ip', '-j', 'link', 'show', 'dev', 'eth1'])
    permanent = command(['/usr/sbin/ethtool', '-P', 'eth1']).strip().split()
    if len(permanent) != 3 or permanent[:2] != ['Permanent', 'address:']:
        raise HarnessError('wan_permanent_identity_unverified')
    target = ethernet_identity(link, permanent[2])
    config = read_json(Path('/etc/pcs/uplinks.json'), 65536)
    if not isinstance(config, dict) or config.get('version') != 1 or config.get('mode') != 'auto':
        raise HarnessError('automatic_uplink_policy_required')
    for name, default, low, high in [('poll_seconds', 10, 1, 10), ('failure_seconds', 30, 1, 30),
                                    ('recovery_seconds', 30, 1, 30), ('probe_timeout', 2, 1, 2)]:
        value = config.get(name, default)
        if type(value) is not int or not low <= value <= high:
            raise HarnessError('policy_timing_outside_scenario_bounds')
    raw = read_json(UPLINK, 262144)
    clean = sanitize_uplink(raw, time.time(), boot_id())
    if clean['collector_error'] or clean['mode'] != 'auto':
        raise HarnessError('uplink_evidence_unavailable')
    configured = config.get('uplinks')
    if not isinstance(configured, list) or not 2 <= len(configured) <= 3:
        raise HarnessError('wan_topology_unsupported')
    ethernet = [r for r in raw['uplinks'] if r.get('interface') == 'eth1' and r.get('type') == 'ethernet']
    wifi = [r for r in raw['uplinks'] if r.get('interface') == 'wlan0' and r.get('type') == 'wifi']
    if len(ethernet) != 1 or len(wifi) != 1:
        raise HarnessError('wan_topology_unsupported')
    for row in ethernet + wifi:
        matches = [u for u in configured if isinstance(u, dict) and u.get('id') == row.get('id')]
        if (len(matches) != 1 or matches[0].get('type') != row['type'] or
                matches[0].get('interface', '') not in ('', row['interface']) or
                (matches[0].get('profile') and matches[0]['profile'] != row.get('profile', '')) or
                type(matches[0].get('priority')) is not int or
                row.get('priority') != matches[0]['priority']):
            raise HarnessError('configured_wan_identity_changed')
        if row['type'] == 'ethernet' and matches[0].get('mac', '').lower() not in ('', target.permanent_mac):
            raise HarnessError('configured_wan_identity_changed')
        if not matches[0].get('interface') and not matches[0].get('mac'):
            raise HarnessError('configured_wan_identity_changed')
        if row['type'] == 'wifi':
            if matches[0].get('interface') != 'wlan0':
                raise HarnessError('configured_wan_identity_changed')
            if matches[0].get('mac'):
                wifi_link = one(['/usr/sbin/ip', '-j', 'link', 'show', 'dev', 'wlan0'])
                if matches[0]['mac'].lower() != wifi_link.get('address'):
                    raise HarnessError('configured_wan_identity_changed')
    slots = [raw['uplinks'].index(r) for r in ethernet + wifi]
    if ethernet[0]['priority'] >= wifi[0]['priority']:
        raise HarnessError('ethernet_must_be_preferred')
    # Production permits interface-bound WANs without an activation profile.
    # Pin their observed profile privately for this run; never export its UUID.
    profiles = tuple(row.get('profile', '') for row in ethernet + wifi)
    if any(not isinstance(profile, str) for profile in profiles):
        raise HarnessError('configured_wan_identity_changed')
    fixed = (target, str(lan), str(wan), config, slots, boot_id(), profiles)
    return {'fixed': fixed, 'target': target, 'lan': str(lan.network), 'wan': str(wan.network),
            'server': str(lan.ip), 'client': query[5], 'slots': slots, 'uplink': clean,
            'failure_seconds': config.get('failure_seconds', 30),
            'recovery_seconds': config.get('recovery_seconds', 30),
            'poll_seconds': config.get('poll_seconds', 10)}


def selected(clean, slots, fallback=False):
    target, wifi = (clean['uplinks'][slot] for slot in slots)
    wanted = wifi if fallback else target
    return (wanted['internet'] is True and wanted['active'] and wanted['selected'] and
            wanted['link'] and wanted['address'] and clean['internet'])


def cellular(clean):
    return [(r['owned'], r['suppressed'], r['active'], r['link'], r['address'])
            for r in clean['uplinks'] if r['type'] == 'cellular']


def checked_snapshot(session):
    require_safe(session, 'checkpoint')
    try:
        return snapshot()
    except HarnessError as exc:
        if str(exc) in ('wan_identity_unverified', 'wan_permanent_identity_unverified',
                        'configured_wan_identity_changed', 'wan_addresses_ambiguous',
                        'direct_ipv4_lan_ssh_required', 'independent_direct_lan_control_required',
                        'ssh_server_not_lan_address', 'policy_routing_unsupported'):
            raise UnsafePath('wan_control_or_identity_unverified') from None
        raise EvidenceUnavailable('wan_observation_unavailable') from None
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise EvidenceUnavailable('wan_observation_unavailable') from None


def run(session, duration):
    if type(duration) is not int or not 60 <= duration <= 120:
        return 'BLOCKED', 'wan_duration_requires_60_to_120_seconds'
    try:
        require_safe(session)
        baseline = snapshot()
        evidence = checkpoint()
        rows = baseline['uplink']['uplinks']
        if (assess(evidence) != 'PASS' or not selected(baseline['uplink'], baseline['slots']) or
                rows[baseline['slots'][1]]['internet'] is not True):
            return 'BLOCKED', 'healthy_ethernet_and_wifi_required'
        receiver = Receiver(session.id, baseline['server'], baseline['client'])
    except RFBlocked as exc:
        return 'BLOCKED', str(exc)
    except HarnessError as exc:
        codes = {'ssh_server_not_lan_address',
                 'independent_direct_lan_control_required', 'direct_ipv4_lan_ssh_required',
                 'policy_routing_unsupported', 'policy_timing_outside_scenario_bounds',
                 'wan_permanent_identity_unverified', 'configured_wan_identity_changed'}
        return 'BLOCKED', str(exc) if str(exc) in codes else 'wan_preflight_failed'
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return 'BLOCKED', 'wan_preflight_failed'
    session.event('wan_waiting_for_witness', {'port': 39841, 'timeout_seconds': 45})
    start_boot, started = boot_id(), time.monotonic()
    fault_start = removed = recovered = None
    failed_over = detected = False
    power_warning = False
    next_sample = started
    receipts_written = 0
    result, reason = 'HARNESS ERROR', 'wan_execution_error'
    try:
        while True:
            receiver.poll()
            now = time.monotonic()
            for row in receiver.receipts.samples[receipts_written:]:
                session.event('lan_witness', row)
                receipts_written += 1
            if boot_id() != start_boot:
                return 'ABORTED', 'boot_changed'
            if fault_start is None:
                if now - started > 45:
                    return 'BLOCKED', 'verified_lan_witness_missing'
                if not receiver.receipts.ready(now):
                    continue
                current = checked_snapshot(session)
                if current['fixed'] != baseline['fixed'] or not selected(current['uplink'], baseline['slots']):
                    return 'BLOCKED', 'wan_preflight_changed'
                def verify():
                    latest = checked_snapshot(session)
                    receiver.poll()
                    if (latest['fixed'] != baseline['fixed'] or
                            not selected(latest['uplink'], baseline['slots']) or
                            not receiver.receipts.ready(time.monotonic())):
                        raise HarnessError('wan_preflight_changed')
                fault_start = time.monotonic()
                arm_wan(session.id, baseline['target'], duration, baseline['lan'], baseline['wan'], verify=verify)
                session.event('wan_fault_started', {'family': 4, 'kernel_timeout_seconds': duration})
                now = time.monotonic()  # The final preflight may have received newer witness receipts.
            if receiver.receipts.invalid:
                return 'INCONCLUSIVE', 'witness_protocol_invalid'
            if receiver.receipts.samples and not receiver.receipts.samples[-1]['ok']:
                return 'FAIL', 'lan_http_sample_failed'
            if not receiver.receipts.ready(now):
                return 'INCONCLUSIVE', 'witness_contact_lost'
            if removed is None and now - fault_start < duration and not lease_valid(session.id):
                return 'ABORTED', 'fault_lease_lost'
            if now >= next_sample:
                if removed is None and now - fault_start < duration:
                    owned_handle(table(), session.id)
                current = checked_snapshot(session)
                if current['fixed'] != baseline['fixed']:
                    return 'ABORTED', 'wan_identity_or_policy_changed'
                clean = current['uplink']
                evidence = checkpoint()
                session.event('checkpoint', evidence)
                outcome = assess(evidence)
                if outcome in ('FAIL', 'INCONCLUSIVE'):
                    return outcome, 'required_observation_unhealthy'
                power = evidence.get('power', {})
                power_warning |= power.get('status') == 'warn' or any(
                    r.get('status') == 'warn' for r in power.get('monitors', {}).values())
                baseline_target = baseline['uplink']['uplinks'][baseline['slots'][0]]
                current_target = clean['uplinks'][baseline['slots'][0]]
                if baseline_target.get('internet6') is True and current_target.get('internet6') is not True:
                    return 'FAIL', 'ipv6_regressed_during_ipv4_fault'
                if cellular(clean) != cellular(baseline['uplink']):
                    return 'FAIL', 'unexpected_cellular_state_change'
                if clean['uplinks'][baseline['slots'][1]]['internet'] is not True:
                    return 'FAIL', 'standby_wifi_unhealthy'
                if removed is None and clean['uplinks'][baseline['slots'][0]]['internet'] is False:
                    if not detected:
                        session.event('wan_failure_observed', {'elapsed_seconds': round(now - fault_start, 3)})
                    detected = True
                if removed is None and selected(clean, baseline['slots'], fallback=True):
                    if now - fault_start < baseline['failure_seconds'] - baseline['poll_seconds'] - 5:
                        return 'FAIL', 'fallback_hysteresis_too_short'
                    if not failed_over:
                        session.event('wan_fallback_observed', {'elapsed_seconds': round(now - fault_start, 3)})
                    failed_over = True
                if removed is not None and selected(clean, baseline['slots']):
                    if now - (fault_start + duration) < baseline['recovery_seconds'] - baseline['poll_seconds'] - 5:
                        return 'FAIL', 'recovery_hysteresis_too_short'
                    if recovered is None:
                        recovered = time.monotonic()
                        receiver.receipts.finish_after = recovered
                        session.event('wan_recovery_observed', {'elapsed_seconds': round(recovered - removed, 3)})
                next_sample = time.monotonic() + 5
            if removed is None and now - fault_start >= duration:
                restore(expected_session=session.id)
                removed = time.monotonic()
                session.event('wan_fault_removed', {'verified': True})
            if removed is not None and now - removed > 90:
                return 'FAIL', 'preferred_wan_recovery_deadline'
            if recovered is not None and receiver.receipts.coverage(fault_start, recovered) != 'INCONCLUSIVE':
                result = receiver.receipts.coverage(fault_start, recovered)
                if result == 'PASS' and power_warning:
                    result = 'PASS WITH OBSERVATION'
                transitioned = failed_over and detected
                reason = 'sampled_lan_and_ipv4_transition' if transitioned else 'failure_or_fallback_not_observed'
                return (result if transitioned else 'FAIL'), reason
    except KeyboardInterrupt:
        return 'ABORTED', 'interrupted'
    except RFBlocked as exc:
        return 'ABORTED', str(exc)
    except UnsafePath:
        return 'ABORTED', 'wan_control_or_identity_unverified'
    except EvidenceUnavailable:
        return 'INCONCLUSIVE', 'wan_observation_unavailable'
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return result, reason
    finally:
        receiver.close()
        restore(expected_session=session.id)
