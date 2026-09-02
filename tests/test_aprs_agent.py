import importlib.util
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).parents[1]
AGENT_PATH = ROOT / "scripts" / "pcs_aprs_agent.py"
SETUP_PATH = ROOT / "scripts" / "setup-pcs-aprs-agent.sh"
SERVICE_PATH = ROOT / "systemd" / "pcs-aprs-agent.service"
CONFIG_EXAMPLE_PATH = ROOT / "config" / "aprs-agent.example.conf"
INSTALL_EXAMPLE_PATH = ROOT / "config" / "pcs-install.example.conf"
DIREWOLF_SETUP_PATH = ROOT / "scripts" / "setup-direwolf-aprs.sh"
DOC_PATH = ROOT / "docs" / "aprs-agent.md"
SPEC = importlib.util.spec_from_file_location("pcs_aprs_agent", AGENT_PATH)
assert SPEC and SPEC.loader
pcs_aprs_agent = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = pcs_aprs_agent
SPEC.loader.exec_module(pcs_aprs_agent)


class FakeProvider:
    def __init__(self):
        self.commands = []

    def execute(self, command):
        self.commands.append(command)
        return f"RESULT {command}"


class FakeRunner:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def run(self, arguments, timeout):
        self.calls.append((arguments, timeout))
        return self.results.pop(0)


class FakeGpsSocket:
    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.sent = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def settimeout(self, _timeout):
        return None

    def sendall(self, value):
        self.sent.append(value)

    def recv(self, _size):
        return self.chunks.pop(0) if self.chunks else b""


def inbound_frame(sender="W8IJC-7", addressee="W8IJC-10", body="PING", message_id="42"):
    suffix = f"{{{message_id}" if message_id is not None else ""
    information = f":{addressee:<9}:{body}{suffix}".encode("ascii")
    return pcs_aprs_agent.encode_ax25_ui(sender, "APRS", information)


def ichannel_frame(sender="W8IJC-7", addressee="W8IJC-10", body="PING", message_id="42"):
    suffix = f"{{{message_id}" if message_id is not None else ""
    packet = (
        f"}}{sender}>APY03D,WIDE1-1,qAR,W8IJC-1:"
        f":{addressee:<9}:{body}{suffix}"
    ).encode("ascii")
    return pcs_aprs_agent.encode_ax25_ui("X", "X", packet)


class KissAndAx25Tests(unittest.TestCase):
    def test_kiss_round_trip_handles_fragmentation_and_reserved_bytes(self):
        payload = b"before" + bytes([pcs_aprs_agent.FEND, pcs_aprs_agent.FESC]) + b"after"
        encoded = pcs_aprs_agent.encode_kiss(8, payload)
        decoder = pcs_aprs_agent.KissDecoder()

        frames = []
        for byte in encoded:
            frames.extend(decoder.feed(bytes([byte])))

        self.assertEqual([bytes([0x80]) + payload], frames)

    def test_kiss_decoder_discards_invalid_escape_and_oversized_frame(self):
        decoder = pcs_aprs_agent.KissDecoder(maximum=4)
        self.assertEqual([], decoder.feed(bytes([0xC0, 0x80, 0xDB, 0x01, 0xC0])))
        self.assertEqual([], decoder.feed(bytes([0xC0, 0x80, 0xDB, 0xC0])))
        self.assertEqual([], decoder.feed(bytes([0xC0, 1, 2, 3, 4, 5, 0xC0])))
        self.assertEqual([b"ok"], decoder.feed(bytes([0xC0]) + b"ok" + bytes([0xC0])))

    def test_ax25_ui_round_trip_preserves_source_destination_and_information(self):
        raw = pcs_aprs_agent.encode_ax25_ui("W8IJC-10", "APZPCS", b":W8IJC-7 :ack42")
        decoded = pcs_aprs_agent.decode_ax25_ui(raw)

        self.assertEqual("W8IJC-10", decoded.source)
        self.assertEqual("APZPCS", decoded.destination)
        self.assertEqual(b":W8IJC-7 :ack42", decoded.information)

    def test_ax25_rejects_non_ui_frames(self):
        raw = bytearray(pcs_aprs_agent.encode_ax25_ui("W8IJC-7", "APRS", b"hello"))
        raw[14] = 0x13
        with self.assertRaisesRegex(ValueError, "UI frames"):
            pcs_aprs_agent.decode_ax25_ui(bytes(raw))

    def test_aprs_parser_requires_fixed_addressee_field_and_extracts_reply_ack_id(self):
        frame = pcs_aprs_agent.decode_ax25_ui(inbound_frame(body="STATUS", message_id="A1"))
        message = pcs_aprs_agent.parse_aprs_message(frame)

        self.assertEqual("W8IJC-7", message.sender)
        self.assertEqual("W8IJC-10", message.addressee)
        self.assertEqual("STATUS", message.body)
        self.assertEqual("A1", message.message_id)

        reply_ack = pcs_aprs_agent.encode_ax25_ui(
            "W8IJC-7", "APRS", b":W8IJC-10 :PING{B2}A1"
        )
        message = pcs_aprs_agent.parse_aprs_message(pcs_aprs_agent.decode_ax25_ui(reply_ack))
        self.assertEqual("B2", message.message_id)
        self.assertEqual("A1", message.reply_ack_id)
        self.assertEqual("PING", message.body)

    def test_aprs_parser_accepts_terminal_yaesu_carriage_return(self):
        yaesu = pcs_aprs_agent.encode_ax25_ui(
            "W8IJC-7", "APY03D", b":W8IJC-10 :ping{24\r"
        )
        message = pcs_aprs_agent.parse_aprs_message(
            pcs_aprs_agent.decode_ax25_ui(yaesu)
        )

        self.assertEqual("ping", message.body)
        self.assertEqual("24", message.message_id)

        trailing_data = pcs_aprs_agent.encode_ax25_ui(
            "W8IJC-7", "APY03D", b":W8IJC-10 :ping{24\rX"
        )
        message = pcs_aprs_agent.parse_aprs_message(
            pcs_aprs_agent.decode_ax25_ui(trailing_data)
        )
        self.assertIsNone(message.message_id)

    def test_direwolf_ichannel_wrapper_recovers_original_aprs_is_packet(self):
        outer = pcs_aprs_agent.decode_ax25_ui(
            ichannel_frame(body="PING", message_id="11")
        )
        inner = pcs_aprs_agent.unwrap_direwolf_ichannel(outer)
        message = pcs_aprs_agent.parse_aprs_message(inner)

        self.assertEqual("W8IJC-7", inner.source)
        self.assertEqual("APY03D", inner.destination)
        self.assertEqual("W8IJC-7", message.sender)
        self.assertEqual("W8IJC-10", message.addressee)
        self.assertEqual("PING", message.body)
        self.assertEqual("11", message.message_id)

    def test_aprs_receipt_parser_accepts_only_exact_ack_or_rej_bodies(self):
        ack = pcs_aprs_agent.parse_aprs_receipt("ack00A1")
        rejection = pcs_aprs_agent.parse_aprs_receipt("REJ9")

        self.assertEqual(("acked", "00A1"), (ack.disposition, ack.message_id))
        self.assertEqual(("rejected", "9"), (rejection.disposition, rejection.message_id))
        for invalid in ("ack", "ack123456", "ack1 extra", " ack1", "xack1", "PING"):
            self.assertIsNone(pcs_aprs_agent.parse_aprs_receipt(invalid))

    def test_direwolf_ichannel_wrapper_rejects_malformed_or_nested_tnc2(self):
        malformed = pcs_aprs_agent.Ax25Frame("X", "X", b"}not-a-packet")
        nested = pcs_aprs_agent.Ax25Frame("X", "X", b"}W8IJC-7>APRS:}nested")

        with self.assertRaisesRegex(ValueError, "TNC2 framing"):
            pcs_aprs_agent.unwrap_direwolf_ichannel(malformed)
        with self.assertRaisesRegex(ValueError, "nested data"):
            pcs_aprs_agent.unwrap_direwolf_ichannel(nested)


class StoreAndAgentTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "state.sqlite3"
        self.store = pcs_aprs_agent.DedupStore(self.db_path, 3600, now=lambda: 10_000)
        self.provider = FakeProvider()
        self.config = pcs_aprs_agent.AgentConfig(state_db=str(self.db_path))
        self.limiter = pcs_aprs_agent.SlidingWindowLimiter(12, 60, now=lambda: 500)
        self.agent = pcs_aprs_agent.AprsAgent(self.config, self.store, self.provider, self.limiter)

    def tearDown(self):
        self.store.close()
        self.temp_dir.cleanup()

    def process(self, frame, channel=8):
        sent = []
        self.agent.process(frame, channel, lambda outbound_channel, raw: sent.append((outbound_channel, raw)))
        self.sent_channels = [outbound_channel for outbound_channel, _raw in sent]
        return [pcs_aprs_agent.decode_ax25_ui(raw) for _outbound_channel, raw in sent]

    def test_addressed_command_is_acked_before_numbered_reply(self):
        sent = self.process(inbound_frame(body="status", message_id="42"))

        self.assertEqual(2, len(sent))
        self.assertEqual("W8IJC-10", sent[0].source)
        self.assertEqual(b":W8IJC-7  :ack42", sent[0].information)
        self.assertRegex(sent[1].information.decode("ascii"), r"^:W8IJC-7  :RESULT STATUS\{[0-9A-Z]{4}$")
        self.assertEqual(["STATUS"], self.provider.commands)
        self.assertEqual(1, self.store.outbound_summary()["pending"])

    def test_only_matching_sender_can_ack_an_outbound_message(self):
        sent = self.process(inbound_frame(body="PING", message_id="42"))
        outbound_id = sent[1].information.decode("ascii").rsplit("{", 1)[1]

        self.assertEqual([], self.process(inbound_frame("N0CALL", body=f"ack{outbound_id}", message_id=None)))
        self.assertEqual(1, self.store.outbound_summary()["pending"])
        self.assertEqual([], self.process(inbound_frame(body=f"ack{outbound_id}", message_id=None)))

        summary = self.store.outbound_summary(include_messages=True)
        self.assertEqual(0, summary["pending"])
        self.assertEqual(1, summary["acked"])
        self.assertEqual("W8IJC-7", summary["messages"][0]["recipient"])
        self.assertEqual(1, summary["messages"][0]["attempts"])

    def test_rejection_and_duplicate_receipt_are_terminal_without_a_reply(self):
        sent = self.process(inbound_frame(body="STATUS", message_id="43"))
        outbound_id = sent[1].information.decode("ascii").rsplit("{", 1)[1]

        self.assertEqual([], self.process(inbound_frame(body=f"rej{outbound_id}", message_id=None)))
        self.assertEqual([], self.process(inbound_frame(body=f"ack{outbound_id}", message_id=None)))

        summary = self.store.outbound_summary()
        self.assertEqual(1, summary["rejected"])
        self.assertEqual(0, summary["acked"])

    def test_reply_ack_extension_completes_prior_message_and_processes_new_command(self):
        first = self.process(inbound_frame(body="PING", message_id="41"))
        outbound_id = first[1].information.decode("ascii").rsplit("{", 1)[1]
        reply_ack = pcs_aprs_agent.encode_ax25_ui(
            "W8IJC-7",
            "APRS",
            f":{'W8IJC-10':<9}:STATUS{{42}}{outbound_id}".encode("ascii"),
        )

        second = self.process(reply_ack)
        summary = self.store.outbound_summary()

        self.assertEqual(b":W8IJC-7  :ack42", second[0].information)
        self.assertEqual(["PING", "STATUS"], self.provider.commands)
        self.assertEqual(1, summary["acked"])
        self.assertEqual(1, summary["pending"])

    def test_due_messages_retry_on_schedule_and_fail_after_bounded_attempts(self):
        clock = [100.0]
        self.store.close()
        self.store = pcs_aprs_agent.DedupStore(self.db_path, 3600, now=lambda: clock[0])
        self.config = pcs_aprs_agent.AgentConfig(
            state_db=str(self.db_path),
            outbound_retry_seconds=(5, 10),
        )
        self.agent = pcs_aprs_agent.AprsAgent(
            self.config, self.store, self.provider, self.limiter,
        )
        initial = self.process(inbound_frame(body="PING", message_id="44"))
        outbound_id = initial[1].information.decode("ascii").rsplit("{", 1)[1]
        retried = []
        send_retry = lambda channel, frame: retried.append((channel, frame))

        clock[0] = 104.0
        self.assertEqual(0, self.agent.retry_due(send_retry))
        clock[0] = 105.0
        self.assertEqual(1, self.agent.retry_due(send_retry))
        clock[0] = 114.0
        self.assertEqual(0, self.agent.retry_due(send_retry))
        clock[0] = 115.0
        self.assertEqual(1, self.agent.retry_due(send_retry))

        summary = self.store.outbound_summary(include_messages=True)
        self.assertEqual(2, len(retried))
        self.assertEqual(3, summary["messages"][0]["attempts"])
        self.assertEqual(1, summary["failed"])

        self.process(inbound_frame(body=f"ack{outbound_id}", message_id=None))
        self.assertEqual(1, self.store.outbound_summary()["acked"])

    def test_failed_socket_send_leaves_message_due_without_counting_an_attempt(self):
        outbound = self.store.queue_outbound("W8IJC-7", "TEST", 100, 8)

        with self.assertRaisesRegex(OSError, "send failed"):
            self.agent._transmit_outbound(
                outbound,
                mock.Mock(side_effect=OSError("send failed")),
            )

        summary = self.store.outbound_summary(include_messages=True)
        self.assertEqual(0, summary["messages"][0]["attempts"])
        self.assertEqual(1, summary["pending"])
        self.assertEqual(1, len(self.store.due_outbound()))

    def test_pending_retry_state_survives_database_reopen(self):
        outbound = self.store.queue_outbound("W8IJC-7", "TEST", 100, 8)
        self.agent._transmit_outbound(outbound, lambda _channel, _frame: None)
        self.store.close()

        reopened = pcs_aprs_agent.DedupStore(self.db_path, 3600, now=lambda: 10_031)
        self.store = reopened
        due = reopened.due_outbound()

        self.assertEqual(1, len(due))
        self.assertEqual(outbound.message_id, due[0].message_id)
        self.assertEqual(1, due[0].attempts)
        self.assertEqual(8, due[0].kiss_channel)

    def test_rf_channel_is_opt_in_and_replies_on_the_ingress_channel(self):
        self.assertEqual([], self.process(inbound_frame(body="PING", message_id="45"), channel=0))

        self.config = pcs_aprs_agent.AgentConfig(
            state_db=str(self.db_path),
            rf_enabled=True,
        )
        self.agent = pcs_aprs_agent.AprsAgent(
            self.config, self.store, self.provider, self.limiter,
        )
        sent = self.process(inbound_frame(body="PING", message_id="46"), channel=0)

        self.assertEqual(2, len(sent))
        self.assertEqual([0, 0], self.sent_channels)
        queued = self.store.outbound_summary(include_messages=True)["messages"]
        self.assertEqual(0, queued[0]["kiss_channel"])

    def test_runtime_preserves_rf_channel_in_kiss_ack_and_reply_frames(self):
        self.config = pcs_aprs_agent.AgentConfig(
            state_db=str(self.db_path),
            rf_enabled=True,
        )
        self.agent = pcs_aprs_agent.AprsAgent(
            self.config, self.store, self.provider, self.limiter,
        )
        runtime = pcs_aprs_agent.AgentRuntime(self.config, self.agent)
        connection = FakeGpsSocket([
            pcs_aprs_agent.encode_kiss(
                0,
                inbound_frame(body="PING", message_id="47"),
            )
        ])

        with self.assertRaisesRegex(ConnectionError, "closed"):
            runtime._serve_connection(connection)

        decoder = pcs_aprs_agent.KissDecoder()
        frames = [decoder.feed(item)[0] for item in connection.sent]
        self.assertEqual([0, 0], [frame[0] >> 4 for frame in frames])

    def test_aprs_is_ichannel_command_is_acked_and_replied_to(self):
        sent = self.process(ichannel_frame(body="PING", message_id="11"))

        self.assertEqual(2, len(sent))
        self.assertEqual(b":W8IJC-7  :ack11", sent[0].information)
        self.assertRegex(sent[1].information.decode("ascii"), r"^:W8IJC-7  :RESULT PING\{[0-9A-Z]{4}$")
        self.assertEqual(["PING"], self.provider.commands)

    def test_short_status_and_help_aliases_execute_canonical_commands(self):
        status = self.process(inbound_frame(body="s", message_id="51"))
        help_reply = self.process(inbound_frame(body="H", message_id="52"))

        self.assertRegex(status[1].information.decode("ascii"), r"RESULT STATUS\{")
        self.assertRegex(help_reply[1].information.decode("ascii"), r"RESULT HELP\{")
        self.assertEqual(["STATUS", "HELP"], self.provider.commands)

    def test_msg_command_stores_bounded_mailbox_entry_once(self):
        first = self.process(inbound_frame(body="MSG Bring Water At 1800", message_id="61"))
        duplicate = self.process(inbound_frame(body="MSG Bring Water At 1800", message_id="61"))
        mailbox = self.store.mailbox_summary(include_messages=True)

        self.assertEqual(2, len(first))
        self.assertIn(b":MESSAGE STORED{", first[1].information)
        self.assertEqual(1, len(duplicate))
        self.assertEqual(1, mailbox["total"])
        self.assertEqual(1, mailbox["unread"])
        self.assertEqual("Bring Water At 1800", mailbox["messages"][0]["body"])
        self.assertEqual([], self.provider.commands)

        self.assertEqual(1, self.store.mark_mailbox_read())
        self.assertEqual(0, self.store.mailbox_summary()["unread"])

    def test_mailbox_accepts_a_reused_aprs_id_after_dedupe_expiry(self):
        clock = [100.0]
        self.store.close()
        self.store = pcs_aprs_agent.DedupStore(self.db_path, 60, now=lambda: clock[0])
        self.agent.store = self.store
        self.process(inbound_frame(body="MSG First note", message_id="5"))
        clock[0] = 161.0
        self.process(inbound_frame(body="MSG Second note", message_id="5"))
        messages = self.store.mailbox_summary(include_messages=True)["messages"]

        self.assertEqual(2, len(messages))
        self.assertEqual(["Second note", "First note"], [item["body"] for item in messages])

    def test_mailbox_limit_removes_oldest_entry_first(self):
        for number in range(3):
            self.store.store_mailbox_message(
                "W8IJC-7", str(number), f"Note {number}", limit=2,
            )
        messages = self.store.mailbox_summary(include_messages=True)["messages"]
        self.assertEqual(2, len(messages))
        self.assertEqual(["Note 2", "Note 1"], [item["body"] for item in messages])

    def test_empty_msg_and_unknown_command_have_unambiguous_replies(self):
        empty = self.process(inbound_frame(body="MSG", message_id="62"))
        unknown = self.process(inbound_frame(body="TEST", message_id="63"))

        self.assertIn(b":MSG FORMAT: MSG <TEXT>{", empty[1].information)
        self.assertIn(b":RESULT TEST{", unknown[1].information)

        provider = pcs_aprs_agent.StatusProvider()
        self.assertEqual(
            "COMMAND UNKNOWN | COMMAND LIST: HELP",
            provider.execute("TEST"),
        )

    def test_duplicate_is_acked_again_but_not_executed_or_replied_again(self):
        first = self.process(inbound_frame(body="PING", message_id="77"))
        second = self.process(inbound_frame(body="PING", message_id="77"))

        self.assertEqual(2, len(first))
        self.assertEqual(1, len(second))
        self.assertEqual(b":W8IJC-7  :ack77", second[0].information)
        self.assertEqual(["PING"], self.provider.commands)

    def test_duplicate_seen_on_rf_and_aprs_is_is_acked_on_each_ingress_only(self):
        self.config = pcs_aprs_agent.AgentConfig(
            state_db=str(self.db_path),
            rf_enabled=True,
        )
        self.agent = pcs_aprs_agent.AprsAgent(
            self.config, self.store, self.provider, self.limiter,
        )

        first = self.process(inbound_frame(body="PING", message_id="78"), channel=0)
        first_channels = list(self.sent_channels)
        second = self.process(inbound_frame(body="PING", message_id="78"), channel=8)

        self.assertEqual(2, len(first))
        self.assertEqual([0, 0], first_channels)
        self.assertEqual(1, len(second))
        self.assertEqual([8], self.sent_channels)
        self.assertEqual(b":W8IJC-7  :ack78", second[0].information)
        self.assertEqual(["PING"], self.provider.commands)

    def test_reused_id_with_different_body_is_acked_but_not_executed(self):
        self.process(inbound_frame(body="PING", message_id="77"))
        second = self.process(inbound_frame(body="STATUS", message_id="77"))

        self.assertEqual(1, len(second))
        self.assertEqual(b":W8IJC-7  :ack77", second[0].information)
        self.assertEqual(["PING"], self.provider.commands)

    def test_other_addressee_unnumbered_and_self_sourced_messages_are_ignored(self):
        self.assertEqual([], self.process(inbound_frame(addressee="N0CALL", message_id="1")))
        self.assertEqual([], self.process(inbound_frame(message_id=None)))
        self.assertEqual([], self.process(inbound_frame(sender="W8IJC-10", message_id="2")))
        self.assertEqual([], self.provider.commands)

    def test_deduplication_and_outbound_sequence_survive_restart(self):
        self.assertEqual("new", self.store.claim("W8IJC-7", "12", "PING"))
        self.assertEqual("0000", self.store.next_outbound_id())
        self.store.close()

        reopened = pcs_aprs_agent.DedupStore(self.db_path, 3600, now=lambda: 10_001)
        self.store = reopened
        self.assertEqual("duplicate", reopened.claim("W8IJC-7", "12", "PING"))
        self.assertEqual("conflict", reopened.claim("W8IJC-7", "12", "STATUS"))
        self.assertEqual("0001", reopened.next_outbound_id())

    def test_rate_limiter_is_per_sender_and_global(self):
        now = [100.0]
        limiter = pcs_aprs_agent.SlidingWindowLimiter(2, 3, now=lambda: now[0])
        self.assertTrue(limiter.allow("A"))
        self.assertTrue(limiter.allow("A"))
        self.assertFalse(limiter.allow("A"))
        self.assertTrue(limiter.allow("B"))
        self.assertFalse(limiter.allow("C"))
        now[0] = 161.0
        self.assertTrue(limiter.allow("A"))

    def test_numbered_messages_are_acked_even_when_command_handling_is_rate_limited(self):
        limited_agent = pcs_aprs_agent.AprsAgent(
            self.config,
            self.store,
            self.provider,
            pcs_aprs_agent.SlidingWindowLimiter(0, 0),
        )
        sent = []

        send_limited = lambda _channel, frame: sent.append(frame)
        limited_agent.process(inbound_frame("W8IJC-7", "W8IJC-10", "PING", "91"), 8, send_limited)
        limited_agent.process(inbound_frame("W8IJC-7", "W8IJC-10", "PING", "91"), 8, send_limited)

        self.assertEqual(2, len(sent))
        self.assertEqual(
            [b":W8IJC-7  :ack91", b":W8IJC-7  :ack91"],
            [pcs_aprs_agent.decode_ax25_ui(frame).information for frame in sent],
        )
        self.assertEqual([], self.provider.commands)


class ConfigurationAndStatusTests(unittest.TestCase):
    def test_packaging_preserves_dire_wolf_and_rf_safety_boundary(self):
        setup = SETUP_PATH.read_text(encoding="utf-8")
        service = SERVICE_PATH.read_text(encoding="utf-8")
        direwolf_setup = DIREWOLF_SETUP_PATH.read_text(encoding="utf-8")
        profile = INSTALL_EXAMPLE_PATH.read_text(encoding="utf-8")
        documentation = DOC_PATH.read_text(encoding="utf-8")

        self.assertIn('PCS_APRS_AGENT_ENABLED="no"', profile)
        self.assertIn('PCS_APRS_AGENT_ICHANNEL="8"', profile)
        self.assertIn('PCS_APRS_AGENT_RF_ENABLED="no"', profile)
        self.assertIn('PCS_APRS_AGENT_RF_CHANNEL="0"', profile)
        self.assertIn('echo "ICHANNEL ${PCS_APRS_AGENT_ICHANNEL}"', direwolf_setup)
        self.assertIn("validate_live_mapping", setup)
        self.assertIn('PCS_APRS_ENGINE}" != "direwolf"', setup)
        self.assertIn("systemctl is-active --quiet direwolf.service", setup)
        self.assertNotIn("systemctl restart direwolf", setup)
        self.assertNotIn("IGLOGIN", setup)
        self.assertIn("DynamicUser=yes", service)
        self.assertIn("After=direwolf.service", service)
        self.assertIn("AF_NETLINK", service)
        self.assertIn("PCS APRS Agent runtime status is fresh, connected, and aggregate-only", (ROOT / "scripts" / "pcs-self-test.sh").read_text(encoding="utf-8"))
        self.assertIn(
            "PCS APRS outbound state and retry queue are valid",
            (ROOT / "scripts" / "pcs-self-test.sh").read_text(encoding="utf-8"),
        )
        self.assertIn(
            "PCS APRS Agent local RF route is explicitly enabled on guarded channel 0",
            (ROOT / "scripts" / "pcs-self-test.sh").read_text(encoding="utf-8"),
        )
        for forbidden in ("Requires=direwolf", "Wants=direwolf", "PartOf=direwolf"):
            self.assertNotIn(forbidden, service)
        self.assertIn("RF access is off by default", documentation)
        self.assertIn("Dire Wolf performs normal channel", documentation)
        self.assertIn("not compatible with Graywolf", documentation)
        self.assertIn("source must match the reply recipient", documentation)
        self.assertIn("PCS_APRS_AGENT_OUTBOUND_RETRY_SECONDS", setup)
        self.assertIn("APRS Agent RF access requires the guarded commissioned TX profile", setup)
        self.assertIn("APRS Agent RF access requires a live guarded Dire Wolf PTT directive", setup)
        self.assertIn(
            "outbound_retry_seconds = 30,60,120,240",
            CONFIG_EXAMPLE_PATH.read_text(encoding="utf-8"),
        )

    def test_runtime_does_not_connect_when_dire_wolf_is_inactive(self):
        config = pcs_aprs_agent.AgentConfig()
        runtime = pcs_aprs_agent.AgentRuntime(config, mock.Mock(), engine_active=lambda: False)

        def stop_after_wait(_delay):
            runtime.stop()
            return True

        with mock.patch.object(runtime.stop_event, "wait", side_effect=stop_after_wait), mock.patch.object(
            pcs_aprs_agent.socket, "create_connection"
        ) as connect:
            runtime.run()

        connect.assert_not_called()

    def test_dire_wolf_guard_requires_exact_cgroup_process_and_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            proc_root = Path(temp_dir)
            process = proc_root / "123"
            process.mkdir()
            (process / "cgroup").write_text(
                "0::/system.slice/direwolf.service\n",
                encoding="ascii",
            )
            (process / "cmdline").write_bytes(
                b"/usr/local/bin/direwolf\0-c\0/etc/direwolf.conf\0"
            )
            self.assertTrue(pcs_aprs_agent.direwolf_service_active(proc_root))

            (process / "cgroup").write_text(
                "0::/system.slice/graywolf.service\n",
                encoding="ascii",
            )
            self.assertFalse(pcs_aprs_agent.direwolf_service_active(proc_root))

    def test_agent_example_is_valid_and_loopback_only(self):
        config = pcs_aprs_agent.AgentConfig.load(CONFIG_EXAMPLE_PATH)
        self.assertEqual("127.0.0.1", config.kiss_host)
        self.assertEqual(8, config.kiss_channel)
        self.assertFalse(config.rf_enabled)
        self.assertEqual(0, config.rf_channel)
        self.assertEqual("W8IJC-10", config.callsign)

    def test_configuration_requires_loopback_kiss_and_non_radio_channel(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "agent.conf"
            path.write_text(
                "[agent]\n"
                "callsign = W8IJC-10\n"
                "kiss_host = 10.42.0.1\n"
                "kiss_channel = 0\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(pcs_aprs_agent.ConfigError, "loopback"):
                pcs_aprs_agent.AgentConfig.load(path)

            path.write_text(
                "[agent]\n"
                "callsign = W8IJC-10\n"
                "kiss_host = 127.0.0.1\n"
                "kiss_channel = 0\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(pcs_aprs_agent.ConfigError, "1 through 15"):
                pcs_aprs_agent.AgentConfig.load(path)

            path.write_text(
                "[agent]\n"
                "callsign = W8IJC-10\n"
                "kiss_host = 127.0.0.1\n"
                "kiss_channel = 8\n"
                "state_db = /tmp/aprs-agent.sqlite3\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(pcs_aprs_agent.ConfigError, "state_db"):
                pcs_aprs_agent.AgentConfig.load(path)

            path.write_text(
                "[agent]\n"
                "callsign = W8IJC-10\n"
                "kiss_host = 127.0.0.1\n"
                "kiss_channel = 8\n"
                "rf_enabled = yes\n"
                "rf_channel = 1\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(pcs_aprs_agent.ConfigError, "physical radio channel 0"):
                pcs_aprs_agent.AgentConfig.load(path)

            path.write_text(
                "[agent]\n"
                "callsign = W8IJC-10\n"
                "kiss_host = 127.0.0.1\n"
                "kiss_channel = 8\n"
                "rf_enabled = maybe\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(pcs_aprs_agent.ConfigError, "rf_enabled must be yes or no"):
                pcs_aprs_agent.AgentConfig.load(path)

    def test_configuration_validates_bounded_nondecreasing_retry_schedule(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "agent.conf"
            base = (
                "[agent]\n"
                "callsign = W8IJC-10\n"
                "kiss_host = 127.0.0.1\n"
                "kiss_channel = 8\n"
            )
            path.write_text(base + "outbound_retry_seconds = 5,30,120\n", encoding="utf-8")
            self.assertEqual((5, 30, 120), pcs_aprs_agent.AgentConfig.load(path).outbound_retry_seconds)

            path.write_text(base + "outbound_retry_seconds = 30,5\n", encoding="utf-8")
            with self.assertRaisesRegex(pcs_aprs_agent.ConfigError, "nondecreasing"):
                pcs_aprs_agent.AgentConfig.load(path)

            path.write_text(base + "outbound_retry_seconds = 1,30\n", encoding="utf-8")
            with self.assertRaisesRegex(pcs_aprs_agent.ConfigError, "5 to 3600"):
                pcs_aprs_agent.AgentConfig.load(path)

            path.write_text(base + "outbound_retry_seconds = 5,,30\n", encoding="utf-8")
            with self.assertRaisesRegex(pcs_aprs_agent.ConfigError, "empty delay"):
                pcs_aprs_agent.AgentConfig.load(path)

    def test_temperature_uptime_and_power_are_bounded_read_only_values(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temperature = Path(temp_dir) / "temp"
            uptime = Path(temp_dir) / "uptime"
            temperature.write_text("43250\n", encoding="ascii")
            uptime.write_text("93784.22 1.0\n", encoding="ascii")
            provider = pcs_aprs_agent.StatusProvider(
                temperature_path=temperature,
                uptime_path=uptime,
            )

            self.assertEqual("TEMP 43C", provider.temperature())
            self.assertEqual("UPTIME 1D 2H 3M", provider.uptime())
            self.assertEqual("POWER N/A", provider.power())

    def test_status_uses_requested_health_uplink_gps_and_temperature_fields(self):
        provider = pcs_aprs_agent.StatusProvider()
        with (
            mock.patch.object(provider, "uplink_value", return_value="WiFi"),
            mock.patch.object(provider, "gps_value", return_value="3D"),
            mock.patch.object(provider, "temperature_c", return_value=37),
        ):
            self.assertEqual(
                "PCS OK | Uplink - WiFi | GPS 3D | Pi Temp - 37C",
                provider.status(),
            )
        with (
            mock.patch.object(provider, "uplink_value", return_value="Down"),
            mock.patch.object(provider, "gps_value", return_value="NO FIX"),
            mock.patch.object(provider, "temperature_c", return_value=38),
        ):
            self.assertEqual(
                "PCS BAD | Uplink - Down | GPS NoFX | Pi Temp - 38C",
                provider.status(),
            )

    def test_gps_reports_fix_dimension_without_coordinates(self):
        gps_socket = FakeGpsSocket(
            [b'{"class":"TPV","mode":3,"time":"2026-09-02T16:00:00Z","lat":39.0,"lon":-77.0}\n']
        )
        provider = pcs_aprs_agent.StatusProvider(timeout=1, wall_time=lambda: 1_788_364_805)
        with mock.patch.object(pcs_aprs_agent.socket, "create_connection", return_value=gps_socket):
            self.assertEqual("GPS 3D", provider.gps())
        self.assertEqual([b'?WATCH={"enable":true,"json":true};\n'], gps_socket.sent)

    def test_gps_does_not_present_a_replayed_fix_as_current(self):
        gps_socket = FakeGpsSocket(
            [b'{"class":"TPV","mode":3,"time":"2026-09-02T15:00:00Z","lat":39.0,"lon":-77.0}\n']
        )
        provider = pcs_aprs_agent.StatusProvider(timeout=1, wall_time=lambda: 1_788_364_805)
        with mock.patch.object(pcs_aprs_agent.socket, "create_connection", return_value=gps_socket):
            self.assertEqual("GPS STALE", provider.gps())

    def test_lte_and_network_use_fixed_read_only_commands(self):
        completed = pcs_aprs_agent.subprocess.CompletedProcess
        runner = FakeRunner(
            [
                completed([], 0, "gsm:connected\nwifi:disconnected\n", ""),
                completed([], 0, "ethernet:connected\ngsm:disconnected\n", ""),
            ]
        )
        provider = pcs_aprs_agent.StatusProvider(runner=runner, timeout=2)

        self.assertEqual("LTE UP", provider.lte())
        self.assertEqual("NET ETH", provider.network())
        self.assertEqual(
            ["nmcli", "-t", "-f", "TYPE,STATE", "device", "status"],
            runner.calls[0][0],
        )

    def test_help_and_every_command_fit_aprs_message_limit(self):
        provider = pcs_aprs_agent.StatusProvider()
        for command in pcs_aprs_agent.COMMANDS:
            if command in {"STATUS", "LTE", "GPS", "NET"}:
                continue
            result = provider.execute(command)
            wire = pcs_aprs_agent.aprs_reply_information("W8IJC-7", result, "0001")
            self.assertLessEqual(len(wire[11:]), pcs_aprs_agent.MAX_APRS_MESSAGE_TEXT)

    def test_dedupe_schema_stores_only_a_command_digest(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "state.sqlite3"
            store = pcs_aprs_agent.DedupStore(path, 3600, now=lambda: 100)
            store.claim("W8IJC-7", "1", "STATUS")
            store.close()
            connection = sqlite3.connect(path)
            columns = [row[1] for row in connection.execute("PRAGMA table_info(received_messages)")]
            connection.close()
        self.assertNotIn("body", columns)
        self.assertIn("body_digest", columns)

    def test_status_reporter_exports_only_aggregate_mailbox_fields(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "state.sqlite3"
            status_path = Path(temp_dir) / "status.json"
            store = pcs_aprs_agent.DedupStore(database, 3600, now=lambda: 100)
            store.store_mailbox_message("W8IJC-7", "71", "Meet at 1900", 100)
            reporter = pcs_aprs_agent.StatusReporter(status_path, store, now=lambda: 101)
            reporter.packet_received()
            reporter.message_received()
            reporter.set_state("connected")
            payload = json.loads(status_path.read_text(encoding="utf-8"))
            store.close()

        self.assertEqual("ok", payload["status"])
        self.assertEqual(1, payload["packets_received"])
        self.assertEqual(1, payload["messages_received"])
        self.assertEqual(1, payload["mailbox_total"])
        self.assertEqual(1, payload["mailbox_unread"])
        self.assertNotIn("body", payload)
        self.assertNotIn("sender", payload)

    def test_state_validation_requires_outbound_schema_and_valid_queue_rows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "state.sqlite3"
            store = pcs_aprs_agent.DedupStore(database, 3600, now=lambda: 100)
            store.queue_outbound("W8IJC-7", "TEST", 100, 8)
            store.close()

            pcs_aprs_agent.validate_state_database(database, 100)
            connection = sqlite3.connect(database)
            connection.execute(
                "UPDATE outbound_messages SET state = 'pending', next_attempt_at = NULL"
            )
            connection.commit()
            connection.close()

            with self.assertRaisesRegex(ValueError, "outbound message state"):
                pcs_aprs_agent.validate_state_database(database, 100)

    def test_existing_outbound_schema_migrates_with_legacy_internet_channel(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "state.sqlite3"
            connection = sqlite3.connect(database)
            connection.execute(
                """CREATE TABLE outbound_messages (
                       message_id TEXT PRIMARY KEY,
                       recipient TEXT NOT NULL,
                       body TEXT NOT NULL,
                       state TEXT NOT NULL,
                       attempts INTEGER NOT NULL,
                       created_at REAL NOT NULL,
                       updated_at REAL NOT NULL,
                       next_attempt_at REAL,
                       last_sent_at REAL,
                       completed_at REAL
                   )"""
            )
            connection.execute(
                "INSERT INTO outbound_messages VALUES "
                "('ABCD', 'W8IJC-7', 'TEST', 'pending', 1, 10, 10, 20, 10, NULL)"
            )
            connection.commit()
            connection.close()

            store = pcs_aprs_agent.DedupStore(
                database,
                3600,
                now=lambda: 20,
                legacy_outbound_channel=7,
            )
            due = store.due_outbound()
            store.close()

            self.assertEqual(1, len(due))
            self.assertEqual(7, due[0].kiss_channel)


if __name__ == "__main__":
    unittest.main()
