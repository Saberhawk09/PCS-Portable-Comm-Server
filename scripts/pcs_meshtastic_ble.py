#!/usr/bin/env python3
"""Reliable Meshtastic BLE transport for the headless PCS gateway.

Meshtastic Python 2.7.11 starts its BLE receive worker before the GATT client
exists and does not subscribe to FromNum until after configuration completes.
On BlueZ this can leave the initial FromRadio queue unread until the library's
configuration timeout expires.  PCS drains that initial queue synchronously,
then uses a notification-driven worker for the persistent session.
"""

from __future__ import annotations

import atexit
import logging
import threading
import time
from typing import Any, Optional

from bleak.exc import BleakDBusError, BleakError
from meshtastic.ble_interface import (
    BLEClient,
    BLEInterface,
    FROMNUM_UUID,
    FROMRADIO_UUID,
    LEGACY_LOGRADIO_UUID,
    LOGRADIO_UUID,
    TORADIO_UUID,
)
from meshtastic.mesh_interface import MeshInterface


LOG = logging.getLogger(__name__)


class PCSBLEInterface(BLEInterface):
    """BLEInterface with deterministic startup and bounded shutdown."""

    def __init__(
        self,
        address: Optional[str],
        noProto: bool = False,
        debugOut: Any = None,
        noNodes: bool = False,
        timeout: int = 300,
    ) -> None:
        MeshInterface.__init__(
            self,
            debugOut=debugOut,
            noProto=noProto,
            noNodes=noNodes,
            timeout=timeout,
        )
        self.client: Optional[BLEClient] = None
        self.should_read = False  # Retain compatibility with BLEInterface callers.
        self._want_receive = True
        self._receive_wake = threading.Event()
        self._receiveThread: Optional[threading.Thread] = None
        self._configuring = True
        self._draining = False
        self._closed = False
        self._close_lock = threading.Lock()
        self._exit_handler: Any = None
        self._startup_timeout = min(max(float(timeout), 1.0), 60.0)

        try:
            self.client = self.connect(address)

            if self.client.has_characteristic(LEGACY_LOGRADIO_UUID):
                self.client.start_notify(
                    LEGACY_LOGRADIO_UUID,
                    self.legacy_log_radio_handler,
                )
            if self.client.has_characteristic(LOGRADIO_UUID):
                self.client.start_notify(LOGRADIO_UUID, self.log_radio_handler)

            # Arm notification delivery before requesting configuration.  The
            # upstream 2.7.11 client does this after waiting for configuration.
            self.client.start_notify(FROMNUM_UUID, self.from_num_handler)
            LOG.info("Meshtastic BLE protocol configuration starting")
            self._startConfig()
            LOG.info("Meshtastic BLE protocol configuration stream received")
            if not self.noProto:
                LOG.info("Meshtastic BLE connection completion check starting")
                self._waitConnected(timeout=self._startup_timeout)
                LOG.info("Meshtastic BLE local configuration check starting")
                self.waitForConfig()
                LOG.info("Meshtastic BLE local configuration complete")

            self._configuring = False
            self._receiveThread = threading.Thread(
                target=self._receive_from_radio,
                name="PCSBLEReceive",
                daemon=True,
            )
            self._receiveThread.start()
            self._receive_wake.set()
            self._exit_handler = atexit.register(self._disconnect_at_exit)
        except Exception:
            self._close_transport(send_disconnect=False)
            raise

    def connect(self, address: Optional[str] = None) -> BLEClient:
        """Connect without calling close from Bleak's event-loop thread."""

        device = self.find_device(address)
        client = BLEClient(
            device.address,
            disconnected_callback=self._on_ble_disconnect,
        )
        client.connect()
        client.discover()
        return client

    def _on_ble_disconnect(self, _client: Any) -> None:
        """Signal the gateway; cleanup runs outside Bleak's callback thread."""

        self._want_receive = False
        self._receive_wake.set()
        self._disconnected()

    def from_num_handler(self, _characteristic: Any, _data: bytes) -> None:
        self.should_read = True
        self._receive_wake.set()

    def _read_from_radio(self) -> bytes:
        if self.client is None:
            return b""
        try:
            return bytes(self.client.read_gatt_char(FROMRADIO_UUID))
        except BleakDBusError as exc:
            raise BLEInterface.BLEError(
                "Meshtastic BLE device disconnected while reading",
                BLEInterface.BLEError.READ_ERROR,
            ) from exc
        except BleakError as exc:
            raise BLEInterface.BLEError(
                "Error reading Meshtastic BLE transport",
                BLEInterface.BLEError.READ_ERROR,
            ) from exc

    def _drain_available(self, empty_retries: int = 5) -> int:
        """Drain queued protocol records, tolerating short BlueZ propagation."""

        received = 0
        empty = 0
        while self._want_receive and empty <= empty_retries:
            payload = self._read_from_radio()
            if not payload:
                empty += 1
                if empty <= empty_retries:
                    time.sleep(0.1)
                continue
            empty = 0
            received += 1
            self._handleFromRadio(payload)
        return received

    def _drain_startup(self) -> None:
        """Synchronously consume the initial config queue until connected."""

        if self._draining:
            return
        self._draining = True
        deadline = time.monotonic() + self._startup_timeout
        total_received = 0
        try:
            while self._want_receive and not self.isConnected.is_set():
                self._receive_wake.wait(timeout=0.5)
                self._receive_wake.clear()
                self.should_read = False
                total_received += self._drain_available()
                if time.monotonic() >= deadline:
                    LOG.warning(
                        "Meshtastic BLE startup drain timed out after %d record(s)",
                        total_received,
                    )
                    return
            LOG.info(
                "Meshtastic BLE startup drain completed after %d record(s)",
                total_received,
            )
        finally:
            self._draining = False

    def _receive_from_radio(self) -> None:
        """Run the persistent notification-driven receive pump."""

        while self._want_receive:
            # The periodic fallback covers a missed D-Bus notification without
            # creating a busy-poll loop.
            self._receive_wake.wait(timeout=5.0)
            self._receive_wake.clear()
            if not self._want_receive:
                break
            self.should_read = False
            try:
                self._drain_available()
            except Exception as exc:  # The gateway observes isConnected below.
                self.failure = exc
                self._want_receive = False
                self._disconnected()
                LOG.exception("Meshtastic BLE receive pump stopped")

    def _sendToRadioImpl(self, toRadio: Any) -> None:
        payload = toRadio.SerializeToString()
        if not payload or self.client is None or self._closed:
            return
        try:
            self.client.write_gatt_char(TORADIO_UUID, payload, response=True)
        except Exception as exc:
            raise BLEInterface.BLEError(
                "Error writing Meshtastic BLE transport",
                BLEInterface.BLEError.WRITE_ERROR,
            ) from exc

        if self._configuring:
            self._drain_startup()
        else:
            self._receive_wake.set()

    def _disconnect_at_exit(self) -> None:
        client = self.client
        if client is not None:
            client.disconnect()

    def _close_transport(self, send_disconnect: bool) -> None:
        with self._close_lock:
            if self._closed:
                return
            if send_disconnect and self.client is not None:
                try:
                    MeshInterface.close(self)
                except Exception as exc:
                    LOG.warning("Could not send Meshtastic disconnect: %s", exc)
            elif self.heartbeatTimer:
                self.heartbeatTimer.cancel()

            self._closed = True
            self._want_receive = False
            self._receive_wake.set()
            receive_thread = self._receiveThread
            if (
                receive_thread is not None
                and receive_thread is not threading.current_thread()
            ):
                receive_thread.join(timeout=2)
            self._receiveThread = None

            if self._exit_handler is not None:
                try:
                    atexit.unregister(self._exit_handler)
                except Exception:
                    pass
                self._exit_handler = None

            client = self.client
            self.client = None
            if client is not None:
                try:
                    client.disconnect()
                finally:
                    client.close()
            self._disconnected()

    def close(self) -> None:
        self._close_transport(send_disconnect=True)
