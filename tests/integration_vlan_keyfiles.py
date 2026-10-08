#!/usr/bin/env python3
"""Parse and verify staged keyfiles using libnm, without a running NM daemon."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from pcs_vlan_switch import profiles
import gi
gi.require_version('NM', '1.0')
from gi.repository import GLib, NM

for policy in ('auto', 'disabled', 'ignore'):
    for name, text in profiles(policy).items():
        keyfile = GLib.KeyFile.new()
        keyfile.load_from_data(text, len(text.encode()), GLib.KeyFileFlags.NONE)
        connection = NM.keyfile_read(keyfile, '/', NM.KeyfileHandlerFlags.NONE, None, None)
        assert connection.verify(), name
        assert not connection.get_setting_connection().get_autoconnect(), name
print('PASS: all staged parent/LAN/WAN keyfiles verified by libnm; no daemon or network changes')
