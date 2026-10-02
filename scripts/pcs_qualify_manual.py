"""FQ-303: observe an audited operator activation; never control cellular."""
import math
import time
from pcs_qualify_cellular import (snapshot, baseline_checks, selected, checkpoint, assess,
    command, boot_id, RUNTIME, HarnessError, Ambiguous, require_safe, RFBlocked,
    Receiver, restore, table, lease_valid, owned_handle)
from pcs_qualify_fault import arm_manual
from pcs_qualify_wan import MANUAL_OWNER


class ManualFailure(HarnessError):
    pass


def manual_identity(facts, expected=None):
    """Private exact identity. Nothing in this return value is exported."""
    if facts['boot'] != boot_id() or not isinstance(facts.get('owned'), dict):
        raise Ambiguous('operator_context_changed')
    if facts['owned']:
        raise ManualFailure('manager_claimed_operator_session')
    rows = facts['active']
    if not rows or not facts['bearer_connected']:
        raise ManualFailure('operator_session_disappeared')
    if len(rows) != 1:
        raise Ambiguous('operator_provenance_unverified')
    row = rows[0]
    if (row['state'] != 2 or row['profile'] != facts['cellular_profile'] or
            not row['interface'] or row['interface'] in ('eth0','eth1','wlan0','lo') or
            facts['modem_state'] != 11 or facts['registration'] not in (1,5) or
            not isinstance(facts['suppressed'], list) or facts['suppressed']):
        raise Ambiguous('operator_provenance_unverified')
    identity = (facts['boot'], facts['daemon'], facts['cellular_id'],
                row['session'], row['profile'], row['interface'], facts['modem_identity'])
    if expected is not None and identity != expected[:7]:
        raise ManualFailure('operator_session_identity_changed')
    records = facts.get('operator_sessions')
    record = records.get(facts['cellular_id']) if isinstance(records,dict) else None
    if (not isinstance(record,dict) or record.get('origin') != 'operator_connect' or
            any(record.get(k) != v for k,v in dict(boot=facts['boot'],daemon=facts['daemon'],
                session=row['session'],profile=row['profile']).items()) or
            any(type(record.get(k)) not in (int,float) or not math.isfinite(record[k]) or record[k] <= 0
                for k in ('monotonic','utc')) or record['monotonic'] > time.monotonic()):
        raise Ambiguous('operator_provenance_unverified')
    identity += (record['monotonic'],record['utc'])
    if expected is not None and identity != expected:
        raise ManualFailure('operator_session_identity_changed')
    return identity


def healthy_start(x):
    manual_identity(x['facts'])
    cell=x['uplink']['uplinks'][x['cell_slot']]
    return (selected(x['uplink'],x['slots']) and x['effective']=='ethernet' and
            x['uplink']['uplinks'][x['slots'][1]]['internet'] is True and
            cell['internet'] is True and not cell['owned'] and not cell.get('suppressed',False))


def prepare(duration):
    if type(duration) is not int or not 90 <= duration <= 180:
        raise HarnessError('cellular_fault_budget_requires_90_to_180_seconds')
    sha=baseline_checks()
    require_safe()
    baseline=snapshot()
    expected=manual_identity(baseline['facts'])
    if not healthy_start(baseline) or assess(checkpoint()) != 'PASS':
        raise HarnessError('healthy_operator_cellular_baseline_required')
    recovery=baseline['recovery_seconds']+5*baseline['poll_seconds']+8
    if (duration < baseline['failure_seconds']+baseline['recovery_seconds']+3*baseline['poll_seconds'] or
            45+duration+recovery>295):
        raise HarnessError('policy_exceeds_bounded_campaign')
    return sha,baseline,expected


def admission(duration=150):
    sha,_,_=prepare(duration)
    return dict(scenario='FQ-303',gate='PASS',normal_commit=sha,
                cellular='audited_operator_session',manager_owned=False)


def run(session,duration):
    try:
        sha,baseline,expected=prepare(duration)
    except (HarnessError,OSError,ValueError,KeyError,TypeError):
        return 'BLOCKED','manual_cellular_admission_unverified'
    session.event('manual_cellular_baseline',dict(normal_commit=sha,
        provenance='production_operator_connect_audit',manager_owned=False,
        identity_pinned=True,poll_seconds=baseline['poll_seconds'],
        failure_seconds=baseline['failure_seconds'],recovery_seconds=baseline['recovery_seconds']))
    result=_campaign(session,duration,baseline,expected)
    # _campaign has already removed only qualification-owned state. Observe the
    # operator's session again; never reconnect it or reconstruct its ownership.
    try:
        final=snapshot()
        manual_identity(final['facts'],expected)
        if final['uplink']['uplinks'][final['cell_slot']]['owned']:
            raise ManualFailure('manager_claimed_operator_session')
        if result[0] in ('PASS','PASS WITH OBSERVATION') and not (
                selected(final['uplink'],final['slots']) and final['effective']=='ethernet'):
            raise ManualFailure('preferred_route_lost_after_cleanup')
        session.event('operator_session_after_cleanup',dict(preserved=True,manager_owned=False))
    except ManualFailure as exc:
        session.event('operator_session_after_cleanup',dict(preserved=False))
        return 'FAIL',str(exc)
    except (HarnessError,OSError,ValueError,KeyError,TypeError):
        if result[0] != 'FAIL':return 'INCONCLUSIVE','operator_session_cleanup_unverified'
    return result


def _campaign(session, duration, baseline, expected):
    receiver = Receiver(session.id,baseline['server'],baseline['client'])
    written = 0
    recovery_budget = baseline['recovery_seconds'] + 3*baseline['poll_seconds'] + 8
    session.event('wan_waiting_for_witness',dict(port=39841,timeout_seconds=45))
    started = time.monotonic()
    injected = removed = recovered = finished_at = None
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
                    checking = time.monotonic()
                    require_safe(session,'before_fault')
                    current=snapshot()
                    receiver.poll()
                    checked=dict(identity_matches=current['fixed'] == baseline['fixed'],
                                 healthy=healthy_start(current) and manual_identity(current['facts']) == expected,
                                 witness_ready=receiver.receipts.ready(time.monotonic()))
                    session.event('cellular_final_check',dict(elapsed_seconds=round(time.monotonic()-checking,3),**checked))
                    if not all(checked.values()):
                        raise HarnessError('cellular_preflight_changed')
                injected=time.monotonic()
                arm_manual(session.id,baseline['target'],baseline['wifi_target'],duration,
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
                    owned_handle(table(),session.id,MANUAL_OWNER)
                require_safe(session,'checkpoint')
                current=snapshot()
                if current['fixed'] != baseline['fixed']:
                    return 'ABORTED','cellular_identity_or_policy_changed'
                manual_identity(current['facts'], expected)
                state='operator_active'
                if current['uplink']['uplinks'][current['cell_slot']]['owned']:
                    raise ManualFailure('manager_claimed_operator_session')
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
                    same_operator_session=True,operator_provenance=True,
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
                    if current['effective']=='cellular':event_once('cellular_route_effective',now)
                    if (both and not cell['owned'] and cell['selected'] and
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
                    if recovered is not None and not (selected(current['uplink'],baseline['slots']) and current['effective']=='ethernet'):
                        return 'FAIL','preferred_route_lost_after_recovery'
                    if ethernet['internet'] is True:event_once('preferred_path_healthy',now)
                    if selected(current['uplink'],baseline['slots']) and current['effective']=='ethernet':
                        if now-removed < baseline['recovery_seconds']-baseline['poll_seconds']-2:
                            return 'FAIL','preferred_recovery_before_hysteresis_window'
                        event_once('preferred_route_restored',now)
                        event_once('operator_session_preserved',now)
                        # Observe at least two additional production polls after
                        # preferred recovery, rather than ending at its first sample.
                        if recovered is None:
                            recovered=time.monotonic()
                            session.event('wan_recovery_observed',dict(elapsed_seconds=round(recovered-removed,3)))
                        if finished_at is None and time.monotonic()-recovered >= 2*baseline['poll_seconds']:
                            finished_at=time.monotonic()
                            receiver.finish(finished_at)
                next_sample=time.monotonic()+2
            if removed is None and time.monotonic()-injected >= duration:
                return 'FAIL','manual_cellular_routing_not_proven_within_budget'
            if removed is not None and recovered is None and time.monotonic()-removed > recovery_budget:
                return 'FAIL','preferred_recovery_deadline'
            if recovered is not None and receiver.finished:
                verdict=receiver.receipts.coverage(injected,finished_at)
                if verdict=='PASS' and warning:verdict='PASS WITH OBSERVATION'
                return verdict,'manual_cellular_session_preserved'
    except ManualFailure as exc:
        return 'FAIL',str(exc)
    except RFBlocked:
        return 'ABORTED','rf_safety_changed'
    except Ambiguous as exc:
        # Fixed codes only; never export raw D-Bus identities or exceptions.
        reason = str(exc)
        if reason not in ('cellular_ownership_unavailable', 'unexpected_manager_ownership',
                          'operator_or_replaced_cellular_session', 'cellular_ownership_unproven',
                          'cellular_state_ambiguous', 'effective_route_ambiguous',
                          'effective_route_unverified', 'route_snapshot_identity_changed',
                          'route_snapshot_unstable', 'operator_provenance_unverified', 'operator_context_changed'):
            reason = 'cellular_ownership_or_route_unproven'
        session.event('cellular_admission_lost', dict(reason=reason))
        return 'INCONCLUSIVE',reason
    except KeyboardInterrupt:
        return 'ABORTED','interrupted'
    except HarnessError as exc:
        reason = str(exc)
        if reason in ('lease_setup_too_slow','lease_expired_before_injection',
                      'cellular_preflight_changed','collector_failed','collector_unavailable',
                      'collector_truncated','collector_invalid','firewall_table_collision',
                      'expiry_not_armed'):
            session.event('cellular_observation_error',dict(reason=reason))
            return 'INCONCLUSIVE',reason
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
