#!/usr/bin/env python3
"""Opt-in root integration test. Run ONLY in a disposable Linux VM.

Creates pcsut* namespaces/interfaces and an otherwise absent eth0 test LAN.
No real network or live PCS host is an acceptable target.
"""
import argparse
import functools
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import pcs_uplink_manager as m


def run(*args):
    return subprocess.run(args, text=True, capture_output=True, timeout=20, check=True).stdout.strip()


class VethNetworkManager(m.NetworkManager):
    """Use production adapter with only veth's test-only device type translated.

    A veth inherits NetworkManager's Wired API but reports DeviceType 31.
    Production hardware discovery continues to require Ethernet type 1.
    """
    def prop(self, path, interface, name):
        value = super().prop(path, interface, name)
        if interface == m.BUS + '.Device' and name == 'DeviceType':
            device_name = str(super().prop(path, interface, 'Interface'))
            if device_name in ('pcsut0', 'pcsut1'):
                return 1
        return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--isolated-vm', action='store_true', required=True)
    parser.parse_args()
    if os.geteuid() != 0 or Path('/sys/class/net/eth0').exists():
        raise SystemExit('Requires root in a disposable VM WITHOUT an existing eth0.')
    for name in ('pcsut0', 'pcsut1', 'pcsutclient'):
        if Path('/sys/class/net', name).exists():
            raise SystemExit('Test interface already exists; refusing to overwrite it.')
    processes, profiles, namespaces, devices = [], [], [], []
    with tempfile.TemporaryDirectory(prefix='pcs-uplink-integration-') as folder:
        try:
            rows = []
            for index in (0, 1):
                ns, iface = f'pcsut{index}', f'pcsut{index}'
                run('ip', 'netns', 'add', ns)
                namespaces.append(ns)
                run('ip', 'link', 'add', iface, 'type', 'veth', 'peer', 'name', f'pcsutpeer{index}')
                devices.append(iface)
                run('ip', 'link', 'set', f'pcsutpeer{index}', 'netns', ns)
                peer = f'pcsutpeer{index}'
                nsrun = functools.partial(run, 'ip', 'netns', 'exec', ns)
                nsrun('ip', 'link', 'set', 'lo', 'up')
                nsrun('ip', 'link', 'set', peer, 'up')
                nsrun('ip', 'addr', 'add', f'10.61.{index}.1/24', 'dev', peer)
                nsrun('ip', '-6', 'addr', 'add', f'2001:db8:{index + 61}::1/64', 'dev', peer)
                for target in ('1.1.1.1', '8.8.8.8'):
                    nsrun('ip', 'addr', 'add', target + '/32', 'dev', 'lo')
                for target in m.Config.ipv6_targets:
                    nsrun('ip', '-6', 'addr', 'add', target + '/128', 'dev', 'lo')
                processes.append(subprocess.Popen(['ip', 'netns', 'exec', ns, 'dnsmasq', '--keep-in-foreground', '--port=0', '--bind-interfaces', f'--interface={peer}', f'--dhcp-range=10.61.{index}.20,10.61.{index}.40,255.255.255.0,2m', f'--dhcp-option=3,10.61.{index}.1', '--dhcp-option=6,1.1.1.1', f'--dhcp-leasefile={folder}/lease{index}'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
                server = f'from http.server import BaseHTTPRequestHandler,HTTPServer\nclass H(BaseHTTPRequestHandler):\n def do_GET(self):\n  self.send_response(200);self.end_headers();self.wfile.write(b"WAN{index}")\nHTTPServer(("1.1.1.1",8087),H).serve_forever()'
                processes.append(subprocess.Popen(['ip', 'netns', 'exec', ns, 'python3', '-c', server], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
                run('nmcli', 'connection', 'add', 'type', 'ethernet', 'ifname', iface, 'con-name', iface, 'connection.autoconnect', 'no', 'ipv4.method', 'auto', 'ipv4.route-metric', str(100 + index), 'ipv6.method', 'manual', 'ipv6.addresses', f'2001:db8:{index + 61}::2/64', 'ipv6.gateway', f'2001:db8:{index + 61}::1', 'ipv6.route-metric', str(100 + index))
                profiles.append(iface)
                run('nmcli', '--wait', '15', 'connection', 'up', iface)
                uuid = run('nmcli', '-g', 'connection.uuid', 'connection', 'show', iface)
                rows.append(m.Uplink(iface, f'WAN{index}', 'ethernet', index + 1, interface=iface, profile=uuid))
                Path(f'/proc/sys/net/ipv4/conf/{iface}/rp_filter').write_text('1\n')
            # A real forwarded client on a NetworkManager-shared test LAN.
            run('ip', 'netns', 'add', 'pcsutclient')
            namespaces.append('pcsutclient')
            run('ip', 'link', 'add', 'eth0', 'type', 'veth', 'peer', 'name', 'pcsutclient')
            devices.append('eth0')
            run('ip', 'link', 'set', 'pcsutclient', 'netns', 'pcsutclient')
            run('nmcli', 'connection', 'add', 'type', 'ethernet', 'ifname', 'eth0', 'con-name', 'pcsutlan', 'ipv4.method', 'shared', 'ipv4.addresses', '10.42.0.1/24', 'ipv6.method', 'disabled')
            profiles.append('pcsutlan')
            run('nmcli', '--wait', '15', 'connection', 'up', 'pcsutlan')
            client = functools.partial(run, 'ip', 'netns', 'exec', 'pcsutclient')
            client('ip', 'link', 'set', 'lo', 'up')
            client('ip', 'link', 'set', 'pcsutclient', 'up')
            client('ip', 'addr', 'add', '10.42.0.100/24', 'dev', 'pcsutclient')
            client('ip', 'route', 'add', 'default', 'via', '10.42.0.1')
            fetch = lambda: client('python3', '-c', 'import urllib.request;print(urllib.request.urlopen("http://1.1.1.1:8087",timeout=3).read().decode())')
            nm = VethNetworkManager()
            cfg = m.Config(tuple(rows), mode='auto', probe_timeout=1)
            controller = m.Controller(cfg, nm, folder)
            real_step = controller.step
            def settled_step(now):
                status = real_step(now)
                print(json.dumps({'now': now, 'selected': status['selected_id'], 'selected6': status['selected6_id'], 'error': status['error'], 'health': {u['id']: [u['internet'], u['internet6']] for u in status['uplinks']}}), flush=True)
                # Synthetic monotonic timestamps test 30-second windows without
                # a multi-minute fixture; allow real IPv6 DAD/route convergence
                # between samples, as the production 10-second poll does.
                time.sleep(2)
                return status
            controller.step = settled_step
            before = run('nmcli', '-g', 'ipv4.addresses,ipv4.method', 'connection', 'show', 'pcsutlan')
            controller.step(0)
            status = controller.step(30)
            assert not status['error'], status
            assert status['active_id'] == 'pcsut0', status
            assert fetch() == 'WAN0'
            deadline = time.monotonic() + 5
            while True:
                observed = nm.observe(cfg)
                if all(o.internet and o.internet6 for o in observed.values()):
                    break
                if time.monotonic() >= deadline:
                    raise AssertionError(observed)
                time.sleep(0.2)
            assert Path('/proc/sys/net/ipv4/conf/pcsut1/rp_filter').read_text().strip() == '2'
            # Satellite outage: DHCP/carrier and default route stay present.
            run('ip', 'netns', 'exec', 'pcsut0', 'iptables', '-I', 'INPUT', '-d', '1.1.1.1', '-j', 'DROP')
            run('ip', 'netns', 'exec', 'pcsut0', 'iptables', '-I', 'INPUT', '-d', '8.8.8.8', '-j', 'DROP')
            status = controller.step(40)
            assert status['active_id'] == 'pcsut0', status
            status = controller.step(70)
            assert not status['error'], status
            assert status['active_id'] == 'pcsut1', status
            assert fetch() == 'WAN1'
            o = nm.observe(cfg)['pcsut0']
            assert o.link and o.address and not o.internet, o
            # Saved DHCP metric remains intact; reconnect/renew is reconciled.
            assert run('nmcli', '-g', 'ipv4.route-metric', 'connection', 'show', 'pcsut0') == '100'
            run('nmcli', 'device', 'reapply', 'pcsut0')
            status = controller.step(80)
            assert status['active_id'] == 'pcsut1', status
            run('ip', 'netns', 'exec', 'pcsut0', 'iptables', '-F', 'INPUT')
            controller.step(90)
            status = controller.step(110)
            assert status['active_id'] == 'pcsut1', status
            status = controller.step(120)
            assert status['active_id'] == 'pcsut0', status
            assert fetch() == 'WAN0'
            assert run('nmcli', '-g', 'ipv4.addresses,ipv4.method', 'connection', 'show', 'pcsutlan') == before
            assert status['usage']['total_bytes'] > 0
            for target in cfg.ipv6_targets:
                run('ip', 'netns', 'exec', 'pcsut0', 'ip6tables', '-I', 'INPUT', '-d', target, '-j', 'DROP')
            assert controller.step(130)['selected6_id'] == 'pcsut0'
            assert controller.step(160)['selected6_id'] == 'pcsut1'
            run('ip', 'netns', 'exec', 'pcsut0', 'ip6tables', '-F', 'INPUT')
            controller.step(170)
            assert controller.step(190)['selected6_id'] == 'pcsut1'
            status = controller.step(200)
            assert status['selected6_id'] == 'pcsut0', status
            sessions = {uid: o.session for uid, o in nm.observe(cfg).items()}
            from dataclasses import replace
            manual = replace(cfg, mode='manual')
            controller = m.Controller(manual, nm, folder)
            controller.step(210)
            assert {uid: o.session for uid, o in nm.observe(manual).items()} == sessions
            assert all(int(nm.applied(o)[0]['ipv4']['route-metric']) == 100 + index for index, o in enumerate(nm.observe(manual).values()))
            assert not controller.state['owned']
            print('PASS: real NM DHCP/Reapply, IPv4/IPv6 bound probes, strict RPF, client NAT failover/recovery, saved profiles, LAN and usage')
        finally:
            for process in processes:
                process.terminate()
            for process in processes:
                process.wait(timeout=5)
            for profile in reversed(profiles):
                subprocess.run(['nmcli', 'connection', 'delete', profile], capture_output=True, timeout=10)
            for device in reversed(devices):
                subprocess.run(['ip', 'link', 'delete', device], capture_output=True, timeout=5)
            for ns in namespaces:
                subprocess.run(['ip', 'netns', 'delete', ns], capture_output=True, timeout=5)


if __name__ == '__main__':
    main()
