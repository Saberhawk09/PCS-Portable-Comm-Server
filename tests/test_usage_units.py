"""Verify byte-to-decimal-MB conversion in both server presentation paths."""
from pathlib import Path
import unittest
import test_pcs_control_panel as control

ROOT = Path(__file__).resolve().parents[1]


class UsageUnitsTests(unittest.TestCase):
    def test_collector_summary_converts_bytes_not_just_label(self):
        source = (ROOT / 'scripts/pcs-web-action.sh').read_text(encoding='utf-8')
        function = source.split('def usage_label(usage):', 1)[1].split('\nnetwork_core_ok', 1)[0]
        namespace = {}
        exec('def usage_label(usage):' + function, namespace)
        label = namespace['usage_label']
        self.assertEqual(label({'rx_bytes': 0, 'tx_bytes': 1_000_000, 'total_bytes': 1_073_741_824, 'partial': True}),
                         'Down 0.000 MB / Up 1.000 MB / Total 1073.742 MB (partial)')
        self.assertEqual(label(None), 'Unavailable')
        self.assertIn('Total Unavailable', label({}))

    def test_public_uplink_card_uses_decimal_megabytes(self):
        page = control.pcs.render_public_page({'network': {'uplinks': [
            {'name': 'Test WAN', 'usage': {'rx_bytes': 0, 'tx_bytes': 1_000_000, 'total_bytes': 1_073_741_824}}
        ]}}).decode()
        self.assertIn('Down 0.000 MB / Up 1.000 MB / Total 1073.742 MB', page)
