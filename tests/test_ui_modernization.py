"""Presentation parity checks; privileged operations are never executed here."""
from copy import deepcopy
from html.parser import HTMLParser
from urllib.parse import urlsplit
import unittest
import re

from test_pcs_control_panel import pcs, ROOT, ADMIN_DATA, PUBLIC_DATA


class Forms(HTMLParser):
    def __init__(self, page):
        super().__init__()
        self.forms = []
        self.current = None
        self.feed(page)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'form':
            self.current = {'attrs': attrs, 'fields': {}}
            self.forms.append(self.current)
        if tag == 'input' and self.current is not None:
            self.current['fields'][attrs.get('name')] = attrs.get('value')

    def handle_endtag(self, tag):
        if tag == 'form':
            self.current = None


class ModernizationTests(unittest.TestCase):
    def test_shared_navigation_and_bookmarks_reach_complete_power_and_starlink(self):
        home = (ROOT / 'web/pcs-home/index.html').read_text(encoding='utf-8')
        admin = pcs.render_admin_page(ADMIN_DATA, 'test-token', public_data=PUBLIC_DATA).decode()
        required_power = {'power', 'powerCharge', 'powerEnergy', 'batteryCapacity', 'batteryRemaining',
                          'batteryPercent', 'batteryWarning', 'shutdownProtection', 'shutdownRemaining',
                          'lowVoltage', 'powerElapsed', 'non5vPower'}
        for prefix in ['input', 'rail12', 'rail5', 'starlink']:
            required_power.update(prefix + suffix for suffix in ['Monitor', 'Voltage', 'Current', 'Charge'])
        required_power.update(['inputPower', 'inputEnergy', 'rail12DistributionPower', 'rail12DistributionEnergy',
                               'rail12OnlyPower', 'rail12OnlyEnergy', 'rail5Power', 'rail5Energy', 'starlinkPower', 'starlinkEnergy'])
        power = pcs.DASHBOARD_SECTION_TEMPLATES['power']
        self.assertFalse(required_power - set(re.findall(r'data-field="([^"]+)"', power)))
        for page in [home, admin]:
            self.assertIn('href="#starlink" data-view="starlink"', page)
            self.assertIn('href="#power" data-view="power"', page)
            self.assertIn('href="/admin/location">Location settings</a>', page)
            self.assertIn('id="starlink"', page)
            self.assertIn('power-readings', page)
            self.assertNotIn('All four monitors, charge and energy totals</a>', page)
        for view in ['power', 'starlink']:
            alias = (ROOT / f'web/pcs-home/{view}/index.html').read_text(encoding='utf-8')
            self.assertIn(f'data-default-view="{view}"', alias)
            self.assertEqual(alias.replace(f' data-default-view="{view}"', '').replace(f'PCS • {view.title()}', 'PCS • Field communications'), home)
        forms = Forms(admin).forms
        for form in forms:
            if form['fields'].get('action', '').startswith('starlink-'):
                self.assertEqual(form['attrs']['action'], '/admin/run#starlink')

    def test_full_power_and_starlink_keep_zero_unavailable_and_escaped_values(self):
        data = deepcopy(PUBLIC_DATA)
        data['power'] = {'configured': True, 'input_online': True, 'input_power': 0,
                         'input_voltage': 12.6, 'input_current': 0, 'input_charge_since_boot_mah': 0,
                         'rail_12v_online': False, 'rail_12v_voltage': 99, 'starlink_configured': False,
                         'starlink_online': False, 'shutdown_remaining_seconds': 0, 'low_voltage_active': False}
        data['starlink'] = {'configured': True, 'available': True, 'state': '<script>bad</script>',
                            'latency_ms': 0, 'downlink_bps': 2_000_000, 'packet_loss_percent': 0}
        power = pcs.render_dashboard_section(data, 'power')
        self.assertIn('data-field="inputPower">0.0 W<', power)
        self.assertIn('data-field="inputCharge">0.0 mAh<', power)
        self.assertIn('data-field="rail12Voltage">Unavailable<', power)
        self.assertIn('data-field="starlinkMonitor">Not commissioned<', power)
        self.assertIn('data-field="shutdownRemaining">0 s<', power)
        sl = pcs.render_dashboard_section(data, 'starlink')
        self.assertIn('data-field="starlinkDown">2.0 Mbps<', sl)
        self.assertIn('&lt;script&gt;bad&lt;/script&gt;', sl)
        data['starlink']['available'] = False
        sl = pcs.render_dashboard_section(data, 'starlink')
        self.assertIn('data-field="starlinkLatency">Unavailable<', sl)
        self.assertIn('data-field="starlinkState">Unavailable<', sl)

    def test_all_clean_section_bindings_have_server_values(self):
        fields = set(re.findall(r'data-field="([^"]+)"', ''.join(pcs.DASHBOARD_SECTION_TEMPLATES.values())))
        self.assertFalse(fields - pcs.dashboard_values({}).keys())
        data = deepcopy(PUBLIC_DATA)
        data['network'].update(usage_summary='Measured WAN summary', uplinks=[{'type': 'wifi', 'name': 'Wi-Fi', 'active': True, 'state': 'healthy', 'usage': {'rx_bytes': 0, 'tx_bytes': 1_000_000, 'total_bytes': 1_000_000}}])
        page = pcs.render_dashboard_section(data, 'network')
        self.assertIn('Measured WAN summary', page)
        self.assertIn('Down 0.000 MB / Up 1.000 MB', page)

    def test_public_and_admin_overviews_share_structure_and_readings(self):
        public = deepcopy(PUBLIC_DATA)
        public['network']['ap_client_count'] = 0
        public['power'] = {'configured': True, 'input_online': True, 'input_power': 0, 'dc_source': 'battery'}
        shared = pcs.render_overview(public)
        admin = pcs.render_admin_page(ADMIN_DATA, 'test-token', public_data=public).decode()
        homepage = (ROOT / 'web/pcs-home/index.html').read_text(encoding='utf-8')
        for heading in ['Connectivity', 'Radio services', 'Power distribution', 'Storage &amp; time']:
            self.assertIn(heading, shared.replace('Storage & time', 'Storage &amp; time'))
        self.assertIn('data-field="clients">0<', shared)
        self.assertIn('data-field="power">0.0 W<', shared)
        self.assertIn('data-field="clients">0<', admin)
        self.assertIn('data-field="power">0.0 W<', admin)
        for panel in ['network', 'radio', 'power', 'storage']:
            self.assertIn(f'data-panel="{panel}"', homepage)
            self.assertIn(f'data-panel="{panel}"', admin)
        self.assertIn('/assets/css/pcs.css', admin)
        self.assertNotIn('No action output in this request', admin)

    def test_overview_does_not_infer_standby_or_zero_from_unknown(self):
        page = pcs.render_overview({})
        self.assertIn('data-field="overviewWifi">Not reported<', page)
        self.assertIn('data-field="power">Unavailable<', page)
        self.assertIn('data-field="clients">Unavailable<', page)

    def test_every_allowlisted_action_keeps_endpoint_token_and_confirmation(self):
        page = pcs.render_admin_page(ADMIN_DATA, 'test-token').decode()
        forms = Forms(page).forms
        actions = [f for f in forms if 'action' in f['fields']]
        self.assertCountEqual([f['fields']['action'] for f in actions], pcs.ACTION_MAP)
        for form in actions:
            name = form['fields']['action']
            self.assertEqual(urlsplit(form['attrs']['action']).path, '/admin/run')
            self.assertEqual(form['attrs']['method'], 'POST')
            self.assertEqual(form['fields']['csrf'], 'test-token')
            self.assertEqual(form['attrs']['data-confirm'], pcs.ACTION_CONFIRMS.get(name, ''))
            self.assertIn(pcs.esc(pcs.ACTION_MAP[name][1]), page)
        self.assertIn('Staged / unsupported', page)

    def test_every_card_field_and_unknown_future_card_survives(self):
        data = deepcopy(ADMIN_DATA)
        for key in ['network', 'aprs-mailbox', 'power', 'new-future-card']:
            data['cards'].append({'id': key, 'title': '<unsafe>', 'summary': 'unique-' + key,
                                  'items': [{'label': key, 'value': '<script>private detail</script>'}],
                                  'metrics': [{'label': 'meter-' + key, 'value': 0, 'suffix': '%'}]})
        page = pcs.render_admin_details_page(data, 'token').decode()
        clean = pcs.render_admin_page(data, 'token', public_data=PUBLIC_DATA).decode()
        self.assertNotIn('unique-new-future-card', clean)
        self.assertNotIn('private detail', clean)
        for key in ['network', 'aprs-mailbox', 'power', 'new-future-card']:
            self.assertIn('unique-' + key, page)
            self.assertIn('meter-' + key, page)
        self.assertIn('&lt;script&gt;private detail&lt;/script&gt;', page)
        self.assertNotIn('<script>private detail', page)
        self.assertIn('Advanced / Other diagnostics', page)

    def test_empty_real_output_and_failed_exit_are_visible(self):
        page = pcs.render_admin_page(ADMIN_DATA, 'token', '', 'Self-test', 1).decode()
        self.assertIn('Result: Self-test', page)
        self.assertIn('exit 1', page)
        self.assertEqual(page.count('id="action-result"'), 1)

    def test_settings_are_normal_forms_outside_dialogs(self):
        page = pcs.render_admin_page(ADMIN_DATA, 'token').decode()
        self.assertNotIn('<dialog', page)
        forms = Forms(page).forms
        for path in ['/admin/power-settings', '/admin/backup-settings', '/admin/logout']:
            matching = [f for f in forms if urlsplit(f['attrs']['action']).path == path]
            self.assertEqual(len(matching), 1)
            self.assertEqual(matching[0]['fields']['csrf'], 'token')
        self.assertIn('max="43200" step="1"', page)

    def test_public_bindings_use_only_existing_public_fields(self):
        class Bindings(HTMLParser):
            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if 'data-value' in attrs:
                    section, field = attrs['data-value'].split('.')
                    self_test.assertIn(field, pcs.PUBLIC_FIELDS[section])
        self_test = self
        Bindings().feed((ROOT / 'web/pcs-home/index.html').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
