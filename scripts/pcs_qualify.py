#!/usr/bin/env python3
"""PCS qualification: fixed observation, lease proof, and guarded IPv4 WAN scenario."""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import resource
import signal
import subprocess
import time

from pcs_qualify_observe import assess, checkpoint
from pcs_qualify_safety import CAMPAIGN, arm, boot_cleanup, lease_valid, preflight, restore
from pcs_qualify_observe import command
from pcs_qualify_state import (HarnessError, RUNTIME, SESSIONS, Session, atomic_json,
                              boot_id, identifier, lock, private_dir, read_json, report)
from pcs_qualify_witness import validate as validate_witness
from pcs_qualify_lan import validate_file as validate_lan_witness

REGISTRY = {'FQ-001': 'Cached power/uplink observation; no continuity claim',
            'FQ-002': 'Independent expiry of a harmless private marker',
            'FQ-301-v4': 'IPv4 Ethernet WAN fault; direct LAN witness and RF isolation required',
            'FQ-302': 'Automatic cellular fallback ownership and release; dual IPv4 WAN fault',
            'FQ-303': 'Audited operator cellular session preservation; dual IPv4 WAN fault'}
EXIT = {'PASS': 0, 'PASS WITH OBSERVATION': 0, 'FAIL': 1, 'INCONCLUSIVE': 2,
        'ABORTED': 3, 'HARNESS ERROR': 4, 'BLOCKED': 5}


def limits():
    os.umask(0o077)
    for name, value in ((resource.RLIMIT_CORE, 0), (resource.RLIMIT_NOFILE, 64),
                        (resource.RLIMIT_FSIZE, 3 * 1024 * 1024),
                        (resource.RLIMIT_AS, 128 * 1024 * 1024), (resource.RLIMIT_CPU, 30)):
        resource.setrlimit(name, (value, value))


def campaign(scenario, duration):
    if not 5 <= duration <= 300:
        raise HarnessError('duration_out_of_bounds')
    private_dir(RUNTIME)
    # Recover killed runners before starting. This cannot acquire an active runner's lock.
    boot_cleanup()
    with lock(RUNTIME / 'campaign.lock'):
        session = Session(scenario)
        print(json.dumps({'session': session.id, 'scenario': scenario}), flush=True)
        result, reason = 'HARNESS ERROR', 'execution_error'
        if scenario in ('FQ-301-v4', 'FQ-302', 'FQ-303'):
            if scenario == 'FQ-303':
                from pcs_qualify_manual import run as run_wan
            elif scenario == 'FQ-302':
                from pcs_qualify_cellular import run as run_wan
            else:
                from pcs_qualify_scenario import run as run_wan
            try:
                preflight()
                result, reason = run_wan(session, duration)
                start = session.manifest['start']
                wall = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(start['utc'])).total_seconds()
                if result in ('PASS', 'PASS WITH OBSERVATION') and abs(wall - (time.monotonic() - start['monotonic'])) > 2:
                    result, reason = 'INCONCLUSIVE', 'wall_clock_changed'
            except KeyboardInterrupt:
                result, reason = 'ABORTED', 'interrupted'
            except (OSError, ValueError, TypeError, KeyError):
                result, reason = 'HARNESS ERROR', 'wan_execution_error'
            finally:
                try:
                    restore(expected_session=session.id)
                except (OSError, ValueError):
                    result, reason = 'HARNESS ERROR', 'cleanup_failed'
            from pcs_qualify_rf import RFBlocked, require_safe
            try:
                require_safe(session, 'after_cleanup')
            except RFBlocked as exc:
                if result in ('PASS', 'PASS WITH OBSERVATION'):
                    result, reason = 'ABORTED', str(exc)
            except (OSError, ValueError):
                result, reason = 'HARNESS ERROR', 'rf_evidence_write_failed'
            return finish(session, result, reason)
        try:
            try:
                control = preflight()
            except HarnessError:
                result, reason = 'BLOCKED', 'preflight_failed'
                return finish(session, result, reason)
            session.event('preflight', control)
            arm(session.id, 5 if scenario == 'FQ-002' else duration + 30)
            session.event('lease_armed', {'effect': 'private_marker'})
            if scenario == 'FQ-002':
                deadline = time.monotonic() + 12
                while any((RUNTIME / name).exists() for name in ('marker', 'active.json')) and time.monotonic() < deadline:
                    time.sleep(0.1)
                if (RUNTIME / 'marker').exists() or (RUNTIME / 'active.json').exists():
                    result, reason = 'FAIL', 'independent_expiry_failed'
                else:
                    result, reason = 'PASS', 'independent_marker_expiry'
            else:
                deadline = time.monotonic() + duration
                results = []
                while True:
                    if not lease_valid(session.id):
                        raise KeyboardInterrupt
                    evidence = checkpoint()
                    session.event('checkpoint', evidence)
                    results.append(assess(evidence))
                    if time.monotonic() >= deadline:
                        break
                    time.sleep(min(5, max(0, deadline - time.monotonic())))
                if boot_id() != session.manifest['start']['boot_id']:
                    result, reason = 'INCONCLUSIVE', 'boot_changed'
                else:
                    priority = ('FAIL', 'INCONCLUSIVE', 'PASS WITH OBSERVATION', 'PASS')
                    result = next(r for r in priority if r in results)
                    reason = 'cached_observation_only'
                    start = session.manifest['start']
                    wall = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(start['utc'])).total_seconds()
                    if abs(wall - (time.monotonic() - start['monotonic'])) > 2:
                        result, reason = 'INCONCLUSIVE', 'wall_clock_changed'
        except KeyboardInterrupt:
            result, reason = 'ABORTED', 'interrupted_or_expired'
        except (OSError, ValueError, TypeError, HarnessError):
            result, reason = 'HARNESS ERROR', 'execution_error'
        finally:
            try:
                restore()
            except (OSError, HarnessError):
                result, reason = 'HARNESS ERROR', 'cleanup_failed'
        return finish(session, result, reason)


def finish(session, result, reason):
    try:
        session.finish(result, reason)
    except (OSError, ValueError, HarnessError):
        print(json.dumps({'session': session.id, 'result': 'HARNESS ERROR', 'reason': 'record_write_failed'}))
        return EXIT['HARNESS ERROR']
    print(json.dumps({'session': session.id, 'result': result, 'report': str(session.path / 'report.md')}))
    return EXIT[result]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('list')
    for name in ('run', '_run'):
        run = sub.add_parser(name, help='fixed scenario campaign' if name == 'run' else argparse.SUPPRESS)
        run.add_argument('scenario', choices=REGISTRY)
        run.add_argument('--duration', type=int)
        run.add_argument('--normal-checkout', type=Path, help='read-only normal checkout identity for FQ-302')
    expiry = sub.add_parser('expire')
    expiry.add_argument('session', type=identifier)
    for name in ('cleanup', 'boot-cleanup'):
        sub.add_parser(name)
    check = sub.add_parser('preflight')
    check.add_argument('--scenario', choices=('FQ-301-v4','FQ-302','FQ-303'))
    check.add_argument('--normal-checkout', type=Path)
    show = sub.add_parser('report')
    show.add_argument('session', type=identifier)
    witness = sub.add_parser('witness')
    witness.add_argument('session', type=identifier)
    witness.add_argument('file', type=Path)
    args = parser.parse_args()
    if args.action in ('run', '_run') and args.duration is None:
        args.duration = 150 if args.scenario == 'FQ-303' else 180 if args.scenario == 'FQ-302' else 90 if args.scenario == 'FQ-301-v4' else 60
    if args.action in ('run', '_run', 'preflight') and args.normal_checkout is not None:
        os.environ['PCS_QUALIFY_NORMAL_CHECKOUT'] = str(args.normal_checkout)
    if args.action == 'list':
        print(json.dumps(REGISTRY, indent=2))
        return 0
    if os.geteuid() != 0:
        print('BLOCKED: root required')
        return EXIT['BLOCKED']
    limits()
    def interrupted(_signum, _frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    try:
        if args.action == 'run':
            if not 5 <= args.duration <= 300:
                raise HarnessError('duration_out_of_bounds')
            active = command(['/usr/bin/systemctl', 'show', '--value', '--property=ActiveState', CAMPAIGN]).strip()
            if active in ('active', 'activating', 'deactivating'):
                raise HarnessError('campaign_busy')
            invocation = ['/usr/bin/systemd-run', '--quiet', '--wait', '--pipe', '--collect',
                          '--unit=' + CAMPAIGN, '--property=Type=exec',
                          '--property=MemoryMax=128M', '--property=CPUQuota=20%',
                          '--property=TasksMax=16', '--property=RuntimeMaxSec=360s',
                          '--property=TimeoutStopSec=5s', '--property=KillMode=control-group',
                          '--property=LogRateLimitIntervalSec=30s', '--property=LogRateLimitBurst=30',
                          '--setenv=SSH_CONNECTION=' + os.environ.get('SSH_CONNECTION', ''),
                          '--setenv=PCS_QUALIFY_NORMAL_CHECKOUT=' + os.environ.get('PCS_QUALIFY_NORMAL_CHECKOUT', ''),
                          '/usr/local/sbin/pcs-qualify', '_run', args.scenario,
                          '--duration', str(args.duration)]
            try:
                return subprocess.run(invocation, check=False).returncode
            except KeyboardInterrupt:
                restore()
                return EXIT['ABORTED']
        if args.action == '_run':
            current = os.environ.get('INVOCATION_ID')
            actual = command(['/usr/bin/systemctl', 'show', '--value', '--property=InvocationID', CAMPAIGN]).strip()
            if not current or current != actual:
                raise HarnessError('owned_service_required')
            return campaign(args.scenario, args.duration)
        if args.action == 'preflight':
            from pcs_qualify_rf import observe
            evidence = preflight()
            if args.scenario == 'FQ-303':
                from pcs_qualify_manual import admission
                evidence['fq303'] = admission()
            if args.scenario == 'FQ-302':
                from pcs_qualify_cellular import admission
                evidence['fq302'] = admission()
            evidence['fq301_rf_safety'] = observe()
            # RF admission is advisory for Phase 1 observation. Only the WAN
            # scenario enforces it; this command does not certify all WAN gates.
            print(json.dumps(evidence))
        elif args.action == 'expire':
            restore(expected_session=args.session)
            try:
                boot_cleanup()
            except HarnessError as exc:
                if str(exc) != 'campaign_busy':
                    raise
        elif args.action == 'cleanup':
            fragment = command(['/usr/bin/systemctl', 'show', '--value', '--property=FragmentPath', CAMPAIGN]).strip()
            if fragment:
                executable = command(['/usr/bin/systemctl', 'show', '--value', '--property=ExecStart', CAMPAIGN]).strip()
                if (fragment != '/run/systemd/transient/' + CAMPAIGN or
                        '/usr/local/sbin/pcs-qualify _run ' not in executable):
                    raise HarnessError('campaign_unit_collision')
                command(['/usr/bin/systemctl', 'stop', CAMPAIGN], timeout=10)
            restore()
            try:
                boot_cleanup()
            except HarnessError as exc:
                if str(exc) != 'campaign_busy':
                    raise
        elif args.action == 'boot-cleanup':
            boot_cleanup()
        elif args.action == 'report':
            if not (SESSIONS / args.session).is_dir():
                raise HarnessError('unknown_session')
            path = private_dir(SESSIONS / args.session)
            report(path)
            print((path / 'report.md').read_text())
        elif args.action == 'witness':
            private_dir(RUNTIME)
            with lock(RUNTIME / 'campaign.lock'):
                if not (SESSIONS / args.session).is_dir():
                    raise HarnessError('unknown_session')
                path = private_dir(SESSIONS / args.session)
                value = read_json(path / 'session.json')
                if value.get('complete') is not True:
                    raise HarnessError('session_not_complete')
                try:
                    if value.get('scenario') in ('FQ-301-v4', 'FQ-302', 'FQ-303'):
                        summary = validate_lan_witness(args.file, args.session, path / 'events.jsonl')
                    else:
                        summary = validate_witness(args.file, args.session)
                except (OSError, ValueError, KeyError, TypeError):
                    summary = {'result': 'INCONCLUSIVE', 'scope': 'independent_http_sampling_only'}
                atomic_json(path / 'witness.json', summary)
                report(path)
                print(json.dumps(summary))
                return EXIT[summary['result']]
    except (OSError, ValueError, TypeError, HarnessError, KeyboardInterrupt):
        print('BLOCKED: qualification state or prerequisite unavailable; no raw diagnostics exported')
        return EXIT['BLOCKED']
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
