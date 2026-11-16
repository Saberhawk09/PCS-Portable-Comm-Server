"""Offline F10 protocol, diagnostics, assistance and ownership acceptance fixtures."""
import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'scripts'))
from pcs_gnss import ubx, UBXStream
import pcs_gnss_diagnostics as diag
import pcs_assistnow as assist
import pcs_gnss_manager as manager
import pcs_gnss_info as info

NOW = dt.datetime(2026, 11, 16, 12, tzinfo=dt.timezone.utc).timestamp()
TODAY = dt.date(2026, 11, 16)


def version(firmware=b'EXT SPGL1L5 6.00 (041e8a)'):
    return ubx(10, 4, firmware.ljust(30, b'\0') + b'000A0000\0\0' +
               b'MOD=NEO-F10N'.ljust(30, b'\0') + b'PROTVER=40.00'.ljust(30, b'\0'))


def unique(value=b'abcde'):
    return ubx(0x27, 3, b'\1\0\0\0' + value)


IDENTITY = assist.receiver_identity(version(), unique())
CREDENTIALS = {'identity': IDENTITY, 'service_url': assist.SERVICE, 'chipcode': 'secretChipCode99',
               'allowed_data': ['uporb_1', 'uporb_3', 'uporb_7', 'uporb_14', 'ulorb_l1', 'utime', 'usvht', 'ukion']}


def prediction(date=TODAY, gnss=0, sv=1):
    return ubx(0x13, 0x20, bytes((0, 0, sv, gnss, date.year - 2000, date.month, date.day, 0)) + bytes(68))


def span(centers=(1575420000, 1176450000), amplitude=80):
    return bytes((0, len(centers), 0, 0)) + b''.join(bytes([amplitude] * 256) +
           struct.pack('<IIIB3x', 25600000, 100000, center, 12) for center in centers)


def signal_rows():
    rows = [struct.pack('<BBBBhBBBBH4x', 0, 4, sig, 0, -2, cno, 5, 0, 8, 9) for sig, cno in ((0, 42), (6, 38))]
    rows += [struct.pack('<BBBBhBBBBH4x', 2, 4, 3, 0, 1, 35, 4, 0, 8, 2)]
    return struct.pack('<IBB2x', 123000, 0, len(rows)) + b''.join(rows)


def live_response():
    p = bytearray(24)
    p[0] = 0x10
    struct.pack_into('<H', p, 4, 2026)
    p[6:11] = bytes((11, 16, 12, 0, 0))
    return ubx(0x13, 0x40, p) + ubx(0x13, 0, bytes((1, 0, 3)) + bytes(65))


class ProtocolTests(unittest.TestCase):
    def test_chrony_pps_source_offset_is_distinct_from_system_and_receiver_accuracy(self):
        data = diag.chrony_source_offsets('#* F10P 0 0 377 1 -479ns[ -621ns] +/- 134ns\n'
                                          '#? F10N 0 0 000 0 +0ns[ +0ns] +/- 0ns\n'
                                          '^* 10.42.0.99 2 6 377 1 +1ms[ +1ms] +/- 2ms\n')
        self.assertEqual(set(data), {'F10P'})
        self.assertAlmostEqual(data['F10P']['measured_offset_seconds'], -0.000000621)
        self.assertAlmostEqual(data['F10P']['adjusted_offset_seconds'], -0.000000479)
        self.assertEqual(data['F10P']['sample_age_seconds'], 1)
        self.assertNotIn('receiver_time_accuracy_ns', data['F10P'])
    def test_unsupported_message_version_is_distinct_from_missing(self):
        receiver = diag.Diagnostics()
        self.assertIsNone(receiver.update(10, 0x31, bytes((99,)) + span()[1:]))
        self.assertEqual(receiver.snapshot()['messages']['spectrum']['state'], 'Not Supported')
        self.assertEqual(receiver.snapshot()['messages']['rf']['state'], 'Unknown')

    def test_mon_rf_layout_scale_and_states(self):
        block = bytearray(24)
        block[0:4] = bytes((1, 2, 2, 1))
        struct.pack_into('<HH', block, 12, 95, 8191)
        block[16] = 200
        row = diag.decode(10, 0x38, bytes((0, 1, 0, 0)) + block)['paths'][0]
        self.assertEqual((row['band'], row['jamming'], row['agc_percent'], row['cw_suppression']), ('L5', 'Warning', 100, 200))

    def test_exact_span_bin_127_and_quarter_db(self):
        paths = diag.decode(10, 0x31, span())['paths']
        self.assertEqual([p['band'] for p in paths], ['L1', 'L5'])
        self.assertEqual(paths[0]['amplitude_db'], [20] * 256)
        self.assertEqual(paths[0]['frequency_hz'][127], 1575420000)
        self.assertEqual(paths[0]['frequency_hz'][0], 1562720000)
        self.assertEqual(paths[0]['frequency_hz'][255], 1588220000)
        self.assertEqual(paths[1]['pga_db'], 12)

    def test_security_unknown_disabled_warning_critical(self):
        for flags, expected in ((0, 'Disabled'), (1, 'Unknown'), (3, 'No Indication'), (5, 'Warning'), (7, 'Critical')):
            p = bytes((1, 0, 0, 0, flags, 0, 0, 0, flags, 0, 0, 0))
            data = diag.decode(0x27, 9, p)
            self.assertEqual((data['jamming'], data['spoofing']), (expected, expected))
            self.assertFalse(data['authenticity_proven'])

    def test_signals_same_spacecraft_two_bands(self):
        receiver = diag.Diagnostics()
        receiver.update(1, 0x43, signal_rows())
        receiver.update_sky({'satellites': [{'gnssid': 0, 'svid': 4, 'used': True},
                                          {'gnssid': 0, 'svid': 4, 'used': True},
                                          {'gnssid': 2, 'svid': 4, 'used': False}]})
        data = receiver.snapshot()
        self.assertEqual((data['counts']['visible'], data['counts']['tracked'], data['counts']['used']), (2, 2, 1))
        self.assertEqual(data['counts']['constellations']['GPS'], 1)
        rows = data['messages']['signals']['data']['rows']
        self.assertEqual([r['band'] for r in rows], ['L1', 'L5', 'L5'])
        self.assertEqual(rows[2]['health'], 'Unhealthy')

    def test_missing_does_not_become_zero_or_disabled(self):
        value = diag.Diagnostics().snapshot()
        self.assertIsNone(value['counts']['visible'])
        self.assertEqual(value['messages']['security']['state'], 'Unknown')
        self.assertIsNone(value['messages']['security']['data'])

    def test_stale_values_and_signal_trend_samples(self):
        now = [100.0]
        receiver = diag.Diagnostics(clock=lambda: now[0], wall=lambda: NOW + now[0])
        receiver.update(1, 0x43, signal_rows())
        now[0] += 8
        data = receiver.snapshot()
        self.assertEqual(data['messages']['signals']['state'], 'Stale')
        self.assertEqual(len(data['signal_trends']), 1)
        self.assertEqual(data['signal_trends'][0]['L5'], 2)

    def test_malformed_versions_truncations_and_counts(self):
        valid = [(10, 0x38, bytes((0, 0, 0, 0))), (10, 0x31, span()),
                 (0x27, 9, bytes((1,)) + bytes(11)), (0x27, 0x10, bytes(8)), (1, 0x43, signal_rows())]
        for cls, msg, payload in valid:
            for bad in (payload[:-1], payload + b'\0'):
                with self.subTest(cls=cls, msg=msg, length=len(bad)), self.assertRaises(ValueError):
                    diag.decode(cls, msg, bad)
        for cls, msg, payload in valid[:4]:
            with self.subTest(msg=msg), self.assertRaises(ValueError):
                diag.decode(cls, msg, bytes((99,)) + payload[1:])

    def test_full_frame_checksum_corruption_not_delivered(self):
        decoder = UBXStream()
        good = ubx(10, 0x31, span())
        bad = good[:-1] + bytes((good[-1] ^ 1,))
        self.assertEqual(decoder.feed(bad), [])
        self.assertEqual(len(decoder.feed(good[:19]) + decoder.feed(good[19:])), 1)

    def test_nav_status_ttff_and_power_loss(self):
        status = struct.pack('<IBBBBII', 12000, 3, 1, 0, 0, 12000, 20000)
        self.assertEqual(diag.decode(1, 3, status)['ttff_seconds'], 12)
        self.assertIsNone(diag.decode(1, 3, bytes(16))['ttff_seconds'])
        self.assertEqual(diag.decode(1, 0x60, bytes(16)), {'enabled': False, 'activity': 'Idle'})

    def test_position_time_accuracy_kept_separate_and_coordinates_discarded(self):
        p = bytearray(92)
        p[11], p[20], p[21] = 7, 3, 1
        struct.pack_into('<I', p, 12, 25)
        struct.pack_into('<iiII', p, 32, 150000, 120000, 500, 750)
        result = diag.decode(1, 7, p)
        self.assertEqual((result['horizontal_accuracy_m'], result['receiver_time_accuracy_ns']), (0.5, 25))
        self.assertEqual(result['altitude_msl_m'], 120)
        self.assertNotIn('latitude', result)
        self.assertNotIn('offset_seconds', result)


class EventCaptureTests(unittest.TestCase):
    def test_security_transition_history_persists_warnings_without_poll_spam(self):
        history = diag.EventHistory(wall=lambda: NOW)
        warning = {'jamming': 'Warning', 'spoofing': 'No Indication'}
        self.assertTrue(history.observe_security(warning))
        self.assertFalse(history.observe_security(warning))
        restarted = diag.EventHistory(history.events, wall=lambda: NOW)
        self.assertFalse(restarted.observe_security(warning))
        self.assertTrue(restarted.observe_security({'jamming': 'No Indication', 'spoofing': 'No Indication'}))
        self.assertEqual(len(restarted.events), 2)

    def test_event_log_elapsed_ages_deduplicated_across_polls_and_restart(self):
        host, mono = [NOW], [100.0]
        log = lambda age: diag.decode(0x27, 0x10, bytes((0, 1)) + bytes(6) + struct.pack('<IBB2x', age, 0, 0))
        history = diag.EventHistory(clock=lambda: mono[0], wall=lambda: host[0])
        self.assertTrue(history.observe(log(10)))
        host[0] += 4
        mono[0] += 4
        self.assertFalse(history.observe(log(14)))
        restarted = diag.EventHistory(history.events, clock=lambda: mono[0], wall=lambda: host[0])
        self.assertFalse(restarted.observe(log(14)))
        self.assertEqual(len(restarted.events), 1)

    def test_clock_step_does_not_duplicate_running_history(self):
        wall, mono = [NOW], [100.0]
        history = diag.EventHistory(clock=lambda: mono[0], wall=lambda: wall[0])
        event = {'events': [{'elapsed_seconds': 10, 'detection_code': 1, 'event_code': 0}]}
        history.observe(event)
        wall[0] += 3600
        mono[0] += 1
        event['events'][0]['elapsed_seconds'] += 1
        self.assertFalse(history.observe(event))

    def test_event_history_retention_and_unknown_old_timestamp(self):
        history = diag.EventHistory(limit=2, wall=lambda: NOW)
        for n in (1, 2, 3):
            history.observe({'events': [{'elapsed_seconds': 5, 'detection_code': n, 'event_code': 0}]})
        self.assertEqual(len(history.events), 2)
        event = {'events': [{'elapsed_seconds': None, 'detection_code': 4, 'event_code': 0}]}
        history.observe(event)
        self.assertFalse(history.observe(event))
        self.assertIsNone(history.events[-1]['estimated_timestamp'])

    def test_passive_capture_start_stop_timeout(self):
        now = [100.0]
        capture = diag.SpectrumCapture(lambda: now[0])
        with self.assertRaises(ValueError):
            capture.start(60, False)
        capture.start(5, True)
        capture.observe(diag.decode(10, 0x31, span()), NOW)
        self.assertTrue(capture.status()['active'])
        now[0] += 6
        self.assertFalse(capture.status()['active'])
        capture.start(5, True)
        capture.stop()
        self.assertFalse(capture.active())

    def test_bounded_traces_and_no_averaging_between_gain_or_frequency_changes(self):
        capture = diag.SpectrumCapture()
        capture.start(60, True)
        for n in range(65):
            capture.observe(diag.decode(10, 0x31, span(amplitude=80)), NOW + n)
        capture.observe(diag.decode(10, 0x31, span(centers=(1570000000,), amplitude=40)), NOW + 66)
        value = capture.status()
        self.assertEqual(value['frame_count'], 60)
        self.assertEqual(value['traces'][0]['sample_count'], 1)
        self.assertEqual(value['traces'][0]['average_db'], [10] * 256)


class AssistanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.cache = assist.Cache(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_predictive_windows_and_date_selection(self):
        for window in (1, 3, 7, 14):
            payload = prediction() + prediction(TODAY + dt.timedelta(days=window - 1), 2, 4)
            self.cache.publish(payload, IDENTITY, 'predictive', NOW, window)
            kind, selected = self.cache.selected(IDENTITY, NOW)
            self.assertEqual(kind, 'predictive')
            self.assertTrue(all(f[10:13] == bytes((26, 11, 16)) for f in selected))
            self.assertEqual(len(selected), 2 if window == 1 else 1)

    def test_expired_malformed_checksum_and_b1i_rejected(self):
        bad_payloads = [prediction(TODAY - dt.timedelta(days=7)), prediction(gnss=3), prediction(gnss=6),
                        prediction()[:-1], prediction() + b'junk', ubx(6, 4, bytes(4))]
        damaged = bytearray(prediction())
        damaged[-1] ^= 1
        bad_payloads.append(bytes(damaged))
        for payload in bad_payloads:
            with self.subTest(length=len(payload)), self.assertRaises(assist.AssistanceError):
                self.cache.publish(payload, IDENTITY, 'predictive', NOW)

    def test_corruption_wrong_identity_and_expiration_fall_back(self):
        self.cache.publish(prediction(), IDENTITY, 'predictive', NOW)
        self.assertEqual(self.cache.selected(IDENTITY | {'binding': 'wrong'}, NOW), ('autonomous-or-ordinary', []))
        self.assertEqual(self.cache.selected(IDENTITY, NOW + 2 * 86400), ('autonomous-or-ordinary', []))
        next(self.root.glob('*.ubx')).write_bytes(b'corrupt')
        self.assertEqual(self.cache.selected(IDENTITY, NOW), ('autonomous-or-ordinary', []))

    def test_malformed_cache_metadata_never_blocks_ordinary_startup_or_other_cache(self):
        for value in ('invalid JSON', json.dumps([]), json.dumps({'version': 1, 'expires_at': 'bad-date'}),
                      json.dumps({'version': 1, 'downloaded_at': NOW, 'expires_at': NOW + 3600, 'sha256': []})):
            (self.root / 'predictive.json').write_text(value)
            self.assertEqual(self.cache.selected(IDENTITY, NOW), ('autonomous-or-ordinary', []))
        (self.root / 'live.json').write_text('invalid JSON')
        self.cache.publish(prediction(), IDENTITY, 'predictive', NOW)
        self.assertEqual(self.cache.selected(IDENTITY, NOW, live=True)[0], 'predictive')
        (self.root / 'live.json').write_text('invalid JSON')
        self.assertEqual(assist.public_status(assist.config({}), CREDENTIALS, {}, self.cache, NOW)['live']['state'], 'Invalid')

    def test_atomic_interrupted_publish_preserves_previous_archive(self):
        self.cache.publish(prediction(), IDENTITY, 'predictive', NOW)
        original = (self.root / 'predictive.json').read_bytes()
        real = assist.os.replace
        def interrupted(source, target):
            if Path(target).name == 'predictive.json':
                raise OSError('interrupted')
            return real(source, target)
        with mock.patch.object(assist.os, 'replace', side_effect=interrupted):
            with self.assertRaises(OSError):
                self.cache.publish(prediction(sv=2), IDENTITY, 'predictive', NOW)
        self.assertEqual((self.root / 'predictive.json').read_bytes(), original)
        self.assertEqual(self.cache.selected(IDENTITY, NOW)[0], 'predictive')
        self.cache.prune(NOW)
        self.assertEqual(len(list(self.root.glob('*.ubx'))), 1)

    def test_cache_retention_and_clear_expired(self):
        for sv in range(1, 5):
            self.cache.publish(prediction(sv=sv), IDENTITY, 'predictive', NOW)
        self.assertEqual(len(list(self.root.glob('*.ubx'))), 1)
        self.cache.prune(NOW + 2 * 86400)
        self.assertEqual(list(self.root.glob('*.ubx')), [])

    def test_firmware_protocol_and_receiver_binding(self):
        for ver, uid in ((version(b'ROM SPGL1L5 6.00'), unique()),
                         (version(), ubx(0x27, 3, bytes(9)))):
            with self.assertRaises(ValueError):
                assist.receiver_identity(ver, uid)
        self.assertNotEqual(IDENTITY['binding'], assist.receiver_identity(version(), unique(b'vwxyz'))['binding'])

    def test_live_freshness_types_and_fallback_to_predictive(self):
        assist.validate_live(live_response(), NOW)
        with self.assertRaises(assist.AssistanceError):
            assist.validate_live(live_response(), NOW + 3600)
        with self.assertRaises(assist.AssistanceError):
            assist.validate_live(live_response() + ubx(0x13, 3, bytes(88)), NOW)
        self.cache.publish(prediction(), IDENTITY, 'predictive', NOW)
        self.assertEqual(self.cache.selected(IDENTITY, NOW, live=True)[0], 'predictive')
        self.cache.publish(live_response(), IDENTITY, 'live', NOW)
        self.assertEqual(self.cache.selected(IDENTITY, NOW, live=True)[0], 'live')
        self.assertFalse(any(frame[3] == 0x40 for frame in self.cache.selected(IDENTITY, NOW + 3600, live=True)[1]))

    def test_offline_missing_credentials_and_metered_are_normal_states(self):
        client = assist.Downloader(self.cache, request=mock.Mock(), clock=lambda: NOW)
        for credentials, uplink, expected in [(CREDENTIALS, {'online': False, 'metered': False}, 'Offline'),
            (None, {'online': True, 'metered': False}, 'Missing Credentials'),
            (CREDENTIALS, {'online': True, 'metered': True}, 'Metered')]:
            value = client.refresh({}, credentials, uplink, {})
            self.assertIn(expected, value['state'])
        client.request.assert_not_called()

    def test_invalid_credential_schema_and_file_are_normal_nonrequest_states(self):
        client = assist.Downloader(self.cache, request=mock.Mock(), clock=lambda: NOW)
        for credential in ({}, {'chipcode': 'bad'}, ['wrong-type'], CREDENTIALS | {'identity': {}}):
            result = client.refresh({}, credential, {'online': True, 'metered': False}, {})
            self.assertIn('Invalid Credentials', result['state'])
        path = self.root / 'credential.json'
        path.write_text('invalid JSON secretChipCode99')
        self.assertEqual(assist.load_credentials(path), {'invalid': True})
        client.request.assert_not_called()

    def test_requests_limits_duplicate_suppression_and_secret_free_status(self):
        request = mock.Mock(return_value=prediction())
        client = assist.Downloader(self.cache, request=request, clock=lambda: NOW)
        uplink = {'online': True, 'metered': False, 'uplink': 'Selected Wi-Fi'}
        usage = client.refresh({}, CREDENTIALS, uplink, {}, force=True)
        value = client.refresh({}, CREDENTIALS, uplink, usage, force=True)
        self.assertEqual(request.call_count, 1)
        self.assertIn('Suppressed', value['state'])
        self.assertNotIn('lat=', request.call_args.args[0])
        status = assist.public_status(assist.config({}), CREDENTIALS, value, self.cache, NOW)
        self.assertNotIn(CREDENTIALS['chipcode'], json.dumps(status))
        self.assertNotIn(IDENTITY['binding'], json.dumps(status))
        self.assertEqual(usage['requests_month'], 1)

    def test_403_429_timeouts_and_invalid_download_preserve_cache_and_backoff(self):
        self.cache.publish(prediction(), IDENTITY, 'predictive', NOW)
        for failure in (assist.AssistanceError('Assistance HTTP 403'), assist.AssistanceError('Assistance HTTP 429'), TimeoutError('secretChipCode99')):
            client = assist.Downloader(self.cache, request=mock.Mock(side_effect=failure), clock=lambda: NOW)
            usage = client.refresh({}, CREDENTIALS, {'online': True, 'metered': False, 'uplink': 'LTE'}, {}, force=True)
            self.assertGreater(usage['retry_after'], NOW)
            self.assertEqual(usage['requests_month'], 1)
            self.assertNotIn('secretChipCode99', json.dumps(usage))
            self.assertEqual(self.cache.selected(IDENTITY, NOW)[0], 'predictive')
            self.assertIn('Backoff', client.refresh({}, CREDENTIALS, {'online': True, 'metered': False}, usage)['state'])

    def test_no_live_request_without_enablement_cost_consent_and_entitlement(self):
        client = assist.Downloader(self.cache, request=mock.Mock(return_value=live_response()), clock=lambda: NOW)
        online = {'online': True, 'metered': False, 'uplink': 'Wi-Fi'}
        self.assertIn('Disabled', client.refresh({}, CREDENTIALS, online, {}, 'live')['state'])
        with self.assertRaises(assist.AssistanceError):
            assist.config({'live_enabled': True})
        settings = {'live_enabled': True, 'live_cost_authorized': True}
        result = client.refresh(settings, CREDENTIALS | {'allowed_data': []}, online, {}, 'live')
        self.assertIn('Entitlement', result['state'])
        client.request.assert_not_called()
        result = client.refresh(settings, CREDENTIALS, online, {}, 'live')
        self.assertIn('Available', result['state'])

    def test_failed_live_backoff_does_not_block_free_predictive_refresh(self):
        request = mock.Mock(side_effect=[assist.AssistanceError('Assistance HTTP 403'), prediction()])
        client = assist.Downloader(self.cache, request=request, clock=lambda: NOW)
        settings = {'live_enabled': True, 'live_cost_authorized': True}
        online = {'online': True, 'metered': False, 'uplink': 'Selected Wi-Fi'}
        usage = client.refresh(settings, CREDENTIALS, online, {}, 'live')
        self.assertIn('Rejected', usage['state'])
        result = client.refresh(settings, CREDENTIALS, online, usage, 'predictive')
        self.assertIn('Available', result['state'])
        self.assertEqual(request.call_count, 2)

    def test_month_and_byte_limits_apply_before_network(self):
        client = assist.Downloader(self.cache, request=mock.Mock(), clock=lambda: NOW)
        online = {'online': True, 'metered': False, 'uplink': 'Wi-Fi'}
        usage = {'day': '2026-11-16', 'month': '2026-11', 'requests_month': 250}
        self.assertIn('Limit', client.refresh({}, CREDENTIALS, online, usage)['state'])
        usage = {'day': '2026-11-16', 'month': '2026-11', 'reserved_bytes': 1048576}
        self.assertIn('Limit', client.refresh({}, CREDENTIALS, online, usage)['state'])
        client.request.assert_not_called()

    def test_registration_full_frames_and_no_automatic_subscription(self):
        request = mock.Mock(return_value=json.dumps({'chipcode': 'enrollmentSecret', 'serviceUrl': assist.SERVICE,
                                                    'allowedData': ['uporb_7']}).encode())
        result = assist.enroll('profileToken123', version(), unique(), request)
        self.assertEqual(result['allowed_data'], ['uporb_7'])
        body = json.loads(request.call_args.args[1])
        self.assertEqual(body['messages']['UBX-SEC-UNIQID'], unique().hex())
        self.assertEqual(request.call_args.args[0], assist.ZTP)
        self.assertFalse(assist.DEFAULT['live_enabled'])

    def test_tls_host_redirect_and_credential_permissions(self):
        with self.assertRaises(assist.AssistanceError):
            assist.https('http://assistnow.services.u-blox.com/')
        with self.assertRaises(assist.AssistanceError):
            assist.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://evil.invalid')
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.headers = {'Content-Length': '10'}
        response.read1.side_effect = [b'partial', b'']
        opener = mock.Mock()
        opener.open.return_value = response
        with mock.patch.object(assist.urllib.request, 'build_opener', return_value=opener):
            with self.assertRaises(assist.AssistanceError) as rejected:
                assist.https(assist.SERVICE + '?chipcode=private-secret')
            self.assertNotIn('private-secret', str(rejected.exception))
            response.read1.side_effect = assist.http.client.IncompleteRead(b'partial')
            with self.assertRaises(assist.AssistanceError):
                assist.https(assist.SERVICE + '?chipcode=private-secret')
        if os.name == 'posix':
            path = self.root / 'secret.json'
            path.write_text('{}')
            path.chmod(0o644)
            with self.assertRaises(assist.AssistanceError):
                assist.read_json(path, private=True)

    def test_uplink_freshness_and_unknown_metering(self):
        value = assist.network({'collected_monotonic': 100, 'data': {'network': {'internet_available': True, 'uplink_type': 'LTE'}}}, 101)
        self.assertTrue(value['online'])
        self.assertTrue(value['metered'])
        self.assertFalse(assist.network({'collected_monotonic': 10}, 100)['online'])

    def test_metering_readback_uses_only_already_selected_interface(self):
        runtime = {'generated_at': NOW, 'active_id': 'wifi', 'uplinks': [
            {'id': 'wifi', 'active': True, 'internet': True, 'interface': 'wlan1'}]}
        with mock.patch.object(info.assist, 'network', return_value={'online': True, 'metered': True, 'uplink': 'Wi-Fi'}), \
             mock.patch.object(info.assist, 'read_json', return_value=runtime), \
             mock.patch.object(info.time, 'time', return_value=NOW), \
             mock.patch.object(info.subprocess, 'run', return_value=mock.Mock(returncode=0, stdout='no\n')) as reader:
            result = info.selected_network()
            self.assertFalse(result['metered'])
            self.assertEqual(reader.call_args.args[0], ['nmcli', '--get-values', 'GENERAL.METERED', 'device', 'show', 'wlan1'])
            runtime['uplinks'][0]['interface'] = 'eth0.10'
            reader.reset_mock()
            info.selected_network()
            reader.assert_not_called()


class ReceiverManagerTests(unittest.TestCase):
    def test_existing_owner_exchange_completes_partial_nonblocking_writes(self):
        spec = importlib.util.spec_from_file_location('partial_gnss_exchange', REPO / 'scripts/pcs-gnss.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        sent = bytearray()
        request = ubx(0x13, 0x20, bytes(76))
        def write(fd, pending):
            size = min(3, len(pending))
            sent.extend(pending[:size])
            return size
        import select
        with mock.patch.object(module.os, 'write', side_effect=write), mock.patch.object(module.os, 'read', return_value=ubx(5, 1, b'\6\x8a')), \
             mock.patch.object(select, 'select', side_effect=lambda reads, writes, errors, timeout: (reads, writes, [])):
            self.assertEqual(module.exchange(123, request, 5, 1), b'\6\x8a')
        self.assertEqual(bytes(sent), request)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.values = {manager.ANA: 0, manager.ITFM: 0, manager.ACK_AIDING: 0}
        self.calls = []

    def tearDown(self):
        self.temp.cleanup()

    def exchange(self, request, cls, msg, ack=None):
        self.calls.append(request)
        if request[2:4] == b'\x0a\x04':
            return version()[6:-2]
        if request[2:4] == b'\x27\x03':
            return unique()[6:-2]
        if request[2:4] == b'\x06\x8b':
            keys = [int.from_bytes(request[i:i + 4], 'little') for i in range(10, len(request) - 2, 4)]
            return b'\1\0\0\0' + b''.join(struct.pack('<IB', k, self.values[k]) for k in keys)
        if request[2:4] == b'\x06\x8a':
            for i in range(10, len(request) - 2, 5):
                self.values[int.from_bytes(request[i:i + 4], 'little')] = request[i + 4]
            return b'\6\x8a'
        if request[2] == 0x13:
            return bytes((1, 0, 0, request[3])) + request[6:10]
        raise AssertionError('Unexpected receiver command')

    def test_autonomous_requires_confirmation_ram_only_and_preserves_itfm(self):
        with self.assertRaises(assist.AssistanceError):
            manager.set_feature(self.exchange, manager.ANA, True, False, self.root)
        manager.set_feature(self.exchange, manager.ANA, True, True, self.root)
        self.assertEqual(self.values[manager.ANA], 1)
        self.assertEqual(self.values[manager.ITFM], 0)
        writes = [f for f in self.calls if f[2:4] == b'\6\x8a']
        self.assertTrue(all(f[7] == 1 for f in writes))

    def test_injection_ack_failure_restores_temporary_setting_without_reset(self):
        def failed(request, cls, msg, ack=None):
            if request[2] == 0x13:
                raise OSError('injection interrupted')
            return self.exchange(request, cls, msg, ack)
        with self.assertRaises(OSError):
            manager.inject(failed, [prediction()], root=self.root)
        self.assertEqual(self.values[manager.ACK_AIDING], 0)
        self.assertEqual(json.loads((self.root / 'receiver-transaction.json').read_text())['state'], 'restored')
        self.assertFalse(any(f[2:4] == b'\x06\x04' for f in self.calls))

    def test_mismatched_ack_rejected_and_cleanup(self):
        def wrong(request, cls, msg, ack=None):
            if request[2] == 0x13:
                return bytes(8)
            return self.exchange(request, cls, msg, ack)
        with self.assertRaises(assist.AssistanceError):
            manager.inject(wrong, [prediction()], root=self.root)
        self.assertEqual(self.values[manager.ACK_AIDING], 0)

    def test_absolute_download_time_is_never_injected(self):
        with self.assertRaises(assist.AssistanceError):
            manager.inject(self.exchange, assist.frames(live_response()), root=self.root)
        self.assertEqual(self.calls, [])

    def test_successful_injection_and_interrupted_journal_recovery(self):
        result = manager.inject(self.exchange, [prediction()], root=self.root)
        self.assertEqual(result, {'state': 'Accepted', 'accepted': 1})
        self.values[manager.ACK_AIDING] = 1
        (self.root / 'receiver-transaction.json').write_text(json.dumps({'state': 'pending', 'binding': IDENTITY['binding'], 'before': {str(manager.ACK_AIDING): 0}}))
        manager.restore_pending(self.exchange, self.root)
        self.assertEqual(self.values[manager.ACK_AIDING], 0)

    def test_pending_ram_journal_never_restores_to_unbound_or_replacement_receiver(self):
        for binding in (None, '0' * 64):
            self.calls.clear()
            self.values[manager.ACK_AIDING] = 1
            journal = {'state': 'pending', 'before': {str(manager.ACK_AIDING): 0}}
            if binding is not None:
                journal['binding'] = binding
            (self.root / 'receiver-transaction.json').write_text(json.dumps(journal))
            with self.assertRaises(assist.AssistanceError):
                manager.restore_pending(self.exchange, self.root)
            self.assertEqual(self.values[manager.ACK_AIDING], 1)
            self.assertFalse(any(f[2] == 6 for f in self.calls))

    def test_boot_no_network_no_cache_and_already_healthy(self):
        exchange = mock.Mock()
        settings = assist.config({'boot_injection': True})
        result = manager.boot_assistance(exchange, version()[6:-2], unique()[6:-2], timing_healthy=True,
                                         settings=settings, root=self.root, now=NOW)
        self.assertIn('Healthy', result['state'])
        exchange.assert_not_called()
        result = manager.boot_assistance(exchange, version()[6:-2], unique()[6:-2],
                                         settings=settings, root=self.root, now=NOW)
        self.assertIn('ordinary', result['state'])
        exchange.assert_not_called()

    def test_backup_power_loss_and_already_fixed_skip_boot_injection(self):
        assist.Cache(self.root).publish(prediction(), IDENTITY, 'predictive', NOW)
        for valid, fixed, reason in ((0, 0, 'UTC unavailable'), (7, 1, 'already fixed')):
            p = bytearray(92)
            struct.pack_into('<H', p, 4, 2026)
            p[6], p[7], p[11], p[21] = 11, 16, valid, fixed
            exchange = mock.Mock(return_value=bytes(p))
            result = manager.boot_assistance(exchange, version()[6:-2], unique()[6:-2], settings=assist.config({'boot_injection': True}), root=self.root, now=NOW)
            self.assertIn(reason, result['state'])
            self.assertEqual(exchange.call_count, 1)

    def test_boot_injects_only_current_day_predictions_in_existing_owner(self):
        payload = prediction() + prediction(TODAY + dt.timedelta(days=1), sv=2)
        assist.Cache(self.root).publish(payload, IDENTITY, 'predictive', NOW)
        pvt = bytearray(92)
        struct.pack_into('<H', pvt, 4, 2026)
        pvt[6], pvt[7], pvt[11] = 11, 16, 7
        def exchange(request, cls, msg, ack=None):
            if request[2:4] == b'\x01\x07':
                return pvt
            return self.exchange(request, cls, msg, ack)
        result = manager.boot_assistance(exchange, version()[6:-2], unique()[6:-2],
            settings=assist.config({'boot_injection': True}), root=self.root, now=NOW)
        self.assertEqual((result['source'], result['accepted']), ('predictive', 1))
        sent = [f for f in self.calls if f[2] == 0x13]
        self.assertEqual(sent, [prediction()])

    def test_web_autonomous_only_queues_and_ownership_conflict_denies_serial(self):
        if os.name == 'posix':
            with mock.patch.object(assist, 'ROOT', self.root):
                result = info.web_update({'operation': 'autonomous-enable', 'confirm': 'yes'})
                self.assertIn('unchanged', result['state'])
        with mock.patch.object(info.subprocess, 'run', return_value=mock.Mock(returncode=0, stdout='active\n')):
            with self.assertRaises(assist.AssistanceError):
                info.maintenance('maintenance-inspect', info.MAINTENANCE)

    def test_normal_diagnostics_have_no_uart_or_service_restart_api(self):
        source = (REPO / 'scripts/pcs_gnss_diagnostics.py').read_text()
        self.assertNotIn('os.open', source)
        serve = __import__('inspect').getsource(info.serve)
        self.assertNotIn('serial_port', serve)
        self.assertNotIn('systemctl', serve)
        self.assertIn("['chronyc', '-h', '127.0.0.1', '-n', 'sources']", serve)
        for control in ('selectopts', 'reselect', 'makestep'):
            self.assertNotIn(control, serve)


class StagingTests(unittest.TestCase):
    def test_stage_manifest_corruption_is_rejected_before_installation(self):
        spec = importlib.util.spec_from_file_location('gnss_info_stage_test', REPO / 'scripts/stage-gnss-intelligence.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary:
            target = module.stage(Path(temporary) / 'stage')
            manifest = module.verify_stage(target)
            self.assertIn('/etc/sudoers.d/pcs-gnss-info', manifest['files'])
            self.assertNotIn('/etc/default/gpsd', manifest['files'])
            self.assertNotIn('/etc/pcs/gnss.json', manifest['files'])
            (target / '0').write_bytes(b'changed')
            with self.assertRaises(ValueError):
                module.verify_stage(target)
            journal = Path(temporary) / 'receiver-transaction.json'
            journal.write_text('{"state":"pending"}')
            with mock.patch.object(module, 'ASSIST_JOURNAL', journal), self.assertRaisesRegex(ValueError, 'recovery journal'):
                module.rollback(Path(temporary) / 'missing-backup')


if __name__ == '__main__':
    unittest.main()
