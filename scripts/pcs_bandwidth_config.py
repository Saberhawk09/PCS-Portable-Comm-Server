"""Validated policy, independent of Linux enforcement and future UI/discovery."""
import copy
import ipaddress
import re


class PolicyError(ValueError):
    pass


def rate(value):
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)([kmg]?)(?:bit|bps)?", str(value).lower())
    if not match:
        raise PolicyError(f"Invalid rate: {value!r}; use e.g. 5mbit or 5M")
    result = int(float(match[1]) * {'': 1, 'k': 1000, 'm': 1000000, 'g': 1000000000}[match[2]])
    if not 1000 <= result <= 10000000000:
        raise PolicyError('Rates must be between 1kbit and 10gbit')
    return result


def interface(value):
    if value is not None and not re.fullmatch(r'[a-zA-Z0-9_.-]{1,15}', str(value)):
        raise PolicyError('Invalid interface name')
    return value


def keys(obj, allowed, context):
    if not isinstance(obj, dict) or set(obj) - set(allowed):
        raise PolicyError(f'Invalid/unknown fields in {context}')


def validate(raw):
    config = copy.deepcopy(raw)
    keys(config, ['version', 'profile', 'lan_network', 'lan_interface', 'wan_interface', 'profiles', 'clients'], 'configuration')
    if config.get('version', 1) != 1:
        raise PolicyError('Unsupported configuration version')
    network = ipaddress.ip_network(config.get('lan_network', '10.42.0.0/24'))
    if network.version != 4 or not 24 <= network.prefixlen <= 30:
        raise PolicyError('v1 supports one IPv4 /24 through /30 LAN')
    config['lan_network'] = str(network)
    for field in ('lan_interface', 'wan_interface'):
        config[field] = interface(config.get(field))
    profiles = config.get('profiles')
    if not isinstance(profiles, dict) or not profiles:
        raise PolicyError('profiles must be a nonempty mapping')
    for name, profile in profiles.items():
        if not isinstance(name, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,40}', name):
            raise PolicyError('Invalid profile name')
        keys(profile, ['enabled', 'total_down', 'total_up', 'default_down', 'default_up'], 'profile')
        if type(profile.get('enabled')) is not bool:
            raise PolicyError('Profile enabled must be a boolean')
        for field in ('total_down', 'total_up', 'default_down', 'default_up'):
            if field in profile:
                rate(profile[field])
        if profile['enabled'] and not all(k in profile for k in ('default_down', 'default_up')):
            raise PolicyError('Enabled profiles require default_down and default_up')
    if config.get('profile') not in profiles:
        raise PolicyError('Select an existing profile')
    clients = config.setdefault('clients', [])
    if not isinstance(clients, list):
        raise PolicyError('clients must be a list')
    seen = set()
    for client in clients:
        keys(client, ['ip', 'name', 'download', 'upload', 'unlimited'], 'client')
        address = ipaddress.ip_address(client.get('ip', ''))
        if address not in list(network.hosts()) or str(address) in seen:
            raise PolicyError('Client must be a unique usable LAN IPv4 address')
        client['ip'] = str(address)
        seen.add(str(address))
        if 'name' in client and (not isinstance(client['name'], str) or len(client['name']) > 100 or any(ord(c) < 32 for c in client['name'])):
            raise PolicyError('Invalid client name')
        if type(client.get('unlimited', False)) is not bool:
            raise PolicyError('unlimited must be a boolean')
        if client.get('unlimited') and ('download' in client or 'upload' in client):
            raise PolicyError('Unlimited clients cannot also specify rates')
        for field in ('download', 'upload'):
            if field in client:
                rate(client[field])
    return config


def load(path):
    import yaml
    # SafeLoader normally silently accepts duplicate keys; policies must not.
    class UniqueLoader(yaml.SafeLoader):
        pass

    def mapping(loader, node, deep=False):
        result = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=deep)
            if key in result:
                raise PolicyError(f'Duplicate YAML key: {key}')
            result[key] = loader.construct_object(value_node, deep=deep)
        return result

    UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
    with open(path, encoding='utf-8') as stream:
        return validate(yaml.load(stream, Loader=UniqueLoader))


def clients(config, router_addresses=()):
    """Preallocate separate defaults for every usable IP, never a shared cap."""
    profile = config['profiles'][config['profile']]
    overrides = {c['ip']: c for c in config['clients']}
    for index, ip in enumerate(ipaddress.ip_network(config['lan_network']).hosts(), 0x100):
        address = str(ip)
        if address in router_addresses:
            continue
        entry = overrides.get(address, {})
        yield dict(ip=address, minor=f'{index:x}', name=entry.get('name', address),
                   explicit=bool(entry), unlimited=entry.get('unlimited', False),
                   down=entry.get('download', profile.get('default_down')),
                   up=entry.get('upload', profile.get('default_up')))
