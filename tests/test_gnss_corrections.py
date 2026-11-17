"""F10 SPG 6.00 correction fixtures and server-rendered dashboard contracts."""
import copy
import importlib.util
from pathlib import Path
import struct
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import pcs_gnss_diagnostics as diag
import pcs_gnss_info as info

spec = importlib.util.spec_from_file_location('gnss_panel', ROOT / 'web/pcs-control-panel/pcs_control_panel.py')
panel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(panel)


def pvt(differential=True, fix=3, valid=True, itow=123000):
    raw = bytearray(92)
    struct.pack_into('<I', raw, 0, itow)
    raw[11], raw[20], raw[21] = 7, fix, int(valid) | (2 if differential else 0)
    struct.pack_into('<I', raw, 12, 15)
    struct.pack_into('<iiII', raw, 32, 100000, 70000, 750, 1200)
    return raw


def sbas(system=0, services=7, itow=123000, mode=1):
    return struct.pack('<IBBbBBB2x', itow, 133, mode, system, services, 1, 2) + struct.pack('<BBBBBBh2xh', 4, 0, 2, system & 255, services, 0, -123, 45)


def fixture():
    """Explicit synthetic receiver fixture, never used by the production collector."""
    diagnostics = diag.Diagnostics(clock=lambda: 100, wall=lambda: 1794887200)
    diagnostics.update(1, 7, pvt())
    diagnostics.update(1, 3, struct.pack('<IBBBBII', 123000, 3, 3, 1, 0, 12000, 300000))
    diagnostics.update(1, 0x32, sbas())
    signals, satellites = [], []
    for gnss, signal_ids in ((0, (0, 6)), (2, (0, 3)), (3, (5, 7))):
        for sv in range(1, 9):
            used = sv <= 6
            for sig in signal_ids:
                signals.append(struct.pack('<BBBBhBBBBH4x', gnss, sv, sig, 0, -2, 30 + sv + sig, 5, 1 if gnss == 0 else 0, 2, 1 | (8 | 64 if used and gnss == 0 else 8 if used else 0)))
            satellites.append(struct.pack('<BBBbhhI', gnss, sv, 40, 10 + sv * 8, (sv * 43 + gnss * 17) % 360, -2, 5 | (8 if used else 0) | (1 << 4) | ((1 << 16) if used and gnss == 0 else 0)))
    diagnostics.update(1, 0x43, struct.pack('<IBB2x', 123000, 0, len(signals)) + b''.join(signals))
    diagnostics.update(1, 0x35, struct.pack('<IBB2x', 123000, 1, len(satellites)) + b''.join(satellites))
    diagnostics.update_sky({'satellites': diagnostics.values['signals']['rows']})
    value = diagnostics.snapshot()
    value.update(state='Fresh', updated_at=1794887200, corrections=diag.correction_summary(value['messages']),
        receiver={'model': 'NEO-F10N', 'firmware': 'EXT SPGL1L5 6.00', 'hardware': '000A0000', 'protocol': '40.00'},
        serial={'device': '/dev/pcs-gnss-neo', 'baud': 115200, 'owner': 'GPSD (read-only)'},
        timing={'state': 'Fresh', 'synchronized': True, 'selected_ref': 'F10P', 'utc_valid': True, 'pps_present': True, 'pps_active': True, 'pps_selection': '*', 'offset_seconds': .00000023},
        chrony_sources={'F10P': {'state': 'Fresh', 'measured_offset_seconds': -.00000016}},
        assistance={'predictive': {'state': 'Available', 'downloaded_at': 1794887200, 'expires_at': 1795478400, 'days': ['2026-11-' + str(d) for d in range(17, 24)], 'constellations': ['GPS', 'Galileo']},
            'live_state': 'Disabled', 'live_entitlement': 'Available', 'settings': {'automatic_refresh': True, 'window_days': 7}, 'usage': {'next_refresh': 1795146405, 'last_success': 1794887200, 'last_uplink': 'Wi-Fi', 'predictive_requests': 1, 'live_requests': 0, 'downloaded_bytes_day': 35280}},
        capture={'active': False, 'remaining_seconds': 0, 'frame_count': 0}, events=[])
    paths = []
    for n, center in enumerate((1575420000, 1176450000)):
        paths.append(bytes([65 + (b * 7 % 31) for b in range(256)]) + struct.pack('<IIIB3x', 25600000, 100000, center, 12))
    diagnostics.update(10, 0x31, bytes((0, 2, 0, 0)) + b''.join(paths))
    diagnostics.update(0x27, 9, bytes((1, 0, 0, 0, 3, 0, 0, 0, 3, 0, 0, 0)))
    rf = bytearray(52)
    rf[1], rf[4], rf[28] = 2, 0, 1
    struct.pack_into('<HH', rf, 16, 100, 4000)
    struct.pack_into('<HH', rf, 40, 110, 4500)
    diagnostics.update(10, 0x38, rf)
    for name in ('spectrum', 'security', 'rf'):
        value['messages'][name] = diagnostics.snapshot()['messages'][name]
    return value


class CorrectionTests(unittest.TestCase):
    def test_sbas_single_sv_ids_include_galileo_and_beidou_not_just_gps(self):
        signal = struct.pack('<IBB2x', 123000, 0, 1) + struct.pack('<BBBBhBBBBH4x', 1, 131, 0, 0, 0, 40, 5, 1, 2, 9)
        signal_row = diag.decode(1, 0x43, signal)['rows'][0]
        self.assertEqual((signal_row['constellation'], signal_row['signal'], signal_row['band']), ('SBAS', 'L1 C/A', 'L1'))
        for raw, gnss, sv in ((4, 0, 4), (56, 3, 29), (240, 2, 30), (214, 2, 4),
                              (159, 3, 1), (131, 1, 131), (193, 5, 1), (65, 6, 1), (247, 7, 1), (255, 6, 255)):
            value = bytearray(sbas())
            value[12] = raw
            row = diag.decode(1, 0x32, value)['rows'][0]
            self.assertEqual((row['gnssid'], row['svid'], row['raw_svid']), (gnss, sv, raw))
        self.assertEqual(diag.single_satellite_id(100)['constellation'], 'Unknown')

    def test_standard_and_differential_fixes_and_invalid_fix(self):
        for fix in (2, 3):
            for differential in (False, True):
                nav = diag.decode(1, 7, pvt(differential, fix))
                self.assertEqual(nav['fix_type'], f'{fix}D')
                self.assertEqual(nav['differential_applied'], differential)
        self.assertFalse(diag.decode(1, 7, pvt(True, valid=False))['differential_applied'])

    def test_availability_does_not_mean_applied(self):
        value = diag.decode(1, 3, struct.pack('<IBBBBII', 123000, 3, 1, 1, 0, 1, 2))
        self.assertTrue(value['corrections_available'])
        self.assertFalse(value['differential_applied'])

    def test_exact_sbas_system_services_and_signed_scales(self):
        for code, name in ((-1, 'Unknown'), (0, 'WAAS'), (1, 'EGNOS'), (2, 'MSAS'), (3, 'GAGAN'), (16, 'GPS'), (42, 'Unknown')):
            value = diag.decode(1, 0x32, sbas(code))
            self.assertEqual(value['system'], name)
            self.assertEqual(value['rows'][0]['system'], name)
            self.assertEqual(value['services'], ['Ranging', 'Corrections', 'Integrity'])
            self.assertEqual(value['geo_prn'], 133)
            self.assertAlmostEqual(value['rows'][0]['pseudorange_correction_m'], -1.23)
            self.assertAlmostEqual(value['rows'][0]['ionosphere_correction_m'], .45)

    def test_malformed_sbas_rejected_and_prior_measurement_ages(self):
        clock = [0]
        receiver = diag.Diagnostics(clock=lambda: clock[0])
        receiver.update(1, 0x32, sbas())
        for raw in (b'', sbas()[:-1], sbas() + b'\0', sbas()[:8] + bytes((255,)) + sbas()[9:]):
            self.assertIsNone(receiver.update(1, 0x32, raw))
        clock[0] = 6
        self.assertEqual(receiver.snapshot()['messages']['sbas']['state'], 'Stale')
        self.assertEqual(receiver.rejected, 4)

    def test_actual_source_and_per_signal_application(self):
        value = fixture()
        self.assertEqual(value['corrections']['source'], 'WAAS')
        self.assertTrue(value['corrections']['applied'])
        self.assertEqual(value['counts']['visible'], 24)
        self.assertEqual(value['counts']['used'], 18)
        self.assertEqual(len(value['messages']['signals']['data']['rows']), 48)
        self.assertTrue(value['messages']['satellites']['data']['rows'][0]['sbas_correction_used'])
        self.assertTrue(value['messages']['signals']['data']['rows'][0]['pseudorange_correction_used'])

    def test_missing_stale_or_unrelated_sbas_never_identified_as_waas(self):
        for alteration in ('missing', 'stale', 'epoch'):
            value = fixture()['messages']
            if alteration == 'missing':
                del value['sbas']
            elif alteration == 'stale':
                value['sbas']['state'] = 'Stale'
            else:
                value['sbas']['data']['itow_ms'] += 3000
            result = diag.correction_summary(value)
            self.assertEqual(result['source'], 'SBAS (Unknown system)')
            self.assertEqual(result['reported_system'], 'Unknown')
        value['navigation']['state'] = 'Stale'
        self.assertIsNone(diag.correction_summary(value)['applied'])
        self.assertEqual(diag.correction_summary(value)['source'], 'Unknown')

    def test_reception_alone_and_orbit_assistance_are_not_correction_source(self):
        messages = fixture()['messages']
        for name in ('signals', 'satellites'):
            messages[name]['data']['rows'] = []
        self.assertEqual(diag.correction_summary(messages)['source'], 'Unknown')
        messages['navigation']['data']['differential_applied'] = False
        self.assertEqual(diag.correction_summary(messages)['source'], 'None applied')

    def test_sbas_integrity_warnings_and_week_wrap(self):
        messages = fixture()['messages']
        messages['sbas']['data'] = diag.decode(1, 0x32, sbas(services=31, itow=604799000, mode=3))
        messages['navigation']['data']['itow_ms'] = 0
        summary = diag.correction_summary(messages)
        self.assertIn('Warning', summary['health'])
        self.assertEqual(summary['selected_prn'], 133)
        self.assertEqual(summary['correction_age'], 'Not reported by NAV-SBAS')

    def test_collector_snapshot_expiry_recomputes_correction_state(self):
        value = fixture()
        value['updated_monotonic'] = 0
        with mock.patch.object(info.assist, 'read_json', return_value=value), mock.patch.object(info.time, 'monotonic', return_value=100):
            stale = info.read_snapshot(Path('unused'))
        summary = diag.correction_summary(stale['messages'])
        self.assertEqual(summary['state'], 'Stale')
        self.assertIsNone(summary['applied'])


class DashboardTests(unittest.TestCase):
    def test_geometry_join_rejects_missing_or_invalid_epochs_and_handles_week_wrap(self):
        for a,b in ((None, None), (0, None), (-1, 0), (604800001, 1), (0, 3000)):
            self.assertFalse(panel.gnss_epochs_match(a,b))
        self.assertTrue(panel.gnss_epochs_match(0,604799000))

    def test_overview_compact_and_other_sections_lazy(self):
        value = fixture()
        value['timing']['state'] = 'fresh'  # Authoritative v2.4.0 gate uses lowercase.
        with mock.patch.object(panel, 'gnss_plot', side_effect=AssertionError('Hidden plot rendered')):
            page = panel.render_gnss_page(value, 'token').decode()
        self.assertEqual(page.count('class="card gnss-metric"'), 6)
        self.assertIn('3D DGPS', page)
        self.assertIn('WAAS', page)
        self.assertIn('<strong>F10P</strong>', page)
        self.assertNotIn('Start Spectrum Capture', page)
        self.assertNotIn('<svg', page)
        for label in panel.GNSS_SECTIONS.values():
            self.assertIn(label, page)

    def test_each_section_preserves_controls_and_active_navigation(self):
        sections = {name: panel.render_gnss_page(fixture(), 'token', section=name).decode() for name in panel.GNSS_SECTIONS}
        for name, page in sections.items():
            self.assertEqual(page.count(' aria-current=page>'), 1)
            self.assertIn(f'href="?section={name}" aria-current=page', page)
        self.assertIn('Start Spectrum Capture', sections['rf'])
        self.assertIn('Capture Snapshot', sections['rf'])
        self.assertIn('Next refresh', sections['assistnow'])
        self.assertIn('Save Prediction Window and Limits', sections['assistnow'])
        self.assertNotIn('Request Enable Autonomous', sections['assistnow'])
        self.assertIn('Request Enable Autonomous', sections['advanced'])
        self.assertIn('Export Redacted', sections['advanced'])

    def test_satellite_filters_and_collapsed_table(self):
        page = panel.render_gnss_page(fixture(), 'token', section='satellites', constellation='GPS', band='L5', used_only=True, search='GPS 4').decode()
        self.assertIn('Show 1 matching signal measurements', page)
        self.assertIn('L5 I', page)
        self.assertNotIn('<details open', page)
        self.assertIn('class="table-wrap"', page)
        self.assertNotIn('E5a I</td>', page)

    def test_spectrum_one_plot_per_view_and_no_interpolation(self):
        for view in ('L1', 'L5', 'compare'):
            page = panel.render_gnss_page(fixture(), 'token', section='rf', view=view).decode()
            self.assertEqual(page.count('<svg '), 1)
            self.assertEqual(page.count('<circle '), 512 if view == 'compare' else 256)
            self.assertNotIn('<polyline', page)

    def test_safe_export_includes_corrections_not_geometry(self):
        value = fixture()
        value['corrections']['secret'] = 'must-not-export'
        export = panel.gnss_redacted(value)
        self.assertEqual(export['corrections']['source'], 'WAAS')
        self.assertNotIn('secret', export['corrections'])
        self.assertNotIn('elevation_deg', str(export))

    def test_query_and_raw_diagnostics_html_escaped(self):
        value = fixture()
        value['messages']['receiver'] = {'data': {'firmware': '<script>alert(1)</script>'}}
        page = panel.render_gnss_page(value, 'token', section='advanced').decode()
        self.assertNotIn('<script>', page)
        self.assertIn('&lt;script&gt;', page)


if __name__ == '__main__':
    unittest.main()
