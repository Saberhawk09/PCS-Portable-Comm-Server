"""Read-only FQ-302 NetworkManager ownership facts; never call observe/activate.

Private output is consumed by the scenario and is never exported verbatim.
The subprocess boundary bounds D-Bus reads independently from witness responses.
"""
import json
from pathlib import Path


def collect():
    import pcs_uplink_manager as production
    config = production.load_config()
    nm = production.NetworkManager()
    bus, root = production.BUS, production.ROOT
    cellular = [u for u in config.uplinks if u.type == 'cellular']
    if len(cellular) != 1:
        raise ValueError('cellular_identity_ambiguous')
    cell = cellular[0]
    settings = nm.iface(root + '/Settings', bus + '.Settings')
    profile = settings.GetConnectionByUuid(cell.profile, timeout=3)
    saved = nm.iface(profile, bus + '.Settings.Connection').GetSettings(timeout=3)
    if (saved.get('connection', {}).get('type') != 'gsm' or
            saved.get('connection', {}).get('interface-name') == 'eth0' or
            saved.get('ipv4', {}).get('method') == 'shared'):
        raise ValueError('cellular_profile_invalid')
    # GetSettings excludes secrets. Never return APN, subscriber, credentials,
    # phone numbers or saved profile settings to the parent/evidence writer.
    active = []
    for path in nm.prop(root, bus, 'ActiveConnections'):
        iface = bus + '.Connection.Active'
        if str(nm.prop(path, iface, 'Type')) != 'gsm':
            continue
        devices = list(nm.prop(path, iface, 'Devices'))
        if len(devices) != 1:
            raise ValueError('cellular_device_ambiguous')
        dev = devices[0]
        active.append(dict(session=str(path), profile=str(nm.prop(path, iface, 'Uuid')),
                           state=int(nm.prop(path, iface, 'State')),
                           interface=str(nm.prop(dev, bus + '.Device', 'IpInterface'))))
    state = production.read_json(production.RUNTIME / 'state.json')
    if not isinstance(state, dict):
        raise ValueError('ownership_state_unavailable')
    owner = nm.daemon()
    if state.get('daemon') != owner:
        raise ValueError('ownership_daemon_changed')
    # ModemManager object enumeration is observation only. No Connect method,
    # enable, scanning, bearer creation or profile mutation is available here.
    mm = 'org.freedesktop.ModemManager1'
    manager = nm.dbus.Interface(nm.bus.get_object(mm, '/org/freedesktop/ModemManager1'),
                               'org.freedesktop.DBus.ObjectManager')
    objects = manager.GetManagedObjects(timeout=3)
    modems = [(str(path), props[mm + '.Modem'], props.get(mm + '.Modem.Modem3gpp', {}))
              for path, props in objects.items() if mm + '.Modem' in props]
    if len(modems) != 1:
        raise ValueError('modem_identity_ambiguous')
    path, modem, registration = modems[0]
    bearers = []
    for bearer in modem['Bearers']:
        props = objects.get(bearer, {}).get(mm + '.Bearer')
        if props is None:
            props = nm.dbus.Interface(nm.bus.get_object(mm, bearer),
                'org.freedesktop.DBus.Properties').GetAll(mm + '.Bearer', timeout=3)
        bearers.append(bool(props['Connected']))
    return dict(boot=state.get('boot'), daemon=owner, owned=state.get('owned'),
                suppressed=state.get('suppressed'), active=active, cellular_id=cell.id,
                cellular_profile=cell.profile, activation=cell.activation,
                modem_identity=[path, str(modem['Device']), str(modem['PrimaryPort']),
                                str(modem['EquipmentIdentifier'])],
                modem_state=int(modem['State']), registration=int(registration['RegistrationState']),
                bearer_connected=any(bearers))


if __name__ == '__main__':
    print(json.dumps(collect(), allow_nan=False))
