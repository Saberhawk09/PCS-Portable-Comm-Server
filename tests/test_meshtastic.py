import json
import contextlib
import io
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import pcs_meshtastic_status as meshtastic_status  # noqa: E402
import pcs_meshtastic_gateway as meshtastic_gateway  # noqa: E402
import pcs_meshtastic_import_mqtt as meshtastic_import_mqtt  # noqa: E402


class MeshtasticStatusTests(unittest.TestCase):
    def test_status_counts_recent_remote_nodes_without_storing_private_mesh_data(self):
        now = 2_000_000_000
        nodes = {
            "!local": {
                "num": 10,
                "user": {
                    "id": "!local",
                    "longName": "PCS Mesh",
                    "shortName": "PCS",
                    "hwModel": "RAK4631",
                },
                "position": {"latitude": 41.0, "longitude": -81.0},
                "deviceMetrics": {
                    "batteryLevel": 83,
                    "voltage": 4.08,
                    "channelUtilization": 4.5,
                    "airUtilTx": 1.25,
                },
                "localStats": {"numPacketsRx": 42, "numPacketsTx": 7},
                "environmentMetrics": {
                    "time": now - 10,
                    "temperature": 29.5,
                    "relativeHumidity": 44.25,
                },
            },
            "!recent": {
                "num": 11,
                "user": {"longName": "Private remote name"},
                "position": {"latitude": 40.0, "longitude": -82.0},
                "lastHeard": now - 60,
                "lastReceived": {"decoded": {"text": "private message"}},
            },
            "!old": {"num": 12, "lastHeard": now - 3_600},
        }
        my_info = SimpleNamespace(my_node_num=10, firmware_version="2.7.20")

        status = meshtastic_status.build_status(nodes, my_info, now=now)

        self.assertEqual(status["state"], "connected")
        self.assertEqual(status["device"]["hardware"], "RAK4631")
        self.assertEqual(status["device"]["battery_percent"], 83)
        self.assertEqual(status["mesh"]["known_remote_nodes"], 2)
        self.assertEqual(status["mesh"]["recent_remote_nodes"], 1)
        self.assertEqual(status["mesh"]["received_packets"], 42)
        self.assertEqual(status["case_environment"]["temperature_c"], 29.5)
        self.assertEqual(status["case_environment"]["temperature_f"], 85.1)
        self.assertEqual(status["case_environment"]["humidity_percent"], 44.25)
        self.assertEqual(status["case_environment"]["sample_age_seconds"], 10)
        self.assertEqual(status["case_environment"]["source"], "local-meshtastic-node")
        serialized = json.dumps(status)
        self.assertNotIn("Private remote name", serialized)
        self.assertNotIn("private message", serialized)
        self.assertNotIn("latitude", serialized)
        self.assertNotIn("!recent", serialized)

    def test_external_power_sentinel_is_not_reported_as_battery_percent(self):
        nodes = {
            "!local": {
                "num": 1,
                "deviceMetrics": {"batteryLevel": 101},
            }
        }
        status = meshtastic_status.build_status(
            nodes,
            SimpleNamespace(my_node_num=1),
            now=2_000_000_000,
        )
        self.assertEqual(status["device"]["power"], "external")
        self.assertIsNone(status["device"]["battery_percent"])

    def test_errors_are_classified_without_leaking_device_address(self):
        status = meshtastic_status.classify_error(
            RuntimeError("Authentication failed for AA:BB:CC:DD:EE:FF"),
            now=2_000_000_000,
        )
        self.assertEqual(status["reason"], "pairing-required")
        self.assertNotIn("AA:BB", json.dumps(status))

    def test_status_file_write_is_valid_and_atomic(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "status.json"
            snapshot = {"schema_version": 1, "state": "connected"}
            meshtastic_status.write_status(target, snapshot)
            self.assertEqual(meshtastic_status.read_status(target), snapshot)

    def test_status_command_reads_existing_usb_snapshot_without_collecting(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "status.json"
            snapshot = {
                "schema_version": 1,
                "state": "connected",
                "transport": "usb-serial",
            }
            meshtastic_status.write_status(target, snapshot)
            output = io.StringIO()
            with contextlib.redirect_stdout(output), mock.patch.object(
                meshtastic_status,
                "collect",
            ) as collect:
                result = meshtastic_status.main(["--status-file", str(target), "--check"])

            self.assertEqual(result, 0)
            self.assertEqual(json.loads(output.getvalue()), snapshot)
            collect.assert_not_called()

    def test_direct_ble_collection_requires_explicit_flag(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "status.json"
            with mock.patch.object(
                meshtastic_status,
                "collect",
                return_value={"schema_version": 1, "state": "connected"},
            ) as collect, contextlib.redirect_stdout(io.StringIO()):
                result = meshtastic_status.main(
                    [
                        "--collect-ble",
                        "--device",
                        "IJC1",
                        "--status-file",
                        str(target),
                    ]
                )

            self.assertEqual(result, 0)
            collect.assert_called_once()
            self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["state"], "connected")

    def test_gateway_limits_radio_writes_to_proxy_and_opt_in_position(self):
        source = (ROOT / "scripts" / "pcs_meshtastic_gateway.py").read_text(encoding="utf-8")
        for forbidden in (
            "sendText(",
            "sendData(",
            "writeConfig(",
            "setOwner(",
            "setFixedPosition(",
        ):
            self.assertNotIn(forbidden, source)
        self.assertIn("sendMqttClientProxyMessage(topic, payload)", source)
        self.assertIn("self.interface.sendPosition(", source)
        self.assertIn('getattr(self.args, "gpsd_position", False)', source)
        self.assertIn("noNodes=True", source)

    def test_gateway_uses_pcs_ble_startup_drain_adapter(self):
        gateway = (ROOT / "scripts" / "pcs_meshtastic_gateway.py").read_text(encoding="utf-8")
        transport = (ROOT / "scripts" / "pcs_meshtastic_ble.py").read_text(encoding="utf-8")
        setup = (ROOT / "scripts" / "setup-meshtastic-bluetooth.sh").read_text(encoding="utf-8")

        self.assertIn("from pcs_meshtastic_ble import PCSBLEInterface", gateway)
        self.assertIn("return PCSBLEInterface(", gateway)
        self.assertIn("self.client.start_notify(FROMNUM_UUID", transport)
        self.assertLess(
            transport.index("self.client.start_notify(FROMNUM_UUID"),
            transport.index("self._startConfig()"),
        )
        self.assertIn("def _drain_startup(self)", transport)
        self.assertIn("target=self._receive_from_radio", transport)
        self.assertNotIn("disconnected_callback=lambda _: self.close()", transport)
        self.assertIn('BLE_TARGET="/usr/local/sbin/pcs_meshtastic_ble.py"', setup)
        self.assertIn('"${BLE_SOURCE}" "${BLE_TARGET}"', setup)

    def test_gateway_supports_scoped_usb_serial_fallback(self):
        gateway = (ROOT / "scripts" / "pcs_meshtastic_gateway.py").read_text(encoding="utf-8")
        service = (ROOT / "systemd" / "pcs-meshtastic.service").read_text(encoding="utf-8")
        setup = (ROOT / "scripts" / "setup-meshtastic-bluetooth.sh").read_text(encoding="utf-8")

        self.assertIn("from meshtastic.serial_interface import SerialInterface", gateway)
        self.assertIn("devPath=self.args.port", gateway)
        self.assertIn('"usb-serial" if getattr(self.args, "port", "") else "bluetooth-le"', gateway)
        self.assertIn("PrivateDevices=yes", service)
        self.assertIn("BindPaths=-/dev/ttyACM0", service)
        self.assertIn("DeviceAllow=/dev/ttyACM0 rw", service)
        self.assertIn("SupplementaryGroups=dialout", service)
        self.assertIn("--configure-usb", setup)
        self.assertIn("--set bluetooth.enabled false", setup)
        self.assertLess(
            setup.index("--set bluetooth.enabled false"),
            setup.index(
                "sudo systemctl enable --now pcs-meshtastic.service",
                setup.index("configure_usb()"),
            ),
        )
        self.assertIn("99-pcs-meshtastic.rules", setup)

    def test_ble_collectors_skip_the_historical_remote_node_database(self):
        status_source = (ROOT / "scripts" / "pcs_meshtastic_status.py").read_text(encoding="utf-8")
        import_source = (ROOT / "scripts" / "pcs_meshtastic_import_mqtt.py").read_text(encoding="utf-8")
        self.assertIn("noNodes=True", status_source)
        self.assertIn("noNodes=True", import_source)

    def test_systemd_service_is_persistent_and_network_scoped(self):
        service = (ROOT / "systemd" / "pcs-meshtastic.service").read_text(encoding="utf-8")
        bluetooth_ready = (ROOT / "systemd" / "pcs-bluetooth-ready.service").read_text(encoding="utf-8")
        bluetooth_ready_script = (ROOT / "scripts" / "pcs-bluetooth-ready.sh").read_text(encoding="utf-8")
        radio_ready_script = (ROOT / "scripts" / "pcs-meshtastic-ready.sh").read_text(encoding="utf-8")
        setup = (ROOT / "scripts" / "setup-meshtastic-bluetooth.sh").read_text(encoding="utf-8")
        self.assertIn("Type=simple", service)
        self.assertIn("Restart=always", service)
        self.assertIn("TimeoutStartSec=150", service)
        self.assertIn("WantedBy=multi-user.target", service)
        self.assertIn("AF_UNIX AF_BLUETOOTH AF_INET AF_INET6", service)
        self.assertIn('meshtastic[cli]==${MESHTASTIC_VERSION}', setup)
        self.assertIn('MESHTASTIC_VERSION="2.7.11"', setup)
        self.assertIn('paho-mqtt==${PAHO_MQTT_VERSION}', setup)
        self.assertIn('PAHO_MQTT_VERSION="2.1.0"', setup)
        self.assertIn('COLLECTOR_TARGET="/usr/local/sbin/pcs_meshtastic_status.py"', setup)
        self.assertIn("--refresh", setup)
        self.assertIn("refresh()", setup)
        self.assertIn("the active gateway did not require a restart", setup)
        self.assertIn("Requires=pcs-bluetooth-ready.service", service)
        self.assertIn("ExecStart=/usr/local/sbin/pcs-bluetooth-ready", bluetooth_ready)
        self.assertIn("BLUETOOTH_READY_TARGET=\"/usr/local/sbin/pcs-bluetooth-ready\"", setup)
        self.assertIn("bluetoothctl power on", bluetooth_ready_script)
        self.assertIn("Powered: yes", bluetooth_ready_script)
        self.assertIn("power_retries", bluetooth_ready_script)
        self.assertIn("/proc/uptime", radio_ready_script)
        self.assertIn("minimum_boot_seconds=110", radio_ready_script)
        self.assertIn("ExecStartPre=/usr/local/sbin/pcs-meshtastic-ready", service)
        self.assertIn("python3-venv rfkill", setup)
        self.assertIn("BT ready unit:", setup)
        self.assertNotIn("pcs-meshtastic.timer", setup)

    def test_downlink_is_empty_by_default_and_requires_explicit_filters(self):
        self.assertEqual(meshtastic_gateway.parse_subscriptions(""), ())
        self.assertEqual(
            meshtastic_gateway.parse_subscriptions("msh/US/2/e/channel/+, msh/US/2/e/PKI/+"),
            ("msh/US/2/e/channel/+", "msh/US/2/e/PKI/+"),
        )
        with self.assertRaises(ValueError):
            meshtastic_gateway.parse_subscriptions("$SYS/#")
        with self.assertRaises(ValueError):
            meshtastic_gateway.parse_subscriptions("msh/#")
        with self.assertRaises(ValueError):
            meshtastic_gateway.parse_subscriptions("msh/US/2/e/+/+")

    def test_proxy_payload_preserves_binary_and_encodes_text(self):
        class ProxyMessage:
            def __init__(self, variant, data=b"", text=""):
                self.variant = variant
                self.data = data
                self.text = text

            def WhichOneof(self, _name):
                return self.variant

        self.assertEqual(
            meshtastic_gateway.proxy_payload(ProxyMessage("data", data=b"\x00\xff")),
            b"\x00\xff",
        )
        self.assertEqual(
            meshtastic_gateway.proxy_payload(ProxyMessage("text", text="hello")),
            b"hello",
        )

    def test_echo_cache_suppresses_one_recent_copy_only(self):
        cache = meshtastic_gateway.EchoCache(ttl_seconds=10)
        cache.remember("msh/test", b"packet", now=100)
        self.assertTrue(cache.consume("msh/test", b"packet", now=101))
        self.assertFalse(cache.consume("msh/test", b"packet", now=102))
        cache.remember("msh/test", b"packet", now=200)
        self.assertFalse(cache.consume("msh/test", b"packet", now=211))

    def test_gateway_relays_proxy_payloads_without_decoding_or_rebroadcast_loop(self):
        class PublishInfo:
            rc = 0

        class FakeClient:
            def __init__(self, *_args, **_kwargs):
                self.published = []

            def reconnect_delay_set(self, **_kwargs):
                pass

            def publish(self, topic, payload, qos, retain):
                self.published.append((topic, payload, qos, retain))
                return PublishInfo()

        class FakeMqtt:
            MQTT_ERR_SUCCESS = 0
            MQTTv311 = 4

            class CallbackAPIVersion:
                VERSION2 = 2

            Client = FakeClient

        class FakePub:
            pass

        class FakeInterface:
            def __init__(self):
                self.sent = []

            def sendMqttClientProxyMessage(self, topic, payload):
                self.sent.append((topic, payload))

        class ProxyMessage:
            topic = "msh/US/2/e/channel/!gateway"
            data = b"\x01\x02service-envelope"
            text = ""
            retained = False

            @staticmethod
            def WhichOneof(_name):
                return "data"

        args = SimpleNamespace(
            mqtt_client_id="pcs-test",
            mqtt_username="",
            mqtt_password="",
            mqtt_tls=False,
            mqtt_ca_file="",
            mqtt_subscriptions=(),
            status_file="unused.json",
        )
        gateway = meshtastic_gateway.Gateway(args, FakeMqtt, FakePub())
        interface = FakeInterface()
        gateway.interface = interface
        gateway.ble_connected = True
        gateway.mqtt_connected = True

        gateway._on_proxy_message(ProxyMessage(), interface)
        gateway._drain()
        self.assertEqual(
            gateway.client.published,
            [(ProxyMessage.topic, ProxyMessage.data, 0, False)],
        )

        echoed = SimpleNamespace(topic=ProxyMessage.topic, payload=ProxyMessage.data)
        gateway._on_mqtt_message(None, None, echoed)
        gateway._drain()
        self.assertEqual(interface.sent, [])
        self.assertEqual(gateway.counts["echoes_suppressed"], 1)

        inbound = SimpleNamespace(topic="msh/US/2/e/channel/!remote", payload=b"other")
        gateway._on_mqtt_message(None, None, inbound)
        gateway._drain()
        self.assertEqual(interface.sent, [(inbound.topic, b"other")])

    def test_gateway_ble_close_is_bounded(self):
        class FakeClient:
            def __init__(self, *_args, **_kwargs):
                pass

            def reconnect_delay_set(self, **_kwargs):
                pass

        class FakeMqtt:
            MQTTv311 = 4

            class CallbackAPIVersion:
                VERSION2 = 2

            Client = FakeClient

        class BlockingInterface:
            def close(self):
                import time

                time.sleep(1)

        args = SimpleNamespace(
            mqtt_client_id="pcs-test",
            mqtt_username="",
            mqtt_password="",
            mqtt_tls=False,
            mqtt_ca_file="",
            mqtt_subscriptions=(),
            status_file="unused.json",
            ble_close_timeout=0.01,
        )
        gateway = meshtastic_gateway.Gateway(args, FakeMqtt, SimpleNamespace())
        gateway.interface = BlockingInterface()
        gateway.ble_connected = True
        gateway._close_ble()
        self.assertIsNone(gateway.interface)
        self.assertFalse(gateway.ble_connected)

    def test_gateway_releases_only_configured_stale_bluez_link(self):
        class FakeClient:
            def __init__(self, *_args, **_kwargs):
                pass

            def reconnect_delay_set(self, **_kwargs):
                pass

        class FakeMqtt:
            MQTTv311 = 4

            class CallbackAPIVersion:
                VERSION2 = 2

            Client = FakeClient

        args = SimpleNamespace(
            device="AA:BB:CC:DD:EE:FF",
            mqtt_client_id="pcs-test",
            mqtt_username="",
            mqtt_password="",
            mqtt_tls=False,
            mqtt_ca_file="",
            mqtt_subscriptions=(),
            status_file="unused.json",
        )
        gateway = meshtastic_gateway.Gateway(args, FakeMqtt, SimpleNamespace())
        with mock.patch.object(meshtastic_gateway.subprocess, "run") as run:
            gateway._disconnect_stale_ble()

        run.assert_called_once_with(
            ["bluetoothctl", "disconnect", args.device],
            check=False,
            stdout=meshtastic_gateway.subprocess.DEVNULL,
            stderr=meshtastic_gateway.subprocess.DEVNULL,
            timeout=10,
        )

    def test_gateway_constructs_ble_interface_on_main_thread(self):
        class FakeClient:
            def __init__(self, *_args, **_kwargs):
                pass

            def reconnect_delay_set(self, **_kwargs):
                pass

        class FakeMqtt:
            MQTTv311 = 4

            class CallbackAPIVersion:
                VERSION2 = 2

            Client = FakeClient

        args = SimpleNamespace(
            device="AA:BB:CC:DD:EE:FF",
            mqtt_client_id="pcs-test",
            mqtt_username="",
            mqtt_password="",
            mqtt_tls=False,
            mqtt_ca_file="",
            mqtt_subscriptions=(),
            status_file="unused.json",
        )
        gateway = meshtastic_gateway.Gateway(args, FakeMqtt, SimpleNamespace())
        current_thread = meshtastic_gateway.threading.current_thread()
        interface = SimpleNamespace(close=lambda: None)

        def open_ble():
            self.assertIs(meshtastic_gateway.threading.current_thread(), current_thread)
            return interface

        with mock.patch.object(gateway, "_open_ble", side_effect=open_ble):
            self.assertEqual(gateway._start_ble_connect(), "connected")
        self.assertIs(gateway.interface, interface)
        self.assertTrue(gateway.ble_connected)

    def test_post_gatt_failure_restarts_process_to_release_client_threads(self):
        source = (ROOT / "scripts" / "pcs_meshtastic_gateway.py").read_text(encoding="utf-8")
        self.assertIn('if connect_result != "device-not-found":', source)
        self.assertIn("Restarting gateway process after radio handshake failure", source)

    def test_gateway_writes_connecting_status_before_blocking_ble_startup(self):
        source = (ROOT / "scripts" / "pcs_meshtastic_gateway.py").read_text(encoding="utf-8")
        self.assertIn('self.last_error = "ble-connecting"\n        self._write_status()', source)

    def test_mqtt_connect_does_not_hide_ble_connecting_state(self):
        class FakeClient:
            def __init__(self, *_args, **_kwargs):
                self.subscribed = []

            def reconnect_delay_set(self, **_kwargs):
                pass

            def subscribe(self, topic, qos):
                self.subscribed.append((topic, qos))
                return 0, 1

        class FakeMqtt:
            MQTT_ERR_SUCCESS = 0
            MQTTv311 = 4

            class CallbackAPIVersion:
                VERSION2 = 2

            Client = FakeClient

        args = SimpleNamespace(
            device="AA:BB:CC:DD:EE:FF",
            mqtt_client_id="pcs-test",
            mqtt_username="",
            mqtt_password="",
            mqtt_tls=False,
            mqtt_ca_file="",
            mqtt_subscriptions=("msh/US/OH/2/e/LongFast/+",),
            status_file="unused.json",
        )
        gateway = meshtastic_gateway.Gateway(args, FakeMqtt, SimpleNamespace())
        gateway.last_error = "ble-connecting"
        gateway._on_mqtt_connect(gateway.client, None, None, 0, None)

        self.assertTrue(gateway.mqtt_connected)
        self.assertEqual(gateway.last_error, "ble-connecting")

    def test_setup_keeps_broker_secrets_out_of_arguments_and_root_only(self):
        setup = (ROOT / "scripts" / "setup-meshtastic-bluetooth.sh").read_text(encoding="utf-8")
        service = (ROOT / "systemd" / "pcs-meshtastic.service").read_text(encoding="utf-8")
        self.assertIn('MQTT_SECRET_FILE="/etc/pcs/meshtastic-mqtt.env"', setup)
        self.assertIn('-m 0600 "${secret_temp}" "${MQTT_SECRET_FILE}"', setup)
        self.assertNotIn("MQTT_PASSWORD=\"$", setup)
        self.assertIn("EnvironmentFile=-/etc/pcs/meshtastic-mqtt.env", service)
        self.assertIn("--import-radio-mqtt", setup)
        self.assertIn('"${IMPORT_TARGET}" --device "${device}" --output "${credential_temp}"', setup)

    def test_mqtt_import_quotes_environment_values_without_printing_secrets(self):
        self.assertEqual(meshtastic_import_mqtt.quote_environment_value("mesh"), '"mesh"')
        self.assertEqual(
            meshtastic_import_mqtt.quote_environment_value('a\\b"c'),
            '"a\\\\b\\"c"',
        )
        with self.assertRaises(ValueError):
            meshtastic_import_mqtt.quote_environment_value("bad\nvalue")

        source = (ROOT / "scripts" / "pcs_meshtastic_import_mqtt.py").read_text(encoding="utf-8")
        self.assertNotIn("print(mqtt.password", source)
        self.assertNotIn("print(mqtt.username", source)

    def test_gpsd_reader_requires_valid_tpv_fix(self):
        class FakeSocket:
            def __init__(self):
                self.chunks = [
                    b'{"class":"VERSION"}\n'
                    b'{"class":"TPV","mode":1}\n'
                    b'{"class":"TPV","mode":3,"lat":41.5,"lon":-81.7,"altHAE":245.6}\n'
                ]

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def settimeout(self, _timeout):
                pass

            def sendall(self, payload):
                self.payload = payload

            def recv(self, _size):
                return self.chunks.pop(0) if self.chunks else b""

        fake_socket = FakeSocket()
        with mock.patch.object(
            meshtastic_gateway.socket,
            "create_connection",
            return_value=fake_socket,
        ):
            position = meshtastic_gateway.read_gpsd_position(timeout=1)

        self.assertEqual(position, (41.5, -81.7, 246))
        self.assertIn(b"WATCH", fake_socket.payload)

    def test_gpsd_position_update_uses_radio_api_without_storing_coordinates(self):
        gateway = meshtastic_gateway.Gateway.__new__(meshtastic_gateway.Gateway)
        gateway.args = SimpleNamespace(
            gpsd_host="127.0.0.1",
            gpsd_port=2947,
            gpsd_timeout=3.0,
            position_channel=0,
        )
        gateway.interface = mock.Mock()
        gateway.counts = {"position_updates": 0, "gpsd_failures": 0}
        gateway.last_position_update = None

        with mock.patch.object(
            meshtastic_gateway,
            "read_gpsd_position",
            return_value=(41.5, -81.7, 246),
        ):
            gateway._send_gpsd_position()

        gateway.interface.sendPosition.assert_called_once_with(
            latitude=41.5,
            longitude=-81.7,
            altitude=246,
            channelIndex=0,
        )
        self.assertEqual(gateway.counts["position_updates"], 1)
        self.assertIsNotNone(gateway.last_position_update)


if __name__ == "__main__":
    unittest.main()
