from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from server.tgame_reconnect import parse_cmd06_resume
from server.assaultfire_room_registry import RoomRegistry
from server.tgame_ticket_state import get_sessions_for_ip, issue_ticket, save_transport_key


def _mode3_encrypt(plain: bytes, key: bytes) -> bytes:
    remainder = len(plain) & 0x0F
    pad_length = (16 if remainder <= 10 else 32) - remainder
    random_length = pad_length - 6
    framed = plain + bytes(random_length) + b"tsf4g" + bytes([pad_length])
    encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    previous = bytes(range(16))
    ciphertext = bytearray()
    for offset in range(0, len(framed), 16):
        block = bytes(a ^ b for a, b in zip(framed[offset:offset + 16], previous))
        encrypted = encryptor.update(block)
        ciphertext.extend(encrypted)
        previous = encrypted
    encryptor.finalize()
    return bytes(ciphertext)


def _mode3_decrypt(ciphertext: bytes, key: bytes) -> bytes:
    decryptor = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
    raw = decryptor.update(ciphertext) + decryptor.finalize()
    previous = bytes(range(16))
    framed = bytearray()
    for offset in range(0, len(raw), 16):
        block = raw[offset:offset + 16]
        framed.extend(a ^ b for a, b in zip(block, previous))
        previous = ciphertext[offset:offset + 16]
    pad_length = framed[-1]
    if framed[-6:-1] != b"tsf4g" or pad_length <= 0:
        raise ValueError("invalid mode3 padding")
    plain_length = len(framed) - pad_length
    remainder = plain_length & 0x0F
    expected = (16 if remainder <= 10 else 32) - remainder
    if pad_length != expected:
        raise ValueError("invalid mode3 padding length")
    return bytes(framed[:plain_length])


class TGameReconnectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.key = bytes.fromhex("00112233445566778899aabbccddeeff")

    def _packet(self, *, uin: int = 10001, sequence: int = 0x7CE1) -> bytes:
        encrypted_header = _mode3_encrypt(
            struct.pack(">I", uin) + bytes(20), self.key
        )
        encrypted_body = _mode3_encrypt(struct.pack(">I", sequence), self.key)
        head_length = 28 + len(encrypted_header)
        return (
            b"\x55\x0e\x06\x04"
            + struct.pack(">II", head_length, len(encrypted_body))
            + struct.pack(">IIII", 3, 2, 0, len(encrypted_header))
            + encrypted_header
            + encrypted_body
        )

    def test_cmd06_decrypts_account_and_sequence(self) -> None:
        parsed = parse_cmd06_resume(
            self._packet(), self.key, _mode3_decrypt
        )
        self.assertEqual(parsed, {
            "mode": 3,
            "service_id": 2,
            "uin": 10001,
            "sequence": 0x7CE1,
        })

    def test_wrong_session_key_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "session-key verification"):
            parse_cmd06_resume(
                self._packet(), bytes(16), _mode3_decrypt
            )

    def test_malformed_frame_lengths_are_rejected(self) -> None:
        packet = bytearray(self._packet())
        packet[7] += 1
        with self.assertRaisesRegex(ValueError, "header length"):
            parse_cmd06_resume(packet, self.key, _mode3_decrypt)

    def test_cmd06_matches_transport_key_loaded_from_sqlite(self) -> None:
        with tempfile.TemporaryDirectory(prefix="af-resume-session-") as temp_dir:
            root = Path(temp_dir)
            db_path = root / "accounts.sqlite3"
            private_key_path = root / "PRIVATE.PEM"
            private_key_path.write_bytes(b"test-only-private-key-material-" * 8)
            ip = "203.0.113.20"
            uin = 10001
            ticket = issue_ticket(
                uin,
                ip,
                bytes.fromhex("ffeeddccbbaa99887766554433221100"),
                db_path=db_path,
                private_key_path=private_key_path,
            )
            self.assertTrue(
                save_transport_key(
                    ticket,
                    uin,
                    ip,
                    self.key,
                    db_path=db_path,
                    private_key_path=private_key_path,
                )
            )

            candidates = get_sessions_for_ip(
                ip,
                db_path=db_path,
                private_key_path=private_key_path,
            )
            self.assertEqual(len(candidates), 1)
            parsed = parse_cmd06_resume(
                self._packet(uin=uin),
                candidates[0]["transport_key"],
                _mode3_decrypt,
            )
            self.assertEqual(parsed["uin"], candidates[0]["uin"])

    def test_same_ip_can_hold_multiple_account_sessions(self) -> None:
        with tempfile.TemporaryDirectory(prefix="af-resume-multisession-") as temp_dir:
            root = Path(temp_dir)
            db_path = root / "accounts.sqlite3"
            private_key_path = root / "PRIVATE.PEM"
            private_key_path.write_bytes(b"test-only-private-key-material-" * 8)
            ip = "203.0.113.20"

            for uin in (10001, 10002):
                ticket = issue_ticket(
                    uin,
                    ip,
                    bytes.fromhex("ffeeddccbbaa99887766554433221100"),
                    db_path=db_path,
                    private_key_path=private_key_path,
                )
                self.assertTrue(
                    save_transport_key(
                        ticket,
                        uin,
                        ip,
                        self.key,
                        db_path=db_path,
                        private_key_path=private_key_path,
                    )
                )

            candidates = get_sessions_for_ip(
                ip,
                db_path=db_path,
                private_key_path=private_key_path,
            )
            self.assertEqual({row["uin"] for row in candidates}, {10001, 10002})

            for expected_uin in (10001, 10002):
                matched = []
                packet = self._packet(uin=expected_uin)
                for candidate in candidates:
                    parsed = parse_cmd06_resume(
                        packet,
                        candidate["transport_key"],
                        _mode3_decrypt,
                    )
                    if parsed["uin"] == candidate["uin"]:
                        matched.append(candidate["uin"])
                self.assertEqual(matched, [expected_uin])


class RoomRestartRecoveryTests(unittest.TestCase):
    def test_restart_keeps_waiting_room_for_live_sessions_only(self) -> None:
        with tempfile.TemporaryDirectory(prefix="af-resume-room-") as temp_dir:
            db_path = Path(temp_dir) / "accounts.sqlite3"
            registry = RoomRegistry(db_path=db_path)
            restored_registry = None
            try:
                registry.create_room(
                    {
                        "room_id": 10,
                        "name": "Waiting Room",
                        "match_settings_wire": b"settings",
                        "fighter_capacity": 4,
                        "observer_capacity": 0,
                        "ds_slot": 2,
                        "ds_public_port": 65007,
                    },
                    owner_uin=10001,
                    owner_name="owner",
                )
                registry.set_ready(10001, True)
                registry.join_room(
                    uin=10002,
                    nickname="guest",
                    room_id=10,
                )
                registry.create_room(
                    {
                        "room_id": 20,
                        "name": "Active Match",
                        "match_settings_wire": b"settings",
                        "fighter_capacity": 4,
                        "observer_capacity": 0,
                    },
                    owner_uin=10003,
                    owner_name="active",
                )
                registry.set_started(20, True)

                summary = registry.recover_after_restart(
                    retain_uins={10002},
                )
                self.assertEqual(
                    summary,
                    {
                        "recovered_rooms": 1,
                        "removed_rooms": 1,
                        "removed_members": 1,
                    },
                )

                restored_registry = RoomRegistry(db_path=db_path)
                room = restored_registry.room_for_player(10002)
                self.assertEqual(room["room_id"], 10)
                self.assertEqual(room["owner_uin"], 10002)
                self.assertFalse(room["started"])
                self.assertEqual(len(room["members"]), 1)
                self.assertFalse(room["members"][0]["ready"])
                self.assertNotIn("ds_slot", room)
                self.assertNotIn("ds_public_port", room)
                self.assertIsNone(restored_registry.room_for_player(10001))
                self.assertIsNone(restored_registry.room_for_player(10003))
            finally:
                if restored_registry is not None:
                    restored_registry.close()
                registry.close()


if __name__ == "__main__":
    unittest.main()
