#!/usr/bin/env python3
"""Explicit root-only disposable network-namespace acceptance; no physical links."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

ROOT = Path(__file__).resolve().parents[1]


def run(*args, data=None, check=True):
    result = subprocess.run(list(args), input=data, text=True, capture_output=True, timeout=120)
    if check and result.returncode:
        raise RuntimeError(f'{args}: {result.stderr}\n{result.stdout}')
    return result.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true', required=True)
    parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit('Root is required; use a disposable Linux host')
    prefix = f'bwtest{os.getpid()}'
    spaces = {name: prefix+name for name in ('r', 'a', 'b', 'w')}
    created = []
    servers = []
    evidence = {}
    with tempfile.TemporaryDirectory(prefix='pcs-bandwidth-test-') as temp:
        temp = Path(temp)

        def ns(name, *args, **kwargs):
            return run('ip', 'netns', 'exec', spaces[name], *args, **kwargs)

        def manager(*args, check=True):
            return ns('r', 'python3', str(ROOT/'scripts/pcs_bandwidth.py'), '--config', str(temp/'config.yaml'), '--runtime', str(temp/'runtime'), *args, check=check)

        def test_rate(client, reverse=False, local=False, port=5201):
            target = '10.42.0.1' if local else '198.18.0.2'
            args = ['iperf3', '-c', target, '-p', str(port), '-t', '5', '-O', '1', '-J']
            if reverse:
                args.append('-R')
            result = json.loads(ns(client, *args))
            if 'error' in result:
                raise AssertionError(result['error'])
            return result['end']['sum_received']['bits_per_second']/1e6

        try:
            for name in spaces.values():
                run('ip', 'netns', 'add', name)
                created.append(name)
            for name in spaces:
                ns(name, 'ip', 'link', 'set', 'lo', 'up')
            ns('r', 'ip', 'link', 'add', 'lan', 'type', 'bridge')
            ns('r', 'ip', 'addr', 'add', '10.42.0.1/29', 'dev', 'lan')
            ns('r', 'ip', 'link', 'set', 'lan', 'up')
            for index, client in enumerate(('a', 'b'), 2):
                ns('r', 'ip', 'link', 'add', f'p{client}', 'type', 'veth', 'peer', 'name', f'c{client}')
                ns('r', 'ip', 'link', 'set', f'c{client}', 'netns', spaces[client])
                ns('r', 'ip', 'link', 'set', f'p{client}', 'master', 'lan')
                ns('r', 'ip', 'link', 'set', f'p{client}', 'up')
                ns(client, 'ip', 'link', 'set', f'c{client}', 'up')
                ns(client, 'ip', 'addr', 'add', f'10.42.0.{index}/29', 'dev', f'c{client}')
                ns(client, 'ip', 'route', 'add', 'default', 'via', '10.42.0.1')
            ns('r', 'ip', 'link', 'add', 'wan', 'type', 'veth', 'peer', 'name', 'srv')
            ns('r', 'ip', 'link', 'set', 'srv', 'netns', spaces['w'])
            for name, dev, address in [('r', 'wan', '198.18.0.1/24'), ('w', 'srv', '198.18.0.2/24')]:
                ns(name, 'ip', 'addr', 'add', address, 'dev', dev)
                ns(name, 'ip', 'link', 'set', dev, 'up')
            ns('r', 'ip', 'route', 'add', 'default', 'via', '198.18.0.2')
            ns('r', 'sysctl', '-qw', 'net.ipv4.ip_forward=1')
            ns('r', 'nft', '-f', '-', data='table ip test_nat { chain post { type nat hook postrouting priority srcnat; ip saddr 10.42.0.0/29 oifname "wan" masquerade; }; }\ntable inet sentinel { chain keep { counter; }; }\n')
            sentinel = ns('r', 'nft', '-s', 'list', 'table', 'inet', 'sentinel')
            root_before = json.loads(ns('r', 'tc', '-j', 'qdisc', 'show', 'dev', 'wan'))
            for name, port in [('w', 5201), ('w', 5202), ('r', 5201)]:
                servers.append(subprocess.Popen(['ip', 'netns', 'exec', spaces[name], 'iperf3', '-s', '-p', str(port)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            import yaml
            config = yaml.safe_load((ROOT/'config/bandwidth.example.yaml').read_text())
            config.update(profile='starlink', lan_network='10.42.0.0/29')
            config['profiles']['starlink'].update(total_down='20M', total_up='10M', default_down='3M', default_up='1M')
            config['clients'] = [{'ip': '10.42.0.2', 'name': 'A', 'download': '5M', 'upload': '2M'}]
            (temp/'config.yaml').write_text(yaml.safe_dump(config))
            print(manager('apply'), flush=True)
            status = json.loads(manager('status', '--json'))
            assert status['healthy'], status
            for name, direction, expected in [('a', 'down', 5), ('a', 'up', 2), ('b', 'down', 3), ('b', 'up', 1)]:
                measured = test_rate(name, reverse=direction == 'down')
                evidence[name+'_'+direction+'_mbps'] = measured
                assert expected*.55 < measured < expected*1.45, (name, direction, measured)
                print(name, direction, measured, flush=True)
            before = json.loads(manager('status', '--json'))
            evidence['local_mbps'] = test_rate('a', local=True)
            after = json.loads(manager('status', '--json'))
            assert before['clients'] == after['clients'], 'Local traffic incorrectly counted'
            assert evidence['local_mbps'] > 10, evidence
            manager('apply')
            assert after['clients'] == json.loads(manager('status', '--json'))['clients'], 'Idempotent apply reset counters'
            assert len([r for r in after['clients'] if r['up_bytes'] and r['down_bytes']]) == 2
            manager('client', '10.42.0.2', '--remove')
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(test_rate, 'a', True, False, 5201)
                second = pool.submit(test_rate, 'b', True, False, 5202)
                evidence['simultaneous_defaults_mbps'] = [first.result(), second.result()]
            assert all(1.8 < value < 4.2 for value in evidence['simultaneous_defaults_mbps']), evidence
            manager('client', '10.42.0.2', '--unlimited')
            evidence['unlimited_mbps'] = test_rate('a', reverse=True)
            assert 8 < evidence['unlimited_mbps'] < 28, evidence
            manager('client', '10.42.0.3', '--unlimited')
            config = yaml.safe_load((temp/'config.yaml').read_text())
            config['profiles']['starlink']['total_down'] = '8M'
            (temp/'config.yaml').write_text(yaml.safe_dump(config))
            manager('apply')
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(test_rate, 'a', True, False, 5201)
                second = pool.submit(test_rate, 'b', True, False, 5202)
                evidence['aggregate_mbps'] = first.result()+second.result()
            assert 4 < evidence['aggregate_mbps'] < 11, evidence
            # A changed rule must not be reported as healthy.
            ns('r', 'nft', 'flush', 'chain', 'inet', 'pcs_bandwidth', 'forward')
            assert not json.loads(manager('status', '--json'))['healthy']
            manager('apply')
            assert json.loads(manager('status', '--json'))['healthy']
            # No default route is a successful unshaped waiting state.
            ns('r', 'ip', 'route', 'del', 'default')
            manager('apply')
            assert json.loads(manager('status', '--json'))['status'] == 'WAITING_FOR_WAN'
            ns('r', 'ip', 'route', 'add', 'default', 'via', '198.18.0.2')
            manager('apply')
            manager('profile', 'unrestricted')
            assert not json.loads(manager('status', '--json'))['ifbs']
            evidence['unrestricted_mbps'] = test_rate('a', reverse=True)
            assert evidence['unrestricted_mbps'] > evidence['a_down_mbps']*2
            assert ns('r', 'nft', '-s', 'list', 'table', 'inet', 'sentinel') == sentinel
            assert json.loads(ns('r', 'tc', '-j', 'qdisc', 'show', 'dev', 'wan')) == root_before
            manager('profile', 'starlink')
            manager('stop')
            ns('a', 'ping', '-c', '1', '-W', '2', '198.18.0.2')
            assert 'pcsbw-' not in ns('r', 'ip', '-j', 'link')
            # A foreign object cannot be adopted or removed.
            ns('r', 'ip', 'link', 'add', 'pcsbw-up', 'type', 'dummy')
            result = manager('apply', check=False)
            assert 'dummy' in ns('r', 'ip', '-j', '-d', 'link', 'show', 'pcsbw-up')
            ns('r', 'ip', 'link', 'del', 'pcsbw-up')
            # A failed tc construction is unshaped, not a broken route.
            ns('r', 'tc', 'qdisc', 'add', 'dev', 'wan', 'ingress')
            manager('apply', check=False)
            ns('a', 'ping', '-c', '1', '-W', '2', '198.18.0.2')
            assert 'pcsbw-' not in ns('r', 'ip', '-j', 'link')
            print(json.dumps(evidence, indent=2), flush=True)
            print('PASS: NAT, client/default caps, local bypass, accounting, unlimited, idempotence, cleanup, collisions', flush=True)
        except Exception:
            if spaces['r'] in created:
                print(ns('r', 'ip', '-j', '-d', 'link'), flush=True)
                print(ns('r', 'tc', '-j', 'filter', 'show', 'dev', 'wan', 'egress'), flush=True)
                print(ns('r', 'tc', 'filter', 'show', 'dev', 'wan', 'egress'), flush=True)
            raise
        finally:
            for server in servers:
                server.terminate()
                server.wait(timeout=5)
            for name in reversed(created):
                run('ip', 'netns', 'delete', name, check=False)


if __name__ == '__main__':
    main()
