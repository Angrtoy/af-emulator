from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server.player_db import PlayerDatabase


class PlayerExperienceTests(unittest.TestCase):
    def test_existing_profile_schema_migrates_and_round_trips_progress(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "accounts.sqlite3"
            conn = sqlite3.connect(db_path)
            conn.executescript(
                """
                CREATE TABLE player_profiles (
                    uin INTEGER PRIMARY KEY,
                    nickname TEXT,
                    current_role_gid INTEGER NOT NULL,
                    current_bag_gid INTEGER NOT NULL,
                    next_gid INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE player_wallets (
                    uin INTEGER PRIMARY KEY,
                    ap INTEGER NOT NULL,
                    gp INTEGER NOT NULL,
                    mp INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                );
                INSERT INTO player_profiles VALUES(
                    10001, 'OldProfile', 42953967927297, 42953967927298,
                    42953967927300, '2026-01-01T00:00:00+00:00',
                    '2026-01-01T00:00:00+00:00'
                );
                INSERT INTO player_wallets VALUES(
                    10001, 500, 600, 700, '2026-01-01T00:00:00+00:00'
                );
                """
            )
            conn.commit()
            conn.close()

            db = PlayerDatabase(db_path)
            conn = db._connect()
            try:
                columns = {
                    row["name"]
                    for row in conn.execute("PRAGMA table_info(player_profiles)")
                }
            finally:
                conn.close()
            self.assertIn("experience", columns)

            state = db.load_player_state(10001)
            self.assertEqual(state["experience"], 0)
            state["experience"] = 98765
            db.save_player_state(10001, state, reason="experience-test")

            reloaded = PlayerDatabase(db_path).load_player_state(10001)
            self.assertEqual(reloaded["experience"], 98765)

    def test_capitalized_existing_experience_column_is_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "accounts.sqlite3"
            conn = sqlite3.connect(db_path)
            conn.executescript(
                """
                CREATE TABLE player_profiles (
                    uin INTEGER PRIMARY KEY,
                    nickname TEXT,
                    Experience INTEGER NOT NULL DEFAULT 0,
                    current_role_gid INTEGER NOT NULL,
                    current_bag_gid INTEGER NOT NULL,
                    next_gid INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE player_wallets (
                    uin INTEGER PRIMARY KEY,
                    ap INTEGER NOT NULL,
                    gp INTEGER NOT NULL,
                    mp INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                );
                INSERT INTO player_profiles VALUES(
                    10001, 'StoredXP', 43210, 42953967927297,
                    42953967927298, 42953967927300,
                    '2026-01-01T00:00:00+00:00',
                    '2026-01-01T00:00:00+00:00'
                );
                INSERT INTO player_wallets VALUES(
                    10001, 500, 600, 700, '2026-01-01T00:00:00+00:00'
                );
                """
            )
            conn.commit()
            conn.close()

            db = PlayerDatabase(db_path)
            conn = db._connect()
            try:
                experience_columns = [
                    row["name"]
                    for row in conn.execute("PRAGMA table_info(player_profiles)")
                    if str(row["name"]).casefold() == "experience"
                ]
            finally:
                conn.close()
            self.assertEqual(experience_columns, ["Experience"])
            self.assertEqual(db.load_player_state(10001)["experience"], 43210)

    def test_login_projection_sends_persisted_exp_without_level_formula(self):
        code = r"""
import os
import sqlite3
import struct
import assaultfire_server_v143b as server

uin = 10001
server._v140_select_player(uin)
state = server._V140_PLAYER_STATE.state()
state["experience"] = 10
server._V140_PLAYER_STATE.save("seed-experience-test")

# Simulate an existing DB-backed resource update while this server process has
# cached state, then use the same refresh operation as the ZONE login path.
with sqlite3.connect(os.environ["AF_ACCOUNT_DB"]) as conn:
    conn.execute(
        "UPDATE player_profiles SET experience=? WHERE uin=?",
        (43210, uin),
    )
server._V140_PLAYER_STATE.reload(uin)

# UpdatePlayerProperty uses the live-verified A00A 36-byte schema:
# u32 bitmask flag, u16 reason, then the fixed wallet/progression fields.
wallet_packet = server._v140_build_update_player_property(
    server.UPDATE_FLAG_TP, server.UPDATE_REASON_TP_BALANCE
)
assert struct.unpack_from(">H", wallet_packet, 2)[0] == 0xA00A
wallet_body = wallet_packet[8:]
assert len(wallet_body) == 36
assert struct.unpack_from(">I", wallet_body, 0)[0] == 0x01
assert struct.unpack_from(">H", wallet_body, 4)[0] == 0x2A
assert struct.unpack_from(">i", wallet_body, 22)[0] == 0

# A50E is the authoritative wallet refresh boundary.  It republishes all
# three displayed wallet values with their recovered bitmask selectors.
refresh = server._v140_build_authoritative_wallet_refresh()
assert [(flag, name, reason) for flag, name, reason, _packet in refresh] == [
    (0x01, "AP", 0x2A),
    (0x02, "GP", 0x00),
    (0x10, "MP", 0x00),
]
for flag, _name, reason, refresh_packet in refresh:
    assert struct.unpack_from(">H", refresh_packet, 2)[0] == 0xA00A
    refresh_body = refresh_packet[8:]
    assert len(refresh_body) == 36
    assert struct.unpack_from(">I", refresh_body, 0)[0] == flag
    assert struct.unpack_from(">H", refresh_body, 4)[0] == reason

# The login profile is the packet the client uses to initialize progression.
# Its Experience field follows the variable-length NickName TDR string and the
# wallet fields; do not derive or substitute a level threshold on the server.
nickname = "ExperienceTest"
profile_packet = server._v48_build_playerinfo(
    7, uin=uin, nickname=nickname
)
assert struct.unpack_from(">H", profile_packet, 2)[0] == server.TGAME_ZN_NTF_PLAYERINFO
experience_offset = (
    8 + 2  # app header + Result
    + 8 + 4  # UIN + preceding field
    + len(server._v50_geo_tdr_string(nickname, 32))
    + 16 + 6  # wallet fields + following fields
)
assert struct.unpack_from(">i", profile_packet, experience_offset)[0] == 43210

captured = []
server._v48_send_app = lambda conn, key, packet, label, desc: captured.append(
    (bytes(packet), desc)
)
server._v140_send_experience_sync(object(), b"0123456789abcdef", "TEST")
assert len(captured) == 1
packet, description = captured[0]
assert struct.unpack_from(">H", packet, 2)[0] == 0xA00A
assert struct.unpack_from(">H", packet, 2)[0] == server.TGAME_ZN_NTF_UPDATE_PLAYER_PROPERTY
body = packet[8:]
assert len(body) == 36
assert struct.unpack_from(">I", body, 0)[0] == server.UPDATE_FLAG_EXP == 0x04
assert struct.unpack_from(">H", body, 4)[0] == 0
assert struct.unpack_from(">i", body, 22)[0] == 43210
assert "experience=43210" in description
"""
        with tempfile.TemporaryDirectory() as td:
            env = os.environ.copy()
            env.update(
                {
                    "AF_ACCOUNT_DB": str(Path(td) / "accounts.sqlite3"),
                    "AF_RUNTIME_MODE": "production",
                    "AF_DEV_WEB": "0",
                }
            )
            result = subprocess.run(
                [sys.executable, "-c", code],
                cwd=ROOT / "server",
                env=env,
                text=True,
                capture_output=True,
                timeout=30,
            )
        self.assertEqual(
            result.returncode,
            0,
            msg=f"profile EXP projection failed:\n{result.stdout}\n{result.stderr}",
        )


if __name__ == "__main__":
    unittest.main()
