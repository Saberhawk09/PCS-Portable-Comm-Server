import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


class WANDisplayNames(unittest.TestCase):
    def test_collector_names_require_available_telemetry_on_matching_uplink(self):
        source = (Path(__file__).resolve().parents[1] / 'scripts/pcs-web-action.sh').read_text()
        block = source.split('try:\n    from pcs_starlink import cached_status as starlink_cached', 1)[1].split('\nactive_uplink_label =', 1)[0]
        code = 'try:\n    from pcs_starlink import cached_status as starlink_cached' + block
        for available in (False, True):
            fake = types.ModuleType('pcs_starlink')
            fake.cached_status = lambda: {'configured': True, 'available': available}
            fake.load_config = lambda: {'uplink_id': 'satellite'}
            rows = [{'id': 'satellite', 'type': 'ethernet', 'name': 'Configured name'},
                    {'id': 'other', 'type': 'ethernet', 'name': 'Other Ethernet'},
                    {'id': 'wifi', 'type': 'wifi', 'name': 'Wi-Fi'}]
            with patch.dict(sys.modules, pcs_starlink=fake):
                exec(code, {'uplink_snapshot': {'uplinks': rows}})
            self.assertEqual([u['name'] for u in rows], ['Starlink' if available else 'Ethernet WAN', 'Ethernet WAN', 'Wi-Fi'])
