"""Fixed RF-silent automatic cellular ownership qualification, IPv4 only."""
import json
import os
from pathlib import Path
import re
import time

from pcs_qualify_state import HarnessError, RUNTIME, boot_id, read_json
from pcs_qualify_observe import command, checkpoint, assess
from pcs_qualify_scenario import snapshot as wan_snapshot, one, ipv4_address, selected
from pcs_qualify_wan import Identity, CELLULAR_OWNER, owned_handle
from pcs_qualify_fault import arm_cellular, table
from pcs_qualify_safety import restore, lease_valid
from pcs_qualify_lan import Receiver
from pcs_qualify_rf import require_safe, RFBlocked


class Ambiguous(HarnessError):
    pass


def ownership(facts, expected=None, recovering=False):
    """Exact production ledger/NM activation match; never infer from routing."""
    if (facts['boot'] != boot_id() or not isinstance(facts['owned'], dict) or
            not isinstance(facts['suppressed'], list) or facts['cellular_id'] in facts['suppressed'] or
            facts['modem_state'] not in (8,9,10,11) or facts['registration'] not in (1,5)):
        raise Ambiguous('cellular_ownership_unavailable')
    active = facts['active']
    owned = facts['owned'].get(facts['cellular_id'])
    if set(facts['owned']) - {facts['cellular_id']}:
        raise Ambiguous('unexpected_manager_ownership')
    if not active:
        if owned or facts['bearer_connected']:
            raise Ambiguous('cellular_ownership_unavailable')
        return 'inactive', None
    if len(active) != 1:
        raise Ambiguous('cellular_ownership_unavailable')
    row = active[0]
    token = dict(session=row['session'], profile=row['profile'])
    if row['profile'] != facts['cellular_profile'] or (expected is not None and token != expected):
        raise Ambiguous('operator_or_replaced_cellular_session')
    if recovering and owned is None and row['state'] == 3 and expected == token:
        return 'releasing', token
    if owned != token:
        raise Ambiguous('cellular_ownership_unproven')
    if row['state'] not in (1, 2, 3):
        raise Ambiguous('cellular_state_ambiguous')
    return ('owned_active' if row['state'] == 2 and facts['bearer_connected'] else 'owned_pending'), token


def snapshot():
    base = wan_snapshot()
    facts = json.loads(command(['/usr/bin/python3', '-I', '-c',
        "import sys,json;sys.path.insert(0,'/usr/local/lib/pcs');from pcs_qualify_cellular_read import collect;print(json.dumps(collect()))"], timeout=8))
    wifi = one(['/usr/sbin/ip', '-j', 'link', 'show', 'dev', 'wlan0'])
    if (wifi.get('ifname') != 'wlan0' or type(wifi.get('ifindex')) is not int or
            not {'UP', 'LOWER_UP'} <= set(wifi.get('flags', [])) or
            wifi.get('operstate') != 'UP' or any(k in wifi for k in ('master','link','link_index','linkinfo')) or
            not re.fullmatch(r'(?:[0-9a-f]{2}:){5}[0-9a-f]{2}', wifi.get('address',''))):
        raise HarnessError('wifi_identity_unverified')
    wifi_target = Identity('wlan0', wifi['ifindex'], wifi['address'])
    wifi_address = ipv4_address('wlan0')
    config = read_json(Path('/etc/pcs/uplinks.json'), 65536)
    rows = base['uplink']['uplinks']
    cells = [i for i,r in enumerate(rows) if r['type'] == 'cellular']
    configured = [r for r in config['uplinks'] if r['type'] == 'cellular']
    if (len(cells) != 1 or len(configured) != 1 or len(rows) != 3 or
            configured[0]['id'] != facts['cellular_id'] or
            configured[0].get('activation') != 'fallback' or
            configured[0]['priority'] <= max(r['priority'] for r in config['uplinks'] if r['type'] != 'cellular') or
            facts['activation'] != 'fallback'):
        raise HarnessError('automatic_cellular_policy_required')
    if not base['fixed'][-1][1]:
        raise HarnessError('wifi_active_profile_unverified')
    # Pin the actual active Wi-Fi UUID even if its optional configured UUID is absent.
    active_wifi = command(['/usr/bin/nmcli','-g','GENERAL.CON-UUID','device','show','wlan0']).strip()
    if active_wifi != base['fixed'][-1][1]:
        raise HarnessError('wifi_profile_changed')
    targets = config.get('ipv4_targets', ['1.1.1.1','8.8.8.8'])
    route = one(['/usr/sbin/ip','-j','-4','route','get',targets[0]])
    if route.get('nexthops') or route.get('type','unicast') != 'unicast' or route.get('table','main') not in ('main',254):
        raise Ambiguous('effective_route_ambiguous')
    devices = {'eth1':'ethernet','wlan0':'wifi'}
    if len(facts['active']) == 1 and facts['active'][0]['interface']:
        devices[facts['active'][0]['interface']] = 'cellular'
    effective = devices.get(route.get('dev'), 'other')
    if effective == 'other':
        raise Ambiguous('effective_route_unverified')
    if command(['/usr/bin/systemctl','--failed','--no-legend','--plain','--no-pager']).strip():
        raise HarnessError('unexpected_failed_service')
    for slot in base['slots']:
        if not rows[slot]['link'] or not rows[slot]['address']:
            raise HarnessError('preferred_interface_or_address_lost')
    base.update(facts=facts, wifi_target=wifi_target, wifi_net=str(wifi_address.network),
                cell_slot=cells[0], effective=effective)
    base['fixed'] += (wifi_target, str(wifi_address), active_wifi, facts['daemon'], facts['modem_identity'])
    return base


def baseline_checks():
    checkout = os.environ.get('PCS_QUALIFY_NORMAL_CHECKOUT', '')
    path = Path(checkout)
    if not checkout or not path.is_absolute() or not (path/'.git').exists():
        raise HarnessError('normal_checkout_required')
    if command(['/usr/bin/git','-C',checkout,'status','--porcelain']).strip():
        raise HarnessError('normal_checkout_dirty')
    sha = command(['/usr/bin/git','-C',checkout,'rev-parse','HEAD']).strip()
    if not re.fullmatch('[0-9a-f]{40}', sha):
        raise HarnessError('normal_checkout_unverified')
    if ((path/'scripts/pcs_uplink_manager.py').read_bytes() !=
            Path('/usr/local/lib/pcs/pcs_uplink_manager.py').read_bytes()):
        raise HarnessError('installed_uplink_manager_differs_from_normal_checkout')
    # Check the fixed install manifest, never run a checkout-provided command.
    command(['/usr/bin/sha256sum','--status','-c','/var/lib/pcs-qualification/install.sha256'])
    if table() is not None or any((RUNTIME/n).exists() for n in ('active.json','wan.json')):
        raise HarnessError('qualification_fault_present')
    return sha


def healthy_start(x):
    state, _ = ownership(x['facts'])
    return (state == 'inactive' and not x['facts']['owned'] and
        x['facts']['modem_state'] == 8 and x['facts']['registration'] in (1,5) and
        selected(x['uplink'],x['slots']) and x['effective'] == 'ethernet' and
        x['uplink']['uplinks'][x['slots'][1]]['internet'] is True and
        not x['uplink']['uplinks'][x['cell_slot']]['owned'] and
        not x['uplink']['uplinks'][x['cell_slot']].get('suppressed',False))


def admission(duration=180):
    sha=baseline_checks()
    require_safe()
    current=snapshot()
    if not healthy_start(current) or assess(checkpoint()) != 'PASS':
        raise HarnessError('healthy_inactive_cellular_baseline_required')
    recovery_budget=current['recovery_seconds']+3*current['poll_seconds']+8
    if (duration < current['failure_seconds']+current['recovery_seconds']+3*current['poll_seconds'] or
            45+duration+recovery_budget>295):
        raise HarnessError('policy_exceeds_bounded_campaign')
    return dict(scenario='FQ-302',gate='PASS',normal_commit=sha,cellular='inactive_unowned',
                modem='registered',effective_ipv4='ethernet',wifi='healthy',
                failure_seconds=current['failure_seconds'],recovery_seconds=current['recovery_seconds'])


def run(session, duration):
    if type(duration) is not int or not 90 <= duration <= 180:
        return 'BLOCKED','cellular_fault_budget_requires_90_to_180_seconds'
    receiver = None
    written = 0
    try:
        sha = baseline_checks()
        require_safe(session)
        baseline = snapshot()
        recovery_budget = baseline['recovery_seconds'] + 3*baseline['poll_seconds'] + 8
        minimum = baseline['failure_seconds'] + baseline['recovery_seconds'] + 3*baseline['poll_seconds']
        if duration < minimum or 45 + duration + recovery_budget > 295:
            return 'BLOCKED','policy_exceeds_bounded_campaign'
        if not healthy_start(baseline) or assess(checkpoint()) != 'PASS':
            return 'BLOCKED','healthy_inactive_cellular_baseline_required'
        session.event('cellular_baseline',dict(normal_commit=sha,ownership='inactive_unowned',
            poll_seconds=baseline['poll_seconds'],failure_seconds=baseline['failure_seconds'],
            recovery_seconds=baseline['recovery_seconds'],fault_budget_seconds=duration,
            recovery_budget_seconds=recovery_budget,activation_request_observable=False))
        receiver = Receiver(session.id,baseline['server'],baseline['client'])
    except (OSError,ValueError,KeyError,TypeError):
        return 'BLOCKED','cellular_preflight_failed'
    session.event('wan_waiting_for_witness',dict(port=39841,timeout_seconds=45))
    started = time.monotonic()
    injected = removed = recovered = None
    token = None
    seen = set()
    next_sample = started
    warning = False
    def event_once(name, now):
        if name not in seen:
            session.event(name,dict(elapsed_seconds=round(now-(injected or started),3)))
            seen.add(name)
    try:
        while True:
            receiver.poll()
            now = time.monotonic()
            for row in receiver.receipts.samples[written:]:
                session.event('lan_witness',row); written += 1
            if boot_id() != baseline['facts']['boot']:
                return 'ABORTED','boot_changed'
            if injected is None:
                if now-started > 45:
                    return 'BLOCKED','verified_lan_witness_missing'
                if not receiver.receipts.ready(now):
                    continue
                def verify():
                    require_safe(session,'before_fault')
                    current=snapshot()
                    receiver.poll()
                    if current['fixed'] != baseline['fixed'] or not healthy_start(current) or not receiver.receipts.ready(time.monotonic()):
                        raise HarnessError('cellular_preflight_changed')
                injected=time.monotonic()
                arm_cellular(session.id,baseline['target'],baseline['wifi_target'],duration,
                    baseline['lan'],baseline['wan'],baseline['wifi_net'],verify=verify)
                session.event('wan_fault_started',dict(family=4,targets=2,kernel_timeout_seconds=duration))
                now=time.monotonic()
            if receiver.receipts.invalid:
                return 'INCONCLUSIVE','witness_contact_lost'
            if any(not r['ok'] for r in receiver.receipts.samples):
                return 'FAIL','lan_http_sample_failed'
            if not receiver.receipts.ready(now):
                return 'INCONCLUSIVE','witness_contact_lost'
            if removed is None and now-injected < duration:
                if not lease_valid(session.id):
                    return 'ABORTED','fault_lease_lost'
            if now >= next_sample:
                if removed is None and now-injected < duration:
                    owned_handle(table(),session.id,CELLULAR_OWNER)
                require_safe(session,'checkpoint')
                current=snapshot()
                if current['fixed'] != baseline['fixed']:
                    return 'ABORTED','cellular_identity_or_policy_changed'
                state, observed_token=ownership(current['facts'],token,recovering=removed is not None)
                if observed_token is not None:
                    token=observed_token
                rows=current['uplink']['uplinks']
                ethernet,wifi=(rows[s] for s in baseline['slots'])
                cell=rows[current['cell_slot']]
                evidence=checkpoint()
                health=assess(evidence)
                session.event('checkpoint',evidence)
                if health in ('FAIL','INCONCLUSIVE'):
                    return health,'required_observation_unhealthy'
                warning |= evidence['power']['status']=='warn'
                session.event('cellular_observation',dict(ownership=state,
                    ethernet_ipv4=ethernet['internet'],wifi_ipv4=wifi['internet'],
                    cellular_ipv4=cell['internet'],cellular_ipv6=cell['internet6'],
                    selected_cellular=cell['selected'],cache_owned=cell['owned'],
                    effective_ipv4=current['effective'],bearer_active=current['facts']['bearer_connected'],
                    dns='not_measured'))
                if removed is None:
                    for name,row in (('ethernet',ethernet),('wifi',wifi)):
                        if row['internet'] is False:event_once(name+'_probe_failed',now)
                    both=ethernet['internet'] is False and wifi['internet'] is False
                    if both:event_once('preferred_paths_unhealthy',now)
                    if state.startswith('owned_') and now-injected < baseline['failure_seconds']-baseline['poll_seconds']-8:
                        return 'FAIL','cellular_activation_before_failure_window'
                    if state.startswith('owned_'):event_once('cellular_owned',now)
                    if state=='owned_active':event_once('cellular_bearer_active',now)
                    if current['effective']=='cellular':event_once('cellular_route_effective',now)
                    if (both and state=='owned_active' and cell['owned'] and cell['selected'] and
                            cell['active'] and cell['internet'] is True and current['effective']=='cellular'):
                        event_once('cellular_internet_proven',now)
                        try:
                            dns=bool(command(['/usr/bin/getent','ahostsv4','example.com'],timeout=2).strip())
                        except HarnessError:
                            dns=False
                        session.event('cellular_dns_observation',dict(result='PASS' if dns else 'unavailable',
                            scope='system_resolver_separate_from_interface_probe'))
                        warning |= not dns
                        restore(expected_session=session.id)
                        removed=time.monotonic()
                        session.event('wan_fault_removed',dict(verified=True))
                else:
                    if ethernet['internet'] is True:event_once('preferred_path_healthy',now)
                    if selected(current['uplink'],baseline['slots']) and current['effective']=='ethernet':
                        if now-removed < baseline['recovery_seconds']-baseline['poll_seconds']-2:
                            return 'FAIL','preferred_recovery_before_hysteresis_window'
                        event_once('preferred_route_restored',now)
                        if state=='inactive' and not cell['owned'] and not cell['active']:
                            event_once('cellular_disconnected_and_ownership_cleared',now)
                            if recovered is None:
                                recovered=time.monotonic()
                                session.event('wan_recovery_observed',dict(elapsed_seconds=round(recovered-removed,3)))
                                receiver.finish(time.monotonic())
                next_sample=time.monotonic()+2
            if removed is None and time.monotonic()-injected >= duration:
                return 'FAIL','automatic_cellular_fallback_not_proven_within_budget'
            if removed is not None and time.monotonic()-removed > recovery_budget:
                return 'FAIL','preferred_recovery_or_owned_release_deadline'
            if recovered is not None and receiver.finished:
                verdict=receiver.receipts.coverage(injected,recovered)
                if verdict=='PASS' and warning:verdict='PASS WITH OBSERVATION'
                return verdict,'automatic_cellular_ownership_and_release'
    except RFBlocked:
        return 'ABORTED','rf_safety_changed'
    except Ambiguous:
        return 'INCONCLUSIVE','cellular_ownership_or_route_unproven'
    except KeyboardInterrupt:
        return 'ABORTED','interrupted'
    except HarnessError as exc:
        if str(exc)=='unexpected_failed_service':
            return 'FAIL','unexpected_failed_service'
        if str(exc) in ('wifi_identity_unverified','wifi_profile_changed','wan_identity_unverified',
                        'wan_permanent_identity_unverified','configured_wan_identity_changed',
                        'wan_addresses_ambiguous','preferred_interface_or_address_lost',
                        'direct_ipv4_lan_ssh_required','independent_direct_lan_control_required',
                        'ssh_server_not_lan_address','policy_routing_unsupported'):
            return 'ABORTED','control_or_preferred_identity_changed'
        return 'INCONCLUSIVE','required_cellular_observation_unavailable'
    except (OSError,ValueError,KeyError,TypeError):
        return 'HARNESS ERROR','cellular_execution_error'
    finally:
        try:
            if receiver is not None:
                receiver.close()
                for row in receiver.receipts.samples[written:]:session.event('lan_witness',row)
        finally:
            try:
                restore(expected_session=session.id)
                if table() is not None or any((RUNTIME/name).exists() for name in ('active.json','wan.json')):
                    raise HarnessError('cellular_fault_cleanup_unverified')
            except BaseException:
                session.event('cellular_fault_cleanup',dict(verified=False,cellular_manipulated=False))
                raise
            session.event('cellular_fault_cleanup',dict(verified=True,cellular_manipulated=False))
