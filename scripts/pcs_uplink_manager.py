#!/usr/bin/env python3
"""PCS optional WAN selection, NetworkManager runtime policy and boot accounting.

No LAN profile is modified. Saved WAN profiles are never rewritten by the daemon.
The D-Bus adapter uses versioned Reapply and exact active-connection objects.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import time
from dataclasses import dataclass, field, replace

CONFIG = Path('/etc/pcs/uplinks.json')
RUNTIME = Path('/run/pcs-uplink-manager')
BUS = 'org.freedesktop.NetworkManager'
ROOT = '/org/freedesktop/NetworkManager'
PROPS = 'org.freedesktop.DBus.Properties'
LOCK = '/run/lock/pcs-uplink-policy.lock'
ETHERNET_IPV4_RECOVERY_SECONDS = 180
ETHERNET_IPV4_RETRY_SECONDS = 300


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f'.tmp.{os.getpid()}')
    temporary.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
    temporary.chmod(0o644)
    os.replace(temporary, path)


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def interface_name(value):
    return isinstance(value, str) and bool(re.fullmatch(r'[a-zA-Z0-9_.-]{1,15}', value)) and value not in {'lo', 'eth0'}


@dataclass(frozen=True)
class Uplink:
    id: str
    name: str
    type: str
    priority: int
    interface: str = ''
    mac: str = ''
    profile: str = ''
    activation: str = 'observe'


@dataclass(frozen=True)
class Config:
    uplinks: tuple[Uplink, ...]
    mode: str = 'manual'
    poll_seconds: int = 10
    failure_seconds: int = 30
    recovery_seconds: int = 30
    probe_timeout: int = 2
    ipv4_targets: tuple[str, ...] = ('1.1.1.1', '8.8.8.8')
    ipv6_targets: tuple[str, ...] = ('2606:4700:4700::1111', '2001:4860:4860::8888')


def load_config(path=CONFIG):
    raw = read_json(path)
    if not isinstance(raw, dict) or raw.get('version') != 1:
        raise ValueError('uplinks configuration requires version 1')
    if raw.get('mode', 'manual') not in {'manual', 'auto'}:
        raise ValueError('mode must be manual or auto')
    entries = raw.get('uplinks')
    if not isinstance(entries, list) or not 1 <= len(entries) <= 32:
        raise ValueError('configure between 1 and 32 uplinks')
    uplinks = []
    seen = {key: set() for key in ('id', 'priority', 'interface', 'mac', 'profile')}
    for entry in entries:
        u = Uplink(**entry)
        if not re.fullmatch(r'[a-z][a-z0-9_-]{0,31}', u.id) or not isinstance(u.name, str) or not u.name.strip() or len(u.name) > 80:
            raise ValueError('invalid uplink identity')
        if u.type not in {'ethernet', 'wifi', 'cellular'} or u.activation not in {'observe', 'fallback'}:
            raise ValueError('invalid uplink type or activation policy')
        if type(u.priority) is not int or not 1 <= u.priority <= 1000:
            raise ValueError('priority must be 1..1000')
        if u.interface and not interface_name(u.interface):
            raise ValueError('invalid or protected WAN interface')
        if u.mac and not re.fullmatch(r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}', u.mac):
            raise ValueError('invalid MAC address')
        if u.mac and (u.mac.lower() == '00:00:00:00:00:00' or int(u.mac[:2], 16) & 1):
            raise ValueError('WAN binding requires a nonzero unicast MAC address')
        if u.type != 'cellular' and not (u.interface or u.mac):
            raise ValueError('Ethernet/Wi-Fi requires an explicit interface or MAC')
        if (u.type == 'cellular' or u.activation == 'fallback') and not u.profile:
            raise ValueError('activation requires an explicit profile UUID')
        if u.profile and not re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', u.profile):
            raise ValueError('profile must be a NetworkManager UUID')
        for key in seen:
            val = getattr(u, key)
            val = val.lower() if isinstance(val, str) else val
            if val and val in seen[key]:
                raise ValueError(f'duplicate uplink {key}')
            seen[key].add(val)
        uplinks.append(u)
    settings = {}
    for key, default, low, high in [('poll_seconds', 10, 1, 300), ('failure_seconds', 30, 0, 3600), ('recovery_seconds', 30, 0, 3600), ('probe_timeout', 2, 1, 5)]:
        value = raw.get(key, default)
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f'invalid {key}')
        settings[key] = value
    for key, family, default in [('ipv4_targets', 4, Config.ipv4_targets), ('ipv6_targets', 6, Config.ipv6_targets)]:
        values = raw.get(key, default)
        if not isinstance(values, (list, tuple)) or not 1 <= len(values) <= 4:
            raise ValueError('probe targets require 1..4 literal addresses per family')
        if any(ipaddress.ip_address(v).version != family or not ipaddress.ip_address(v).is_global for v in values):
            raise ValueError('probe targets must be public IP literals')
        settings[key] = tuple(values)
    return Config(tuple(sorted(uplinks, key=lambda u: u.priority)), mode=raw.get('mode', 'manual'), **settings)


@dataclass
class Observation:
    interface: str = ''
    device: str = ''
    profile: str = ''
    session: str = ''
    link: bool = False
    address: bool = False
    address6: bool = False
    internet: bool | None = False
    internet6: bool | None = False
    error: str = ''
    addresses: list = field(default_factory=list)
    counters: dict | None = None
    counter_identity: str = ''

    @property
    def state(self):
        if self.error or self.internet is None:
            return 'unknown'
        if not self.interface:
            return 'unavailable'
        if not self.link:
            return 'link-down'
        if not self.address:
            return 'waiting-address'
        return 'healthy' if self.internet else 'no-internet'


class Policy:
    """Pure monotonic-time state machine; side effects are acknowledged separately."""
    def __init__(self, config):
        self.config = config
        self.selected = None
        self.good = {}
        self.bad = {}
        self.retry_at = {}

    def choose(self, observations, now, suppressed=()):
        for u in self.config.uplinks:
            o = observations[u.id]
            if o.internet is True and not o.error:
                self.good.setdefault(u.id, now)
                self.bad.pop(u.id, None)
            elif o.internet is False and not o.error:
                self.bad.setdefault(u.id, now)
                self.good.pop(u.id, None)
            else:
                self.good.pop(u.id, None)
                self.bad.pop(u.id, None)
        stable = [u for u in self.config.uplinks if u.id in self.good and now - self.good[u.id] >= self.config.recovery_seconds]
        current = observations.get(self.selected)
        if current and (current.error or current.internet is None):
            return self.selected, None
        if current and self.selected not in self.good and now - self.bad.get(self.selected, now) < self.config.failure_seconds:
            return self.selected, None
        desired = self.selected if current and current.internet else None
        if stable:
            best = stable[0]
            rank = {u.id: u.priority for u in self.config.uplinks}
            if desired is None or best.priority < rank[desired]:
                desired = best.id
        # A lower-priority activation requires *every* preferred source to have
        # sustained failure, including a source with IP but failed probes.
        for index, u in enumerate(self.config.uplinks):
            o = observations[u.id]
            preferred = self.config.uplinks[:index]
            if u.activation != 'fallback' or o.session or u.id in suppressed or o.error:
                continue
            if now < self.retry_at.get(u.id, 0):
                continue
            if all(p.id in self.bad and now - self.bad[p.id] >= self.config.failure_seconds for p in preferred) and u.id in self.bad and now - self.bad[u.id] >= self.config.failure_seconds:
                return desired, u.id
        return desired, None


class Accounting:
    def __init__(self, saved, boot):
        self.data = saved if isinstance(saved, dict) and saved.get('boot') == boot else {'boot': boot, 'uplinks': {}, 'partial': False}
        if 'baselines' not in self.data:
            self.data['baselines'] = {row['identity']: row['baseline'] for row in self.data['uplinks'].values() if row.get('identity') and row.get('baseline')}

    def update(self, observations):
        for uid, o in observations.items():
            if o.error:
                self.data['partial'] = True
            if o.counters is None:
                continue
            row = self.data['uplinks'].setdefault(uid, {'rx_bytes': 0, 'tx_bytes': 0, 'baseline': None, 'identity': ''})
            # Baselines belong to a kernel interface lifetime, not the user-
            # editable uplink ID. Renaming an ID (and renaming it back) cannot
            # duplicate traffic already attributed to another configured ID.
            old = self.data['baselines'].get(o.counter_identity)
            if row['identity'] and row['identity'] != o.counter_identity:
                self.data['partial'] = True
            for key in ('rx_bytes', 'tx_bytes'):
                val = o.counters[key]
                if old is None:
                    delta = val
                elif val >= old[key]:
                    delta = val - old[key]
                else:
                    delta = val
                    self.data['partial'] = True
                row[key] += delta
            row.update(baseline=o.counters.copy(), identity=o.counter_identity)
            self.data['baselines'][o.counter_identity] = o.counters.copy()
        return self.summary()

    def summary(self):
        rows = {k: {n: v[n] for n in ('rx_bytes', 'tx_bytes')} for k, v in self.data['uplinks'].items()}
        for row in rows.values():
            row['total_bytes'] = row['rx_bytes'] + row['tx_bytes']
        total = {key: sum(r[key] for r in rows.values()) if rows else None for key in ('rx_bytes', 'tx_bytes', 'total_bytes')}
        return dict(total, per_uplink=rows, partial=self.data['partial'], scope='wan-interface', since='boot')


def probe(interface, targets, timeout):
    if not interface_name(interface):
        return None
    try:
        for target in targets:
            result = subprocess.run(['ping', '-n', '-4' if ipaddress.ip_address(target).version == 4 else '-6', '-I', interface, '-c', '1', '-W', str(timeout), target], capture_output=True, timeout=timeout + 1, check=False)
            if result.returncode == 0:
                return True
            if result.returncode not in (1,):
                return None
        return False
    except (OSError, subprocess.SubprocessError):
        return None


class NetworkManager:
    def __init__(self):
        import dbus  # Debian package python3-dbus; delayed for portable policy tests.
        self.dbus = dbus
        self.bus = dbus.SystemBus()
        self.owner = str(self.bus.get_name_owner(BUS))
        self.manager = self.iface(ROOT, BUS)

    def iface(self, path, interface):
        return self.dbus.Interface(self.bus.get_object(self.owner, path), interface)

    def prop(self, path, interface, name):
        return self.iface(path, PROPS).Get(interface, name, timeout=5)

    def daemon(self):
        return str(self.bus.get_name_owner(BUS))

    def prepare_probes(self, interface, auto):
        """Loose reverse-path validation is required on a multihomed standby.

        Change only configured WANs, and retain boot-local originals for manual
        restoration. LAN and global sysctls are never changed.
        """
        if not interface_name(interface):
            raise ValueError('protected probe interface')
        path = Path('/proc/sys/net/ipv4/conf') / interface / 'rp_filter'
        if not path.exists():
            return
        current = int(path.read_text())
        global_value = int(Path('/proc/sys/net/ipv4/conf/all/rp_filter').read_text())
        journal = RUNTIME / 'rp-filter.json'
        saved = read_json(journal, {})
        if auto and max(current, global_value) == 1:
            saved.setdefault(interface, current)
            atomic_json(journal, saved)
            path.write_text('2\n')
        elif not auto and interface in saved:
            path.write_text(str(saved.pop(interface)) + '\n')
            atomic_json(journal, saved)

    def observe(self, config):
        active = {}
        for path in self.prop(ROOT, BUS, 'ActiveConnections'):
            interface = BUS + '.Connection.Active'
            active[str(self.prop(path, interface, 'Uuid'))] = (str(path), list(self.prop(path, interface, 'Devices')))
        devices = []
        for path in self.manager.GetDevices(timeout=5):
            interface = BUS + '.Device'
            name = str(self.prop(path, interface, 'Interface'))
            kind = int(self.prop(path, interface, 'DeviceType'))
            mac = ''
            if kind in (1, 2):
                suffix = '.Wired' if kind == 1 else '.Wireless'
                mac = str(self.prop(path, interface + suffix, 'PermHwAddress')).lower()
            devices.append((str(path), name, kind, mac))
        result = {}
        used = set()
        for u in config.uplinks:
            expected_type = {'ethernet': 1, 'wifi': 2, 'cellular': 8}[u.type]
            matches = [d for d in devices if d[2] == expected_type and (not u.interface or d[1] == u.interface) and (not u.mac or d[3] == u.mac.lower()) and (u.type != 'cellular' or (u.profile in active and d[0] in active[u.profile][1]))]
            o = Observation()
            result[u.id] = o
            if not matches:
                continue
            if len(matches) != 1:
                o.error = 'ambiguous device identity'
                continue
            path, name, kind, mac = matches[0]
            if name == 'eth0' or path in used:
                o.error = 'protected or duplicate device'
                continue
            used.add(path)
            dev = BUS + '.Device'
            o.device = path
            o.interface = str(self.prop(path, dev, 'IpInterface')) or name
            if not interface_name(o.interface):
                o.error = 'protected or invalid data interface'
                continue
            state = int(self.prop(path, dev, 'State'))
            o.link = bool(self.prop(path, dev + '.Wired', 'Carrier')) if kind == 1 else state == 100
            for uuid, (session, paths) in active.items():
                if path in paths:
                    o.profile, o.session = uuid, session
            # Bound Ethernet and Wi-Fi identities observe the actual active
            # profile, including an operator-selected profile. The configured
            # UUID controls activation, not ownership of pre-existing sessions.
            for family in (4, 6):
                ip_path = str(self.prop(path, dev, f'Ip{family}Config'))
                if ip_path == '/':
                    continue
                addresses = self.prop(ip_path, BUS + f'.IP{family}Config', 'AddressData')
                ips = [str(a['address']) for a in addresses]
                o.addresses.extend(ips)
                usable = any(not ipaddress.ip_address(ip).is_link_local and not ipaddress.ip_address(ip).is_unspecified for ip in ips)
                if family == 4:
                    o.address = usable
                    if any(ipaddress.ip_address(ip) in ipaddress.ip_network('10.42.0.0/24') for ip in ips):
                        o.error = 'WAN address overlaps PCS LAN'
                else:
                    o.address6 = usable
            try:
                base = Path('/sys/class/net') / o.interface
                o.counters = {k: int((base / 'statistics' / k).read_text()) for k in ('rx_bytes', 'tx_bytes')}
                o.counter_identity = f'{mac}:{(base / "ifindex").read_text().strip()}'
            except (OSError, ValueError):
                o.counters = None
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            jobs = []
            for o in result.values():
                if o.error:
                    continue
                if o.interface:
                    self.prepare_probes(o.interface, config.mode == 'auto')
                if o.address and o.link:
                    jobs.append((o, 'internet', pool.submit(probe, o.interface, config.ipv4_targets, config.probe_timeout)))
                if o.address6 and o.link:
                    jobs.append((o, 'internet6', pool.submit(probe, o.interface, config.ipv6_targets, config.probe_timeout)))
            for o, key, future in jobs:
                setattr(o, key, future.result())
        return result

    def activate(self, u):
        # D-Bus activation returns the precise object created by this request.
        settings = self.iface(ROOT + '/Settings', BUS + '.Settings')
        path = settings.GetConnectionByUuid(u.profile, timeout=5)
        saved = self.iface(path, BUS + '.Settings.Connection').GetSettings(timeout=5)
        connection = saved.get('connection', {})
        expected = {'cellular': 'gsm', 'ethernet': '802-3-ethernet', 'wifi': '802-11-wireless'}[u.type]
        if str(connection.get('type')) != expected or connection.get('interface-name') == 'eth0' or saved.get('ipv4', {}).get('method') == 'shared':
            raise ValueError('refusing activation of an incompatible or LAN profile')
        device = '/'
        if u.type != 'cellular':
            if not u.interface:
                raise ValueError('automatic non-cellular activation requires an explicit interface')
            device = self.manager.GetDeviceByIpIface(u.interface, timeout=5)
            if not interface_name(str(self.prop(device, BUS + '.Device', 'Interface'))):
                raise ValueError('refusing protected activation device')
        return str(self.manager.ActivateConnection(path, device, '/', timeout=10))

    def deactivate(self, session):
        self.manager.DeactivateConnection(session, timeout=10)

    def renew_ipv4(self, u, o):
        """Retrigger DHCP on one exact Ethernet profile without disconnecting it."""
        if u.type != 'ethernet' or not u.profile or o.profile != u.profile or not o.session:
            raise ValueError('refusing to renew an unverified Ethernet profile')
        if not interface_name(o.interface) or o.interface == 'eth0':
            raise ValueError('refusing to renew a protected interface')
        current = str(self.prop(o.device, BUS + '.Device', 'ActiveConnection'))
        if current != o.session:
            raise RuntimeError('activation changed during Ethernet DHCP renewal')
        settings, _ = self.applied(o)
        if settings.get('connection', {}).get('type') != '802-3-ethernet':
            raise ValueError('refusing DHCP renewal on a non-Ethernet profile')
        if settings.get('ipv4', {}).get('method') != 'auto':
            raise ValueError('refusing DHCP renewal on a non-DHCP profile')
        metric = int(settings.get('ipv4', {}).get('route-metric', -1))
        # NetworkManager treats an identical Reapply as a no-op. A bounded
        # one-step runtime metric change makes it restart DHCP without changing
        # the saved profile or active-connection identity. The next controller
        # pass restores the policy metric.
        if not 1 < metric < 4_000_000_000 - 1:
            raise ValueError('Ethernet route metric leaves no safe DHCP-renewal step')
        self.reapply(o, {'ipv4': {'route-metric': metric + 1}})

    def applied(self, o):
        return self.iface(o.device, BUS + '.Device').GetAppliedConnection(0, timeout=5)

    def reapply(self, o, values):
        if not interface_name(o.interface) or o.interface == 'eth0':
            raise ValueError('refusing protected interface')
        current = str(self.prop(o.device, BUS + '.Device', 'ActiveConnection'))
        if current != o.session:
            raise RuntimeError('activation changed during route update')
        settings, version = self.applied(o)
        if settings.get('connection', {}).get('type') == 'gsm':
            raise ValueError('refusing modem reapply: preserve bearer IP configuration')
        if settings.get('ipv4', {}).get('method') == 'shared' or settings.get('connection', {}).get('interface-name') == 'eth0':
            raise ValueError('refusing to reapply a LAN sharing profile')
        for family, fields in values.items():
            for key, value in fields.items():
                if value is None:
                    settings[family].pop(key, None)
                else:
                    settings[family][key] = self.dbus.Int64(value) if key == 'route-metric' else self.dbus.Int32(value)
        self.iface(o.device, BUS + '.Device').Reapply(settings, version, 0, timeout=10)

    def fixed_metrics(self, o):
        """Read installed modem defaults; automatic NM metrics need not be 900."""
        result = {}
        for family, flag in [('ipv4', '-4'), ('ipv6', '-6')]:
            routes = subprocess.run(['ip', flag, '-j', 'route', 'show', 'default', 'dev', o.interface],
                                    capture_output=True, text=True, timeout=3, check=True)
            metrics = [int(row.get('metric', 0)) for row in json.loads(routes.stdout or '[]')]
            if metrics:
                result[family] = min(metrics)
        return result

    def effective(self, target='1.1.1.1'):
        family = '-4' if ipaddress.ip_address(target).version == 4 else '-6'
        result = subprocess.run(['ip', family, '-j', 'route', 'get', target], capture_output=True, text=True, timeout=3, check=False)
        rows = json.loads(result.stdout or '[]')
        return rows[0].get('dev', '') if rows else ''


class Controller:
    def __init__(self, config, nm, runtime=RUNTIME, boot=None):
        self.config, self.nm, self.runtime = config, nm, Path(runtime)
        self.boot = boot or Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        self.policy = Policy(config)
        self.policy6 = Policy(config)
        self.address_missing_at = {}
        self.address_retry_at = {}
        self.startup = True
        self.state = read_json(self.runtime / 'state.json', {})
        if self.state.get('boot') != self.boot:
            self.state = {'boot': self.boot, 'owned': {}, 'original': {}, 'suppressed': []}
        self.accounting = Accounting(read_json(self.runtime / 'usage.json'), self.boot)
        if not (self.runtime / 'usage.json').exists():
            # Initial counters cannot recover an interface removed before the
            # observer first ran. Be explicit about mid-boot installation.
            try:
                self.accounting.data['partial'] = float(Path('/proc/uptime').read_text().split()[0]) > 60
            except OSError:
                pass
        self.daemon = nm.daemon()
        if self.state.get('daemon') != self.daemon:
            self.state.update(owned={}, original={}, daemon=self.daemon)
        else:
            self.policy.selected = self.state.get('selected')
            self.policy6.selected = self.state.get('selected6')

    def save(self):
        atomic_json(self.runtime / 'state.json', self.state)
        atomic_json(self.runtime / 'usage.json', self.accounting.data)

    def owns(self, uid, o):
        return bool(o.session) and self.state['owned'].get(uid) == {'session': o.session, 'profile': o.profile}

    def restore(self, observations):
        for uid, saved in list(self.state['original'].items()):
            o = observations.get(uid)
            if any(u.id == uid and u.type == 'cellular' for u in self.config.uplinks):
                # Older controllers journaled modem Reapply changes. Restoring
                # those also discards the bearer address on affected NM versions.
                del self.state['original'][uid]
                continue
            if o and o.session == saved['session']:
                settings, _ = self.nm.applied(o)
                values = {family: {key: value for key, value in fields.items() if settings.get(family, {}).get(key) == saved.get('applied', {}).get(family, {}).get(key)} for family, fields in saved['values'].items()}
                if any(values.values()):
                    self.nm.reapply(o, {k: v for k, v in values.items() if v})
            del self.state['original'][uid]

    def recover_stalled_ethernet(self, observations, now):
        """Renew DHCP after a bound WAN keeps carrier but loses IPv4.

        NetworkManager normally retries DHCP itself. A dual-stack Ethernet
        profile can nevertheless remain activated on IPv6 after IPv4 DHCP has
        stalled. After a bounded grace period, reapply only the exact configured
        DHCP profile while another Internet path remains healthy. The active
        session is retained; eth0 and operator-selected profiles stay untouched.
        """
        for u in self.config.uplinks:
            o = observations[u.id]
            healthy_alternative = any(
                uid != u.id and candidate.internet
                for uid, candidate in observations.items()
            )
            waiting = (
                u.type == 'ethernet' and o.link and o.session
                and o.profile == u.profile and not o.address and not o.error
            )
            if not waiting:
                self.address_missing_at.pop(u.id, None)
                self.address_retry_at.pop(u.id, None)
                continue
            since = self.address_missing_at.setdefault(u.id, now)
            if (
                not healthy_alternative
                or now - since < ETHERNET_IPV4_RECOVERY_SECONDS
                or now < self.address_retry_at.get(u.id, 0)
            ):
                continue
            self.nm.renew_ipv4(u, o)
            self.address_missing_at[u.id] = now
            self.address_retry_at[u.id] = now + ETHERNET_IPV4_RETRY_SECONDS
            print(f'Renewed {u.name} DHCP after sustained carrier without IPv4', flush=True)
            return u.id
        return None

    def route(self, desired, observations, desired6=None):
        # Promote the replacement before demoting the old path. DHCP settings
        # and connected routes remain NetworkManager-owned throughout.
        fixed = {'ipv4': [], 'ipv6': []}
        for u in self.config.uplinks:
            o = observations[u.id]
            if u.type == 'cellular' and o.session and not o.error:
                for family, metric in self.nm.fixed_metrics(o).items():
                    if not 1 < metric < 4_000_000_000:
                        raise RuntimeError('cellular default metric leaves no safe WAN preference range')
                    fixed[family].append(metric)
        ordered = sorted(self.config.uplinks, key=lambda u: u.id != desired)
        for u in ordered:
            o = observations[u.id]
            if u.type == 'cellular':
                # NetworkManager 1.52 discards modem-provided addresses on
                # Reapply. Select around its existing routes without bouncing
                # or adopting the operator's bearer session.
                continue
            if not o.session or o.error or not (o.address or o.address6):
                continue
            settings, _ = self.nm.applied(o)
            original = self.state['original'].get(u.id)
            if not original or original['session'] != o.session:
                original = {'session': o.session, 'values': {family: {key: int(settings.get(family, {}).get(key, default)) for key, default in [('route-metric', -1), ('dns-priority', 0)]} for family in ('ipv4', 'ipv6') if family in settings}}
                self.state['original'][u.id] = original
                self.save()  # Journal before changing runtime policy.
            values = {}
            for family, health in [('ipv4', o.internet), ('ipv6', o.internet6)]:
                if family not in settings:
                    continue
                selected = u.id == (desired if family == 'ipv4' else desired6)
                anchors = fixed[family]
                preferred = min([50] + [metric - 1 for metric in anchors])
                standby = max([1000] + [metric + 1 for metric in anchors])
                failed = max(20000, standby)
                metric = preferred if selected else ((standby if health else failed) + u.priority)
                dns = original['values'][family]['dns-priority']
                # Preserve negative split-DNS/VPN policy; use positive ordering
                # otherwise, without excluding another connection's DNS zones.
                dns = dns if dns < 0 else (40 if selected else 1000 + u.priority)
                if int(settings[family].get('route-metric', -1)) != metric or int(settings[family].get('dns-priority', 0)) != dns:
                    values[family] = {'route-metric': metric, 'dns-priority': dns}
            if values:
                original.setdefault('applied', {}).update(values)
                self.save()
                self.nm.reapply(o, values)
        for selected, target in [(desired, self.config.ipv4_targets[0]), (desired6, self.config.ipv6_targets[0])]:
            if not selected:
                continue
            candidate = observations[selected]
            address = candidate.address if ipaddress.ip_address(target).version == 4 else candidate.address6
            if not candidate.session or not candidate.link or not address:
                # NetworkManager may remove a physically lost route before the
                # controller's failure window expires; this is not a policy-
                # routing conflict and does not authorize paid activation early.
                continue
            # Reapply may return before asynchronous kernel route updates have
            # settled. Verify within a fixed deadline rather than reporting a
            # false policy conflict during the legitimate transition.
            deadline = time.monotonic() + 3
            while self.nm.effective(target) != observations[selected].interface:
                if time.monotonic() >= deadline:
                    raise RuntimeError(f'effective IPv{ipaddress.ip_address(target).version} route does not match selected WAN (check VPN/policy routes)')
                time.sleep(0.1)

    def step(self, now=None):
        now = time.monotonic() if now is None else now
        if self.nm.daemon() != self.daemon:
            raise RuntimeError('NetworkManager restarted; restart controller to reconcile')
        obs = self.nm.observe(self.config)
        if self.startup and self.config.mode == 'auto':
            for policy, target, address_field in [(self.policy, self.config.ipv4_targets[0], 'address'), (self.policy6, self.config.ipv6_targets[0], 'address6')]:
                if policy.selected is None:
                    effective = self.nm.effective(target)
                    policy.selected = next((uid for uid, o in obs.items() if o.interface == effective and getattr(o, address_field) and not o.error), None)
            self.startup = False
        usage = self.accounting.update(obs)
        for uid in list(self.state['owned']):
            if uid not in obs or not self.owns(uid, obs[uid]):
                self.state['owned'].pop(uid, None)
        error = ''
        desired, activate = self.policy.choose(obs, now, self.state['suppressed'])
        obs6 = {uid: replace(o, internet=o.internet6) for uid, o in obs.items()}
        desired6, _ = self.policy6.choose(obs6, now, self.state['suppressed'])
        if desired and desired6:
            ranks = {u.id: u.priority for u in self.config.uplinks}
            if ranks[desired6] > ranks[desired] and self.owns(desired6, obs[desired6]):
                # Do not keep paid, automatically started cellular alive only
                # for IPv6 after a preferred IPv4 WAN has recovered.
                desired6 = next((u.id for u in self.config.uplinks if u.priority <= ranks[desired] and u.id in self.policy6.good and now - self.policy6.good[u.id] >= self.config.recovery_seconds), None)
        try:
            if self.config.mode == 'auto':
                if activate:
                    u = next(u for u in self.config.uplinks if u.id == activate)
                    self.policy.retry_at[u.id] = now + 60
                    # Recheck immediately; never adopt an existing activation.
                    fresh = self.nm.observe(replace(self.config, uplinks=(u,)))[u.id]
                    if not fresh.session and not fresh.error:
                        session = self.nm.activate(u)
                        self.state['owned'][u.id] = {'session': session, 'profile': u.profile}
                        self.save()
                self.route(desired, obs, desired6)
                changed = desired != self.policy.selected
                self.policy.selected = desired
                self.policy6.selected = desired6
                self.state.update(selected=desired, selected6=desired6)
                if desired:
                    preferred = next(u.priority for u in self.config.uplinks if u.id == desired)
                    for u in self.config.uplinks:
                        if u.priority > preferred and u.id != desired6 and self.owns(u.id, obs[u.id]):
                            self.nm.deactivate(obs[u.id].session)
                            self.state['owned'].pop(u.id, None)
                    if changed:
                        subprocess.run(['systemctl', '--no-block', 'start', 'pcs-direwolf-uplink-recovery.service'], capture_output=True, timeout=3, check=False)
                self.recover_stalled_ethernet(obs, now)
            else:
                self.restore(obs)
                self.state['owned'].clear()  # Manual mode leaves sessions alive.
                self.policy.selected = self.policy6.selected = None
                self.state.update(selected=None, selected6=None)
        except Exception as exc:
            error = str(exc)
        effective = self.nm.effective(self.config.ipv4_targets[0])
        active_id = next((u.id for u in self.config.uplinks if obs[u.id].interface == effective), None)
        rows = []
        for u in self.config.uplinks:
            o = obs[u.id]
            rows.append(dict(id=u.id, name=u.name, type=u.type, priority=u.priority, interface=o.interface, profile=o.profile or u.profile, addresses=o.addresses, link=o.link, address=o.address, address6=o.address6, internet=o.internet, internet6=o.internet6, state=o.state, active=u.id == active_id, selected=u.id == self.policy.selected, selected6=u.id == self.policy6.selected, owned=self.owns(u.id, o), suppressed=u.id in self.state['suppressed'], error=o.error, usage=usage['per_uplink'].get(u.id)))
        status = dict(version=1, boot=self.boot, generated_at=time.time(), mode=self.config.mode, active_id=active_id, selected_id=self.policy.selected, selected6_id=self.policy6.selected, internet=bool(active_id and obs[active_id].internet), uplinks=rows, usage=usage, error=error)
        self.save()
        atomic_json(self.runtime / 'status.json', status)
        return status


PUBLIC_UPLINK_FIELDS = ('id', 'name', 'type', 'priority', 'link', 'address', 'address6', 'internet', 'internet6', 'state', 'active', 'selected', 'selected6', 'usage')


def cached_status(path=RUNTIME / 'status.json', public=False, now=None):
    value = read_json(path, {})
    now = time.time() if now is None else now
    if not isinstance(value, dict) or not isinstance(value.get('generated_at'), (int, float)) or not 0 <= now - value['generated_at'] <= 90:
        return {'available': False, 'uplinks': [], 'usage': None}
    if public:
        return {'available': True, 'mode': value.get('mode'), 'internet': value.get('internet'), 'active_id': value.get('active_id'), 'uplinks': [{k: row.get(k) for k in PUBLIC_UPLINK_FIELDS} for row in value.get('uplinks', [])], 'usage': value.get('usage')}
    return dict(value, available=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=CONFIG)
    parser.add_argument('--runtime', type=Path, default=RUNTIME)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--cached', action='store_true')
    parser.add_argument('--public', action='store_true')
    parser.add_argument('--operator', choices=['connect', 'disconnect', 'resume'])
    parser.add_argument('--restore-runtime', action='store_true', help='Restore only journaled runtime settings; leave connections alive')
    parser.add_argument('--uplink', default='cellular')
    args = parser.parse_args()
    if args.cached:
        print(json.dumps(cached_status(args.runtime / 'status.json', args.public)))
        return 0
    config = load_config(args.config)
    if args.check:
        print(f'Uplink mode: {config.mode}; configured: ' + ', '.join(u.name for u in config.uplinks))
        status = cached_status(args.runtime / 'status.json')
        if not status['available']:
            print('WARN: uplink observer data unavailable or stale (LAN is independent)')
        for u in status.get('uplinks', []):
            print(f"  {u['name']}: {u['state']} / {'active' if u['active'] else 'standby'} / {u['interface'] or 'absent'} / {'owned' if u['owned'] else 'unowned'}")
        print('WAN bytes since boot: ' + json.dumps(status.get('usage')))
        if status.get('error'):
            print('WARN: ' + status['error'])
        return 0
    import fcntl
    if not args.operator and not args.restore_runtime:
        daemon_lock = open('/run/lock/pcs-uplink-manager.lock', 'a')
        try:
            fcntl.flock(daemon_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('An uplink controller is already running; use --check for status.')
            return 1
    nm = NetworkManager()
    while True:
        started = time.monotonic()
        with open(LOCK, 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if 'controller' not in locals():
                controller = Controller(config, nm, args.runtime)
            else:
                controller.state = read_json(args.runtime / 'state.json', controller.state)
            if args.restore_runtime:
                observations = nm.observe(replace(config, mode='manual'))
                controller.restore(observations)
                controller.state['owned'].clear()
                controller.save()
                return 0
            if args.operator:
                u = next(u for u in config.uplinks if u.id == args.uplink)
                if not u.profile:
                    raise ValueError('operator activation requires a configured profile')
                controller.state['owned'].pop(u.id, None)
                suppressed = set(controller.state['suppressed'])
                if args.operator == 'disconnect':
                    suppressed.add(u.id)
                else:
                    suppressed.discard(u.id)
                controller.state['suppressed'] = sorted(suppressed)
                controller.save()
                o = nm.observe(Config((u,)))[u.id]
                if args.operator == 'connect' and not o.session:
                    nm.activate(u)
                elif args.operator == 'disconnect' and o.session:
                    nm.deactivate(o.session)
                return 0
            status = controller.step()
            if status['error']:
                print(status['error'], flush=True)
        if args.once:
            return int(bool(status['error']))
        time.sleep(max(0.1, config.poll_seconds - (time.monotonic() - started)))


if __name__ == '__main__':
    raise SystemExit(main())
