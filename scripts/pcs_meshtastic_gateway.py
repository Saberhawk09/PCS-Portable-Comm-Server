#!/usr/bin/env python3
"""Maintain a Meshtastic BLE link and transparently proxy its MQTT traffic."""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import queue
import signal
import ssl
import subprocess
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Sequence

from pcs_meshtastic_status import build_status, classify_error, write_status


LOG = logging.getLogger("pcs-meshtastic-gateway")
PROXY_TOPIC = "meshtastic.mqttclientproxymessage"
DEFAULT_STATUS_FILE = "/var/lib/pcs-meshtastic/status.json"


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None or not value.strip():
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"invalid boolean value: {value!r}")


def parse_subscriptions(value: str | None) -> tuple[str, ...]:
    """Parse an explicit comma-separated MQTT downlink allowlist."""

    if not value:
        return ()
    topics = tuple(topic.strip() for topic in value.split(",") if topic.strip())
    for topic in topics:
        segments = topic.split("/")
        wildcard_is_safe = "#" not in topic and all(
            "+" not in segment or (segment == "+" and index == len(segments) - 1)
            for index, segment in enumerate(segments)
        )
        if (
            topic.startswith("$")
            or "\x00" in topic
            or not wildcard_is_safe
            or len(topic.encode("utf-8")) > 65535
        ):
            raise ValueError(f"unsafe MQTT subscription filter: {topic!r}")
    return topics


def proxy_payload(message: Any) -> bytes:
    """Return the active protobuf payload variant without converting binary data."""

    variant = message.WhichOneof("payload_variant")
    if variant == "data":
        return bytes(message.data)
    if variant == "text":
        return str(message.text).encode("utf-8")
    raise ValueError("Meshtastic MQTT proxy message has no payload")


class EchoCache:
    """Suppress only immediate broker echoes of packets PCS just published."""

    def __init__(self, ttl_seconds: float = 60.0, maximum: int = 512) -> None:
        self.ttl_seconds = ttl_seconds
        self.maximum = maximum
        self._entries: OrderedDict[bytes, float] = OrderedDict()
        self._lock = threading.Lock()

    @staticmethod
    def _key(topic: str, payload: bytes) -> bytes:
        digest = hashlib.sha256()
        digest.update(topic.encode("utf-8"))
        digest.update(b"\0")
        digest.update(payload)
        return digest.digest()

    def remember(self, topic: str, payload: bytes, now: float | None = None) -> None:
        current = time.monotonic() if now is None else now
        key = self._key(topic, payload)
        with self._lock:
            self._expire(current)
            self._entries[key] = current + self.ttl_seconds
            self._entries.move_to_end(key)
            while len(self._entries) > self.maximum:
                self._entries.popitem(last=False)

    def consume(self, topic: str, payload: bytes, now: float | None = None) -> bool:
        current = time.monotonic() if now is None else now
        key = self._key(topic, payload)
        with self._lock:
            self._expire(current)
            return self._entries.pop(key, None) is not None

    def _expire(self, now: float) -> None:
        while self._entries:
            key, expires = next(iter(self._entries.items()))
            if expires > now:
                break
            self._entries.pop(key, None)


class Gateway:
    """Own one persistent BLE session and one reconnecting MQTT session."""

    def __init__(self, args: argparse.Namespace, mqtt_module: Any, pub: Any) -> None:
        self.args = args
        self.mqtt_module = mqtt_module
        self.pub = pub
        self.stop_event = threading.Event()
        self.interface: Any = None
        self.mqtt_connected = False
        self._mqtt_ever_connected = False
        self.ble_connected = False
        self.radio_to_mqtt: queue.Queue[tuple[str, bytes, bool]] = queue.Queue(maxsize=256)
        self.mqtt_to_radio: queue.Queue[tuple[str, bytes]] = queue.Queue(maxsize=256)
        self.echoes = EchoCache()
        self.counts = {
            "mqtt_uplink": 0,
            "mqtt_downlink": 0,
            "echoes_suppressed": 0,
            "queue_drops": 0,
            "ble_reconnects": 0,
            "mqtt_reconnects": 0,
        }
        self.last_error: str | None = None
        self.started = time.time()
        self.client = self._new_mqtt_client()

    def _new_mqtt_client(self) -> Any:
        client = self.mqtt_module.Client(
            self.mqtt_module.CallbackAPIVersion.VERSION2,
            client_id=self.args.mqtt_client_id,
            protocol=self.mqtt_module.MQTTv311,
        )
        client.on_connect = self._on_mqtt_connect
        client.on_disconnect = self._on_mqtt_disconnect
        client.on_message = self._on_mqtt_message
        client.reconnect_delay_set(min_delay=2, max_delay=60)
        if self.args.mqtt_username:
            client.username_pw_set(self.args.mqtt_username, self.args.mqtt_password or None)
        if self.args.mqtt_tls:
            client.tls_set(
                ca_certs=self.args.mqtt_ca_file or None,
                cert_reqs=ssl.CERT_REQUIRED,
            )
        return client

    def _on_mqtt_connect(self, client: Any, _userdata: Any, _flags: Any, reason_code: Any, _properties: Any) -> None:
        reason_value = getattr(reason_code, "value", reason_code)
        if getattr(reason_code, "is_failure", False) or reason_value != 0:
            self.last_error = "mqtt-connection-refused"
            LOG.warning("MQTT broker refused the connection")
            return
        if self._mqtt_ever_connected:
            self.counts["mqtt_reconnects"] += 1
        self._mqtt_ever_connected = True
        self.mqtt_connected = True
        if self.last_error in {"mqtt-connection-refused", "mqtt-disconnected"}:
            self.last_error = None
        for topic in self.args.mqtt_subscriptions:
            result, _mid = client.subscribe(topic, qos=1)
            if result != self.mqtt_module.MQTT_ERR_SUCCESS:
                LOG.warning("MQTT subscription request failed for filter %s", topic)
        LOG.info("MQTT connected; %d downlink filter(s) active", len(self.args.mqtt_subscriptions))

    def _on_mqtt_disconnect(self, _client: Any, _userdata: Any, _flags: Any, _reason_code: Any, _properties: Any) -> None:
        self.mqtt_connected = False
        if not self.stop_event.is_set():
            if self.ble_connected:
                self.last_error = "mqtt-disconnected"
            LOG.warning("MQTT disconnected; automatic reconnect remains active")

    def _on_mqtt_message(self, _client: Any, _userdata: Any, message: Any) -> None:
        topic = str(message.topic)
        payload = bytes(message.payload)
        if self.echoes.consume(topic, payload):
            self.counts["echoes_suppressed"] += 1
            return
        self._put_latest(self.mqtt_to_radio, (topic, payload))

    def _on_proxy_message(self, proxymessage: Any, interface: Any) -> None:
        if interface is not self.interface:
            return
        try:
            payload = proxy_payload(proxymessage)
            topic = str(proxymessage.topic)
            retained = bool(proxymessage.retained)
            if not topic or topic.startswith("$") or "+" in topic or "#" in topic:
                raise ValueError("unsafe MQTT publish topic")
            self._put_latest(self.radio_to_mqtt, (topic, payload, retained))
        except (TypeError, ValueError) as exc:
            self.counts["queue_drops"] += 1
            self.last_error = "invalid-radio-mqtt-message"
            LOG.warning("Rejected malformed MQTT proxy message: %s", exc)

    def _put_latest(self, target: queue.Queue[Any], item: Any) -> None:
        try:
            target.put_nowait(item)
        except queue.Full:
            try:
                target.get_nowait()
            except queue.Empty:
                pass
            target.put_nowait(item)
            self.counts["queue_drops"] += 1

    def _status(self) -> dict[str, Any]:
        if self.interface is not None and self.ble_connected:
            status = build_status(
                self.interface.nodes,
                self.interface.myInfo,
                self.interface.metadata,
            )
        elif self.last_error:
            status = classify_error(RuntimeError(self.last_error))
            status["reason"] = self.last_error
        else:
            status = classify_error(RuntimeError("connection failed"))

        status["transport"] = "usb-serial" if getattr(self.args, "port", "") else "bluetooth-le"

        radio_mqtt: Any = None
        if self.interface is not None:
            local_node = getattr(self.interface, "localNode", None)
            module_config = getattr(local_node, "moduleConfig", None)
            radio_mqtt = getattr(module_config, "mqtt", None)

        status["gateway"] = {
            "mode": "transparent-mqtt-client-proxy",
            "ble_connected": self.ble_connected,
            "mqtt_connected": self.mqtt_connected,
            "downlink_filters": len(self.args.mqtt_subscriptions),
            "radio_mqtt_enabled": getattr(radio_mqtt, "enabled", None),
            "radio_proxy_enabled": getattr(radio_mqtt, "proxy_to_client_enabled", None),
            "started_at_epoch": int(self.started),
            "counters": dict(self.counts),
            "queued": {
                "to_mqtt": self.radio_to_mqtt.qsize(),
                "to_radio": self.mqtt_to_radio.qsize(),
            },
        }
        return status

    def _write_status(self) -> None:
        try:
            write_status(self.args.status_file, self._status())
        except Exception:
            self.last_error = "status-write-failed"
            LOG.exception("Could not update Meshtastic gateway status")

    def _drain(self) -> None:
        if self.mqtt_connected:
            for _ in range(16):
                try:
                    topic, payload, retained = self.radio_to_mqtt.get_nowait()
                except queue.Empty:
                    break
                self.echoes.remember(topic, payload)
                info = self.client.publish(topic, payload, qos=0, retain=retained)
                if info.rc == self.mqtt_module.MQTT_ERR_SUCCESS:
                    self.counts["mqtt_uplink"] += 1
                else:
                    self.echoes.consume(topic, payload)
                    self._put_latest(self.radio_to_mqtt, (topic, payload, retained))
                    self.last_error = "mqtt-publish-failed"
                    break

        if self.ble_connected and self.interface is not None:
            for _ in range(8):
                try:
                    topic, payload = self.mqtt_to_radio.get_nowait()
                except queue.Empty:
                    break
                self.interface.sendMqttClientProxyMessage(topic, payload)
                self.counts["mqtt_downlink"] += 1

    def _close_ble(self) -> None:
        """Bound shutdown so a stuck BlueZ disconnect cannot hang systemd."""

        interface = self.interface
        self.interface = None
        self.ble_connected = False
        if interface is None:
            return

        close_thread = threading.Thread(
            target=interface.close,
            name="BLEClose",
            daemon=True,
        )
        close_thread.start()
        close_thread.join(timeout=float(getattr(self.args, "ble_close_timeout", 5.0)))
        if close_thread.is_alive():
            LOG.warning("Meshtastic BLE close timed out; process exit will release the transport")

    def _disconnect_stale_ble(self) -> None:
        """Release a BlueZ link left behind by a failed BLEInterface startup."""

        if getattr(self.args, "port", ""):
            return

        try:
            subprocess.run(
                ["bluetoothctl", "disconnect", self.args.device],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            LOG.warning("Could not release stale Meshtastic BlueZ connection")

    def _open_ble(self) -> Any:
        if getattr(self.args, "port", ""):
            from meshtastic.serial_interface import SerialInterface

            return SerialInterface(
                devPath=self.args.port,
                noNodes=True,
                timeout=self.args.ble_timeout,
            )

        from pcs_meshtastic_ble import PCSBLEInterface

        # The gateway needs local config and live proxy events, not the radio's
        # historical remote-node database. Large node DBs can exceed the BLE
        # client's fixed configuration timeout and expose data PCS does not
        # retain.
        return PCSBLEInterface(
            self.args.device,
            noNodes=True,
            timeout=self.args.ble_timeout,
        )

    def _start_ble_connect(self) -> str:
        """Run one BLE handshake on the main thread as BlueZ requires here."""

        self._close_ble()
        try:
            interface = self._open_ble()
        except Exception as exc:
            reason = classify_error(exc)["reason"]
            self._disconnect_stale_ble()
            LOG.warning("Meshtastic BLE connection failed (%s)", reason)
            return reason

        self.interface = interface
        self.ble_connected = True
        self.last_error = None
        LOG.info("Meshtastic BLE session established")
        return "connected"

    def run(self) -> int:
        self.pub.subscribe(self._on_proxy_message, PROXY_TOPIC)
        self.client.connect_async(
            self.args.mqtt_host,
            self.args.mqtt_port,
            keepalive=self.args.mqtt_keepalive,
        )
        self.client.loop_start()
        next_ble_attempt = 0.0
        next_status = 0.0
        self.last_error = "ble-connecting"
        self._write_status()
        try:
            while not self.stop_event.is_set():
                now = time.monotonic()
                connected_event = getattr(self.interface, "isConnected", None)
                if self.ble_connected and connected_event is not None and not connected_event.is_set():
                    self.ble_connected = False
                    self.last_error = "ble-disconnected"
                    self.counts["ble_reconnects"] += 1
                    next_ble_attempt = now
                    LOG.warning("Meshtastic BLE disconnected; reconnecting")

                if (
                    not self.ble_connected
                    and now >= next_ble_attempt
                ):
                    self.last_error = "ble-connecting"
                    self._write_status()
                    connect_result = self._start_ble_connect()
                    if connect_result != "connected":
                        self.last_error = connect_result
                        if connect_result != "device-not-found":
                            # A fresh process ensures that a failed Bleak event
                            # loop cannot poison the next BlueZ attempt.
                            LOG.warning("Restarting gateway process after BLE handshake failure")
                            break
                        next_ble_attempt = time.monotonic() + self.args.ble_retry_seconds

                if self.ble_connected:
                    try:
                        self._drain()
                    except Exception:
                        self.ble_connected = False
                        self.last_error = "ble-transport-failed"
                        self.counts["ble_reconnects"] += 1
                        self._close_ble()
                        self._disconnect_stale_ble()
                        next_ble_attempt = now + self.args.ble_retry_seconds
                        LOG.exception("Meshtastic BLE transport failed")

                if now >= next_status:
                    self._write_status()
                    next_status = now + self.args.status_interval
                self.stop_event.wait(0.2)
        finally:
            self.client.disconnect()
            self.client.loop_stop()
            self._close_ble()
            self.mqtt_connected = False
            self.last_error = "service-stopped"
            self._write_status()
            self.pub.unsubscribe(self._on_proxy_message, PROXY_TOPIC)
        return 0

    def stop(self, *_args: Any) -> None:
        self.stop_event.set()


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", default=os.environ.get("PCS_MESHTASTIC_DEVICE", ""))
    parser.add_argument("--port", default=os.environ.get("PCS_MESHTASTIC_PORT", ""))
    parser.add_argument("--status-file", default=os.environ.get("PCS_MESHTASTIC_STATUS_FILE", DEFAULT_STATUS_FILE))
    parser.add_argument("--mqtt-host", default=os.environ.get("PCS_MESHTASTIC_MQTT_HOST", ""))
    parser.add_argument("--mqtt-port", type=int, default=int(os.environ.get("PCS_MESHTASTIC_MQTT_PORT", "1883")))
    parser.add_argument("--mqtt-client-id", default=os.environ.get("PCS_MESHTASTIC_MQTT_CLIENT_ID", "pcs-meshtastic"))
    parser.add_argument("--mqtt-username", default=os.environ.get("PCS_MESHTASTIC_MQTT_USERNAME", ""))
    parser.add_argument("--mqtt-password", default=os.environ.get("PCS_MESHTASTIC_MQTT_PASSWORD", ""))
    parser.add_argument("--mqtt-ca-file", default=os.environ.get("PCS_MESHTASTIC_MQTT_CA_FILE", ""))
    parser.add_argument("--mqtt-tls", action="store_true", default=_bool(os.environ.get("PCS_MESHTASTIC_MQTT_TLS")))
    parser.add_argument("--mqtt-subscriptions", type=parse_subscriptions, default=parse_subscriptions(os.environ.get("PCS_MESHTASTIC_MQTT_SUBSCRIPTIONS")))
    parser.add_argument("--mqtt-keepalive", type=int, default=60)
    parser.add_argument("--ble-timeout", type=int, default=60)
    parser.add_argument("--ble-retry-seconds", type=int, default=15)
    parser.add_argument("--status-interval", type=int, default=10)
    args = parser.parse_args(argv)
    if not args.device.strip() and not args.port.strip():
        parser.error("PCS_MESHTASTIC_DEVICE or PCS_MESHTASTIC_PORT is required")
    if not args.mqtt_host.strip():
        parser.error("PCS_MESHTASTIC_MQTT_HOST is required")
    if not 1 <= args.mqtt_port <= 65535:
        parser.error("MQTT port must be between 1 and 65535")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args(argv)
    import paho.mqtt.client as mqtt
    from pubsub import pub

    Path(args.status_file).parent.mkdir(parents=True, exist_ok=True)
    gateway = Gateway(args, mqtt, pub)
    signal.signal(signal.SIGTERM, gateway.stop)
    signal.signal(signal.SIGINT, gateway.stop)
    return gateway.run()


if __name__ == "__main__":
    raise SystemExit(main())
