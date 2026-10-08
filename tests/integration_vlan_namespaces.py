#!/usr/bin/env python3
"""Disposable Linux VLAN switch and IPv4/IPv6 firewall packet test.

Run sudo python3 tests/integration_vlan_namespaces.py. The parent only launches
unshare; all network mutations happen after proving a private network namespace.
No physical devices, host routes, host nftables, or NetworkManager are touched.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from pcs_vlan_switch import guard


def run(*args, **kwargs):
    return subprocess.run(args, check=True, text=True, capture_output=True, **kwargs).stdout


def main():
    if '--isolated' not in sys.argv:
        return subprocess.call(['unshare', '--net', sys.executable, __file__, '--isolated'])
    if os.readlink('/proc/self/ns/net') == os.readlink('/proc/1/ns/net'):
        raise RuntimeError('refusing host network namespace')
    processes = []
    def ns(pid, *args):
        return run('nsenter', '-t', str(pid), '-n', *args)
    try:
        for _ in range(3):
            process = subprocess.Popen(['unshare', '--net', 'sleep', '120'])
            processes.append(process)
            for attempt in range(100):
                if os.readlink(f'/proc/{process.pid}/ns/net') != os.readlink('/proc/self/ns/net'):
                    break
                time.sleep(0.01)
            else:
                raise RuntimeError('child namespace did not start')
        switch, lan, wan = [p.pid for p in processes]
        for pid in (switch, lan, wan):
            ns(pid, 'ip', 'link', 'set', 'lo', 'up')
        run('ip', 'link', 'set', 'lo', 'up')
        run('ip', 'link', 'add', 'eth0', 'type', 'veth', 'peer', 'name', 'trunk')
        run('ip', 'link', 'set', 'trunk', 'netns', str(switch))
        ns(switch, 'ip', 'link', 'add', 'br0', 'type', 'bridge', 'vlan_filtering', '1', 'vlan_default_pvid', '0')
        ns(switch, 'ip', 'link', 'set', 'br0', 'up')
        ns(switch, 'ip', 'link', 'set', 'trunk', 'master', 'br0')
        ns(switch, 'ip', 'link', 'set', 'trunk', 'up')
        for tag, endpoint, port in ((10, lan, 'lan'), (20, wan, 'wan')):
            ns(switch, 'ip', 'link', 'add', port, 'type', 'veth', 'peer', 'name', 'client')
            ns(switch, 'ip', 'link', 'set', 'client', 'netns', str(endpoint))
            ns(switch, 'ip', 'link', 'set', port, 'master', 'br0')
            ns(switch, 'ip', 'link', 'set', port, 'up')
            ns(switch, 'bridge', 'vlan', 'add', 'dev', port, 'vid', str(tag), 'pvid', 'untagged')
            ns(switch, 'bridge', 'vlan', 'add', 'dev', 'trunk', 'vid', str(tag))
            ns(endpoint, 'ip', 'link', 'set', 'client', 'up')
            run('ip', 'link', 'add', 'link', 'eth0', 'name', f'eth0.{tag}', 'type', 'vlan', 'id', str(tag))
            run('ip', 'link', 'set', f'eth0.{tag}', 'up')
        run('ip', 'link', 'set', 'eth0', 'up')
        for interface, address in (('eth0.10', '10.42.0.1/24'), ('eth0.20', '192.0.2.2/24'),
                                   ('eth0.10', 'fd42::1/64'), ('eth0.20', 'fd20::2/64')):
            run('ip', 'address', 'add', address, 'dev', interface)
        for pid, addresses in ((lan, ('10.42.0.100/24', 'fd42::100/64')),
                               (wan, ('192.0.2.1/24', 'fd20::1/64'))):
            for address in addresses:
                ns(pid, 'ip', 'address', 'add', address, 'dev', 'client')
        ns(lan, 'ip', 'route', 'add', 'default', 'via', '10.42.0.1')
        ns(lan, 'ip', '-6', 'route', 'add', 'default', 'via', 'fd42::1')
        ns(wan, 'ip', 'route', 'add', '10.42.0.0/24', 'via', '192.0.2.2')
        ns(wan, 'ip', '-6', 'route', 'add', 'fd42::/64', 'via', 'fd20::2')
        run('sysctl', '-qw', 'net.ipv4.ip_forward=1')
        run('sysctl', '-qw', 'net.ipv6.conf.all.forwarding=1')
        text = guard('02:00:00:00:00:02', '02:00:00:00:00:04')
        run('nft', '-c', '-f', '-', input=text)
        run('nft', '-f', '-', input=text)
        time.sleep(2)  # bounded IPv6 DAD in disposable namespaces
        def ping(pid, target, succeeds):
            result = subprocess.run(['nsenter', '-t', str(pid), '-n', 'ping', '-n', '-c', '1', '-W', '1', target], capture_output=True)
            if (result.returncode == 0) != succeeds:
                raise AssertionError(f'packet expectation failed: {target}, expected success={succeeds}: {result.stderr!r}')
        for target in ('10.42.0.1', '192.0.2.1', 'fd20::1'):
            ping(lan, target, True)
        for target in ('192.0.2.2', '10.42.0.100', 'fd42::100'):
            ping(wan, target, False)
        # Same endpoint now emulates management identities; bridged client above passed.
        for identity in ('02:00:00:00:00:02', '02:00:00:00:00:04'):
            ns(lan, 'ip', 'link', 'set', 'client', 'address', identity)
            ping(lan, '192.0.2.1', False)
            ping(lan, 'fd20::1', False)
        # Upstream cannot reach a LAN client by direct L2/ARP on the access VLAN.
        ns(wan, 'ip', 'address', 'add', '10.42.0.200/24', 'dev', 'client')
        ping(wan, '10.42.0.100', False)
        print('PASS: VLAN 10/20 separation, routed client traffic, WAN ingress denial, IPv4/IPv6 infrastructure egress denial')
    finally:
        for process in processes:
            process.terminate()
        for process in processes:
            process.wait(timeout=5)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
