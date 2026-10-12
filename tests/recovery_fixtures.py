"""Synthetic recovery state. Never read /etc or private operator archives."""
import io
import json
from pathlib import Path
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import pcs_vlan_switch as vlan
import pcs_recovery_manifest as manifest

WIFI = '10000000-0000-4000-8000-000000000001'
CELL = '10000000-0000-4000-8000-000000000002'
LAN = '10000000-0000-4000-8000-000000000003'
WAN = '10000000-0000-4000-8000-000000000004'
SECRET = 'SyntheticOnly9'


def state(mode='vlan', wifi=False, cellular=False, policies=True):
    data = {manifest.MODE: (mode + '\n').encode()}
    if mode == 'vlan':
        data.update({manifest.NM + name: text.encode() for name, text in vlan.profiles().items()})
        lan, wan, parent = [vlan.IDS[name] for name in ('pcs-lan-vlan', 'pcs-starlink-vlan', 'pcs-vlan-parent')]
        interfaces = dict(lan='eth0.10', wan='eth0.20', parent='eth0')
    else:
        lan, wan, parent = LAN, WAN, None
        interfaces = dict(lan='eth0', wan='eth1', parent=None)
        for role, identity, method in (('lan', lan, 'shared'), ('wan', wan, 'auto')):
            text = f'[connection]\nid=test-{role}\nuuid={identity}\ntype=ethernet\ninterface-name={interfaces[role]}\nautoconnect=false\n[ipv4]\nmethod={method}\n'
            if role == 'lan':
                text += 'address1=10.42.0.1/24\n'
            text += '[ipv6]\nmethod=disabled\n[ethernet]\nmtu=1500\n'
            data[manifest.NM + role + '.nmconnection'] = text.encode()
    uplinks = [dict(id='starlink', name='Synthetic WAN', type='vlan' if mode == 'vlan' else 'ethernet',
                    priority=1, interface=interfaces['wan'], profile=wan)]
    if mode == 'vlan':
        uplinks[0].update(parent='eth0', vlan_id=20)
    if wifi:
        data[manifest.NM + 'wifi.nmconnection'] = (f'[connection]\nid=synthetic-wifi\nuuid={WIFI}\ntype=wifi\ninterface-name=wlan0\nautoconnect=false\n[wifi]\nssid=SyntheticTest\nmode=infrastructure\n[wifi-security]\nkey-mgmt=wpa-psk\npsk={SECRET}\n[ipv4]\nmethod=auto\n[ipv6]\nmethod=disabled\n').encode()
        uplinks.append(dict(id='wifi', name='Synthetic Wi-Fi', type='wifi', priority=2, interface='wlan0', profile=WIFI))
    if cellular:
        data[manifest.NM + 'cellular.nmconnection'] = (f'[connection]\nid=synthetic-cellular\nuuid={CELL}\ntype=gsm\nautoconnect=false\n[gsm]\napn=synthetic.invalid\npassword={SECRET}\n[ipv4]\nmethod=auto\n[ipv6]\nmethod=disabled\n').encode()
        uplinks.append(dict(id='cellular', name='Synthetic Modem', type='cellular', priority=3, profile=CELL, activation='fallback'))
    if policies:
        data['etc/pcs/uplinks.json'] = manifest.json_bytes(dict(version=1, mode='auto', startup_grace_enabled=True,
            startup_grace_uplink='starlink', startup_grace_seconds=240, switch_assist=True, uplinks=uplinks))
        data['etc/pcs/uplink-management.json'] = b'{"version":1,"trusted":[]}\n'
        data['etc/pcs/bandwidth.yaml'] = (ROOT / 'config/bandwidth.example.yaml').read_bytes()
        data['etc/pcs/switch-monitor.json'] = b'{"version":1,"enabled":true}\n'
        data['etc/pcs/switch-monitor.community'] = (SECRET + '\n').encode()
        power = json.loads((ROOT / 'config/power-monitor.pcs.json').read_bytes())
        power['allow_shutdown'] = False
        power['monitors']['input']['shunt_ohms'] = 0.003  # Deliberately synthetic customization.
        data['etc/pcs/power-monitor.json'] = manifest.json_bytes(power)
    data['etc/pcs/operator-unrelated.json'] = b'{"note":"preserve, do not select"}\n'
    request = dict(schema=1, mode=mode, lan_uuid=lan, wan_uuid=wan, parent_uuid=parent, interfaces=interfaces)
    return data, request


def archive(path, data):
    with tarfile.open(path, 'w:gz') as stream:
        for name, value in data.items():
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(value), 0o600
            stream.addfile(info, io.BytesIO(value))
    return path


def bundle(root, data, sealed=True):
    raw = archive(root / 'old.tar.gz', data)
    if not sealed:
        return raw
    result = root / 'new.tar.gz'
    manifest.seal(raw, result, 'a' * 40, 'v2.2.0', machine_id=b'synthetic-machine')
    return result
