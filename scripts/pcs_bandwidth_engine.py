"""Owned, fail-open nft/tc resources. No routing or firewall verdict changes."""
import hashlib
import ipaddress
import json
import os
import re
from pathlib import Path
import subprocess

from pcs_bandwidth_config import PolicyError, clients, interface, rate

OWNER = 'pcs-bandwidth-v1'
TABLE = 'pcs_bandwidth'
PREF = '47801'
HANDLE = '0x1'
DIRECTIONS = {'up': ('pcsbw-up', 'b701'), 'down': ('pcsbw-down', 'b702')}


def run(args, *, data=None, check=True):
    result = subprocess.run(args, input=data, text=True, capture_output=True, timeout=30)
    if check and result.returncode:
        raise RuntimeError(f'{args[0]} failed: {result.stderr.strip()}')
    return result


def read_json(args):
    output = run(args).stdout
    try:
        return json.loads(output)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f'Invalid JSON from {" ".join(args)}: {output[:800]}') from exc


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_suffix('.new')
    with open(temporary, 'w', encoding='utf-8') as stream:
        json.dump(value, stream, sort_keys=True, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def table_fingerprint(table):
    def stable(value):
        if isinstance(value, dict):
            return {k: stable(v) for k, v in value.items() if k not in ('metainfo', 'handle', 'packets', 'bytes')}
        if isinstance(value, list):
            return [stable(v) for v in value if not isinstance(v, dict) or 'metainfo' not in v]
        return value
    return hashlib.sha256(json.dumps(stable(table), sort_keys=True).encode()).hexdigest()


def detect(config, addresses, routes, rules):
    network = ipaddress.ip_network(config['lan_network'])
    candidates = [a for a in addresses if any(i.get('family') == 'inet' and
                  ipaddress.ip_address(i['local']) in network for i in a.get('addr_info', []))]
    if config['lan_interface']:
        candidates = [a for a in candidates if a['ifname'] == config['lan_interface']]
    if len(candidates) != 1:
        raise PolicyError('LAN detection ambiguous or unavailable; configure lan_interface')
    lan = candidates[0]['ifname']
    own = [a['local'] for a in candidates[0]['addr_info'] if a.get('family') == 'inet']
    if not config['wan_interface'] and any(r.get('table') not in ('local', 'main', 'default', 253, 254, 255) for r in rules):
        raise PolicyError('Policy routing detected; explicitly configure wan_interface')
    defaults = [r for r in routes if r.get('dst') == 'default' and r.get('type', 'unicast') == 'unicast']
    if any(r.get('nexthops') for r in defaults) and not config['wan_interface']:
        raise PolicyError('Multipath WAN requires explicit wan_interface')
    if config['wan_interface']:
        defaults = [r for r in defaults if r.get('dev') == config['wan_interface']]
    if not defaults:
        return dict(lan=lan, wan=None, router_addresses=own)
    best = min(r.get('metric', 0) for r in defaults)
    names = {r.get('dev') for r in defaults if r.get('metric', 0) == best}
    if len(names) != 1 or None in names:
        raise PolicyError('Equal-cost WAN routes ambiguous; configure wan_interface')
    wan = names.pop()
    if wan == lan or wan in ('lo', 'pcsbw-up', 'pcsbw-down'):
        raise PolicyError('WAN must be separate from LAN and manager IFBs')
    return dict(lan=interface(lan), wan=interface(wan), router_addresses=own)


def topology(config):
    return detect(config, read_json(['ip', '-j', '-4', 'address']),
                  read_json(['ip', '-j', '-4', 'route', 'show', 'table', 'main']),
                  read_json(['ip', '-j', '-4', 'rule']))


def plan(config, network):
    profile = config['profiles'][config['profile']]
    entries = list(clients(config, network['router_addresses']))
    if not profile['enabled'] or not network['wan']:
        return dict(enabled=False, network=network, clients=entries, tc=[], nft='')
    commands = []
    rules = [f'table inet {TABLE} {{', f'comment "{OWNER}"']
    for entry in entries:
        for direction in DIRECTIONS:
            rules.append(f'counter {direction}_{entry["minor"]} {{ }}')
    rules += ['chain forward {', 'type filter hook forward priority 100; policy accept;']
    for direction, (device, major) in DIRECTIONS.items():
        total = rate(profile.get(f'total_{direction}', '10gbit'))
        if total < len(entries) * 8:
            raise PolicyError('Aggregate ceiling is too small to allocate one byte/second per LAN address')
        commands += [f'qdisc add dev {device} root handle {major}: htb default 0',
                     f'class add dev {device} parent {major}: classid {major}:1 htb rate {total}bit ceil {total}bit']
        for entry in entries:
            ceiling = total if entry['unlimited'] else min(total, rate(entry[direction]))
            guarantee = max(8, min(ceiling, total // len(entries)))
            classid = f'{major}:{entry["minor"]}'
            commands += [f'class add dev {device} parent {major}:1 classid {classid} htb rate {guarantee}bit ceil {ceiling}bit quantum 1514',
                         f'qdisc add dev {device} parent {classid} fq_codel']
            src, dst = (network['lan'], network['wan']) if direction == 'up' else (network['wan'], network['lan'])
            field = 'saddr' if direction == 'up' else 'daddr'
            other = 'daddr' if direction == 'up' else 'saddr'
            rules.append(f'iifname "{src}" oifname "{dst}" ip {field} {entry["ip"]} ip {other} != {config["lan_network"]} counter name {direction}_{entry["minor"]} meta priority set "{classid}"')
    rules += ['}', '}']
    return dict(enabled=True, network=network, clients=entries, tc=commands, nft='\n'.join(rules)+'\n')


def redirect(dev, direction, operation='add'):
    target, major = DIRECTIONS[direction]
    args = ['tc', 'filter', operation, 'dev', dev, 'egress', 'protocol', 'ip', 'pref', PREF, 'handle', HANDLE, 'basic']
    if operation == 'add':
        args += ['match', f'meta(priority mask 0xffff0000 eq 0x{major}0000)',
                 'action', 'mirred', 'egress', 'redirect', 'dev', target]
    return args


class Engine:
    def __init__(self, state_dir):
        self.path = Path(state_dir) / 'state.json'

    def state(self):
        return json.loads(self.path.read_text()) if self.path.exists() else {}

    def save(self, state):
        atomic_json(self.path, state)

    def table(self):
        tables = read_json(['nft', '-j', 'list', 'tables'])['nftables']
        if any(t.get('table', {}).get('name') == TABLE and t['table'].get('family') == 'inet' for t in tables):
            return read_json(['nft', '-j', 'list', 'table', 'inet', TABLE])
        return None

    def owned_table(self, table):
        return any(t.get('table', {}).get('comment') == OWNER for t in table['nftables'])

    def links(self):
        return {d['ifname']: d for d in read_json(['ip', '-j', '-d', 'link', 'show'])}

    def filters(self, dev):
        output = run(['tc', '-j', 'filter', 'show', 'dev', dev, 'egress']).stdout
        # iproute2 6.15 emits the basic ematch as raw text even with -j.
        # Convert only this known field; never guess at other malformed output.
        output = re.sub(r'("handle":"[^"]+")\s*\n\s*(meta\([^\n]+\))\s*\n,',
                        lambda m: m[1]+',"ematch":'+json.dumps(m[2])+',', output)
        return json.loads(output)

    def own_filter(self, item, direction):
        options = item.get('options', {})
        return (str(item.get('pref')) == PREF and item.get('kind') == 'basic' and
                str(options.get('handle')) in ('1', '0x1') and
                any(a.get('kind') == 'mirred' and a.get('to_dev') == DIRECTIONS[direction][0]
                    for a in options.get('actions', [])))

    def cleanup(self):
        state = self.state()
        table = self.table()
        if table:
            if not self.owned_table(table):
                raise RuntimeError('Foreign pcs_bandwidth table; refusing deletion')
            run(['nft', 'delete', 'table', 'inet', TABLE])
        # Remove redirects before IFBs; never leave a redirect to a missing IFB.
        links = self.links()
        def owned_link(name):
            link = links[name]
            return (link.get('linkinfo', {}).get('info_kind') == 'ifb' and
                    (link.get('ifalias') == OWNER or
                     state.get('created_ifbs', {}).get(name) == link.get('ifindex')))

        for name, _ in DIRECTIONS.values():
            if name in links and not owned_link(name):
                raise RuntimeError(f'Foreign interface {name}; refusing deletion')
        attachments = dict(state.get('attachments', {}))
        # Recover our exact redirects even if the runtime journal was lost.
        for dev in links:
            for item in self.filters(dev):
                for direction in DIRECTIONS:
                    if self.own_filter(item, direction):
                        run(redirect(dev, direction, 'del'))
        for direction, dev in attachments.items():
            if dev not in links:
                continue
            for item in self.filters(dev):
                if self.own_filter(item, direction):
                    run(redirect(dev, direction, 'del'))
            if any(str(f.get('pref')) == PREF and f.get('options') for f in self.filters(dev)):
                raise RuntimeError('Foreign filter in reserved slot; cleanup needs inspection')
        for direction, (name, _) in DIRECTIONS.items():
            if name in links:
                link = links[name]
                if not owned_link(name):
                    raise RuntimeError(f'Foreign interface {name}; refusing deletion')
                run(['ip', 'link', 'delete', 'dev', name])
        for dev in state.get('created_clsact', []):
            if dev in links:
                qdiscs = read_json(['tc', '-j', 'qdisc', 'show', 'dev', dev])
                if any(q.get('kind') == 'clsact' for q in qdiscs):
                    ingress = read_json(['tc', '-j', 'filter', 'show', 'dev', dev, 'ingress'])
                    if not ingress and not self.filters(dev):
                        run(['tc', 'qdisc', 'del', 'dev', dev, 'clsact'])
        self.save({'status': 'INACTIVE'})

    def health(self, state=None):
        state = state or self.state()
        if state.get('status') != 'ACTIVE':
            return [state.get('error', state.get('status'))] if state.get('status') in ('ERROR', 'APPLYING') else []
        errors = []
        table = self.table()
        if not table or not self.owned_table(table):
            errors.append('Accounting/classification table missing or foreign')
        elif state.get('table_fingerprint') != table_fingerprint(table):
            errors.append('Accounting/classification rules changed')
        links = self.links()
        for direction, (device, major) in DIRECTIONS.items():
            link = links.get(device, {})
            if link.get('ifalias') != OWNER or 'UP' not in link.get('flags', []):
                errors.append(f'{device} missing, down or foreign')
                continue
            qdiscs = read_json(['tc', '-j', 'qdisc', 'show', 'dev', device])
            classes = read_json(['tc', '-j', 'class', 'show', 'dev', device])
            if not any(q.get('kind') == 'htb' and q.get('handle') == major+':' for q in qdiscs):
                errors.append(f'{device} HTB missing')
            expected = {f'{major}:{c["minor"]}' for c in state['clients']}
            if not expected.issubset({c.get('handle') for c in classes}):
                errors.append(f'{device} client classes missing')
            limits = state.get('limits', {})
            total = rate(limits.get(f'total_{direction}', '10gbit'))
            class_map = {c.get('handle'): c for c in classes}
            parent = class_map.get(major+':1', {})
            if parent.get('rate') != total // 8 or parent.get('ceil') != total // 8:
                errors.append(f'{device} aggregate rate changed')
            for entry in state['clients']:
                ceiling = total if entry['unlimited'] else min(total, rate(entry[direction]))
                guarantee = max(8, min(ceiling, total // len(state['clients'])))
                actual = class_map.get(f'{major}:{entry["minor"]}', {})
                if actual.get('rate') != guarantee // 8 or actual.get('ceil') != ceiling // 8:
                    errors.append(f'{device} client rate changed')
                    break
            if not expected.issubset({q.get('parent') for q in qdiscs if q.get('kind') == 'fq_codel'}):
                errors.append(f'{device} fair queues missing')
            dev = state['attachments'][direction]
            if dev not in links or not any(self.own_filter(f, direction) for f in self.filters(dev)):
                errors.append(f'{direction} redirect missing')
            elif any(f.get('options', {}).get('ematch') not in
                     (None, f'meta(priority mask 0xffff0000 eq {int(major, 16)*65536 - 2**32})',
                      f'meta(priority mask 0xffff0000 eq 0x{major}0000)')
                     for f in self.filters(dev) if self.own_filter(f, direction)):
                errors.append(f'{direction} redirect match changed')
        return errors

    def apply(self, config):
        try:
            network = topology(config)
        except PolicyError:
            if config['profiles'][config['profile']]['enabled']:
                raise
            network = dict(lan=config['lan_interface'], wan=None, router_addresses=[])
        candidate = plan(config, network)
        if candidate['enabled'] and any(c['ip'] in network['router_addresses'] for c in config['clients']):
            raise PolicyError('A client override cannot target a PCS router address')
        fingerprint = hashlib.sha256(json.dumps([config, network], sort_keys=True).encode()).hexdigest()
        old = self.state()
        if old.get('fingerprint') == fingerprint and not self.health(old):
            return old
        self.cleanup()
        if not candidate['enabled']:
            result = dict(status='DISABLED' if not config['profiles'][config['profile']]['enabled'] else 'WAITING_FOR_WAN',
                          profile=config['profile'], network=network)
            self.save(result)
            return result
        state = dict(status='APPLYING', fingerprint=fingerprint, profile=config['profile'],
                     network=network, clients=candidate['clients'], limits=config['profiles'][config['profile']], created_clsact=[], created_ifbs={},
                     attachments={'up': network['wan'], 'down': network['lan']})
        if any('flowtable' in item for item in read_json(['nft', '-j', 'list', 'ruleset'])['nftables']):
            raise PolicyError('nft flowtable offload detected; v1 cannot enforce traffic that bypasses forwarding hooks')
        # Check coexistence before creating anything. Reserved filter preference is exclusive.
        for dev in state['attachments'].values():
            qdiscs = read_json(['tc', '-j', 'qdisc', 'show', 'dev', dev])
            if any(q.get('kind') == 'ingress' for q in qdiscs):
                raise RuntimeError(f'{dev} has ingress qdisc; refusing replacement')
            if any(str(f.get('pref')) == PREF for f in self.filters(dev)):
                raise RuntimeError(f'{dev} filter preference {PREF} already used')
        self.save(state)
        try:
            for device, _ in DIRECTIONS.values():
                run(['ip', 'link', 'add', 'name', device, 'type', 'ifb'])
                state['created_ifbs'][device] = self.links()[device]['ifindex']
                self.save(state)
                run(['ip', 'link', 'set', 'dev', device, 'alias', OWNER])
                run(['ip', 'link', 'set', 'dev', device, 'up'])
            run(['tc', '-batch', '-'], data='\n'.join(candidate['tc'])+'\n')
            # nft check and commit both occur before redirects start carrying traffic.
            run(['nft', '--check', '-f', '-'], data=candidate['nft'])
            run(['nft', '-f', '-'], data=candidate['nft'])
            state['table_fingerprint'] = table_fingerprint(self.table())
            for direction, dev in state['attachments'].items():
                qdiscs = read_json(['tc', '-j', 'qdisc', 'show', 'dev', dev])
                if not any(q.get('kind') == 'clsact' for q in qdiscs):
                    state['created_clsact'].append(dev)
                    self.save(state)
                    run(['tc', 'qdisc', 'add', 'dev', dev, 'clsact'])
                run(redirect(dev, direction))
            state['status'] = 'ACTIVE'
            self.save(state)
            errors = self.health(state)
            if errors:
                raise RuntimeError('; '.join(errors))
            return state
        except Exception:
            self.cleanup()
            raise

    def status(self, config):
        state = self.state()
        try:
            errors = self.health(state)
            table = self.table()
            counters = {item['counter']['name']: item['counter'] for item in (table or {}).get('nftables', []) if 'counter' in item}
            try:
                network = topology(config)
            except PolicyError:
                if config['profiles'][config['profile']]['enabled']:
                    raise
                network = dict(lan=config['lan_interface'], wan=None, router_addresses=[])
            if state.get('status') == 'ACTIVE' and state.get('network') != network:
                errors.append('Active WAN/LAN changed; apply required')
            if state.get('status') == 'ACTIVE':
                fingerprint = hashlib.sha256(json.dumps([config, network], sort_keys=True).encode()).hexdigest()
                if fingerprint != state.get('fingerprint'):
                    errors.append('Configuration changed; apply required')
            ipv6 = read_json(['ip', '-j', '-6', 'address'])
        except (RuntimeError, PolicyError) as exc:
            errors, counters, network, ipv6 = [str(exc)], {}, state.get('network', {}), []
        rows = []
        for entry in clients(config, network.get('router_addresses', [])):
            row = dict(entry)
            for direction in DIRECTIONS:
                counter = counters.get(f'{direction}_{entry["minor"]}', {})
                row[direction+'_bytes'] = counter.get('bytes')
                row[direction+'_packets'] = counter.get('packets')
            if row['explicit'] or row['up_bytes'] or row['down_bytes']:
                rows.append(row)
        return dict(schema_version=1, status=state.get('status', 'INACTIVE'), healthy=not errors,
                    issues=errors, profile=config['profile'], limits=config['profiles'][config['profile']],
                    network=network, ifbs=list(d[0] for d in DIRECTIONS.values()) if state.get('status') == 'ACTIVE' else [],
                    ipv6={'shaped': False, 'warning': 'IPv6 traffic is not shaped or accounted in v1',
                          'global_addresses_present': any(a.get('scope') == 'global' for d in ipv6 for a in d.get('addr_info', []))},
                    clients=rows)
