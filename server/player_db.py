"""SQLite persistence for stable Assault Fire local identities and player state.

The existing account database remains the single database file.  This module adds
supplementary game tables without changing password/authentication semantics.

Persistent:
  * AP login -> stable UIN mapping for the current local AP path
  * nickname, experience, and profile selection state
  * AP/GP/MP wallet
  * inventory / equipment

Not persistent here:
  * match rooms are owned by RoomRegistry (stored in this SQLite file)
  * live network sessions and dedicated-server processes
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping



class PlayerDBError(RuntimeError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _login_key(login_name: str) -> str:
    value = str(login_name or "").strip()
    if not value:
        raise PlayerDBError("empty AP login identity")
    if len(value.encode("utf-8")) > 128:
        raise PlayerDBError("AP login identity is too long")
    return value.casefold()


def _i64(value: Any) -> int:
    value = int(value)
    if value < 0 or value > 0x7FFFFFFFFFFFFFFF:
        raise PlayerDBError(f"value outside SQLite signed 64-bit range: {value}")
    return value


def _experience_value(value: Any) -> int:
    try:
        value = int(value)
    except (TypeError, ValueError) as exc:
        raise PlayerDBError("experience must be an integer") from exc
    if value < 0 or value > 0x7FFFFFFF:
        raise PlayerDBError("experience must be between 0 and 2147483647")
    return value


DEFAULT_DB_PATH = Path(
    __import__("os").environ.get(
        "AF_ACCOUNT_DB",
        str(Path(__file__).with_name("assaultfire_accounts.sqlite3")),
    )
)


class PlayerDatabase:
    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
        self._schema_lock = threading.RLock()
        self.init_schema()

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def init_schema(self) -> Path:
        with self._schema_lock:
            conn = self._connect()
            try:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS meta (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS game_identities (
                        login_key TEXT PRIMARY KEY,
                        login_display TEXT NOT NULL,
                        uin INTEGER NOT NULL UNIQUE CHECK (uin >= 10001),
                        created_at TEXT NOT NULL,
                        last_seen_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS player_profiles (
                        uin INTEGER PRIMARY KEY CHECK (uin >= 10001),
                        nickname TEXT,
                        experience INTEGER NOT NULL DEFAULT 0 CHECK (experience >= 0),
                        current_role_gid INTEGER NOT NULL,
                        current_bag_gid INTEGER NOT NULL,
                        next_gid INTEGER NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS player_wallets (
                        uin INTEGER PRIMARY KEY CHECK (uin >= 10001),
                        ap INTEGER NOT NULL CHECK (ap >= 0),
                        gp INTEGER NOT NULL CHECK (gp >= 0),
                        mp INTEGER NOT NULL CHECK (mp >= 0),
                        updated_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS player_inventory (
                        uin INTEGER NOT NULL CHECK (uin >= 10001),
                        gid INTEGER NOT NULL,
                        item_id INTEGER NOT NULL,
                        owner_gid INTEGER NOT NULL,
                        location INTEGER NOT NULL,
                        durability INTEGER NOT NULL,
                        durability_max INTEGER NOT NULL,
                        avail_hours INTEGER NOT NULL,
                        validity INTEGER NOT NULL,
                        gain_type INTEGER NOT NULL,
                        obtained_at INTEGER NOT NULL DEFAULT 0,
                        expires_at INTEGER NOT NULL DEFAULT 0,
                        PRIMARY KEY (uin, gid)
                    );

                    CREATE INDEX IF NOT EXISTS idx_player_inventory_uin_item
                        ON player_inventory(uin, item_id);

                    CREATE TABLE IF NOT EXISTS player_moneyflow (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        uin INTEGER NOT NULL CHECK (uin >= 10001),
                        occurred_at INTEGER NOT NULL CHECK (occurred_at >= 0),
                        money_type INTEGER NOT NULL CHECK (money_type BETWEEN 1 AND 3),
                        number INTEGER NOT NULL,
                        current_balance INTEGER NOT NULL CHECK (current_balance >= 0),
                        reason INTEGER NOT NULL CHECK (reason BETWEEN 0 AND 255),
                        details TEXT NOT NULL DEFAULT '',
                        commodity_ids TEXT NOT NULL DEFAULT '[]',
                        created_at TEXT NOT NULL,
                        FOREIGN KEY (uin) REFERENCES player_profiles(uin)
                            ON DELETE CASCADE
                    );

                    CREATE INDEX IF NOT EXISTS idx_player_moneyflow_uin_id
                        ON player_moneyflow(uin, id);

                    CREATE INDEX IF NOT EXISTS idx_player_profiles_nickname_nocase
                        ON player_profiles(nickname COLLATE NOCASE)
                        WHERE nickname IS NOT NULL AND nickname <> '';
                    """
                )
                # Upgrade existing player_inventory tables in place. Older
                # rows have unknown purchase dates and remain non-expiring.
                inventory_columns = {
                    str(row["name"])
                    for row in conn.execute(
                        "PRAGMA table_info(player_inventory)"
                    ).fetchall()
                }
                if "obtained_at" not in inventory_columns:
                    conn.execute(
                        "ALTER TABLE player_inventory "
                        "ADD COLUMN obtained_at INTEGER NOT NULL DEFAULT 0"
                    )
                if "expires_at" not in inventory_columns:
                    conn.execute(
                        "ALTER TABLE player_inventory "
                        "ADD COLUMN expires_at INTEGER NOT NULL DEFAULT 0"
                    )
                profile_columns = {
                    str(row["name"]).casefold()
                    for row in conn.execute(
                        "PRAGMA table_info(player_profiles)"
                    ).fetchall()
                }
                if "experience" not in profile_columns:
                    conn.execute(
                        "ALTER TABLE player_profiles "
                        "ADD COLUMN experience INTEGER NOT NULL DEFAULT 0 "
                        "CHECK (experience >= 0)"
                    )
                conn.execute(
                    "INSERT OR REPLACE INTO meta(key, value) "
                    "VALUES('player_state_schema_version', '4')"
                )
                conn.commit()
            finally:
                conn.close()
        return self.db_path

    def resolve_identity(self, login_name: str) -> int:
        """Resolve an AP login to a stable UIN transactionally.

        If the login is already a registered web account, reuse that account UIN.
        Otherwise allocate from the shared UIN number space and remember the mapping.
        """
        display = str(login_name or "").strip()
        key = _login_key(display)
        now = _utc_now()
        self.init_schema()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT uin FROM game_identities WHERE login_key = ?",
                (key,),
            ).fetchone()
            if row:
                uin = int(row["uin"])
                conn.execute(
                    "UPDATE game_identities SET login_display=?, last_seen_at=? WHERE login_key=?",
                    (display, now, key),
                )
                conn.commit()
                return uin

            # Prefer a real registered account identity when the web/account
            # schema is present in this same SQLite file.
            has_accounts = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='accounts'"
            ).fetchone()
            account_row = None
            max_account = 10000
            if has_accounts:
                account_row = conn.execute(
                    "SELECT uin FROM accounts WHERE username_norm = ?",
                    (key,),
                ).fetchone()
                a = conn.execute("SELECT MAX(uin) AS m FROM accounts").fetchone()
                max_account = int(a["m"]) if a and a["m"] is not None else 10000

            if account_row:
                uin = int(account_row["uin"])
            else:
                g = conn.execute("SELECT MAX(uin) AS m FROM game_identities").fetchone()
                max_game = int(g["m"]) if g and g["m"] is not None else 10000
                uin = max(10001, max(max_account, max_game) + 1)

            conn.execute(
                """
                INSERT INTO game_identities(login_key, login_display, uin, created_at, last_seen_at)
                VALUES(?, ?, ?, ?, ?)
                """,
                (key, display, uin, now, now),
            )
            conn.commit()
            return int(uin)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def login_for_uin(self, uin: int) -> str | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT login_display FROM game_identities WHERE uin = ?",
                (int(uin),),
            ).fetchone()
            return str(row["login_display"]) if row else None
        finally:
            conn.close()

    def has_player_state(self, uin: int) -> bool:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT 1 FROM player_profiles WHERE uin = ?",
                (int(uin),),
            ).fetchone()
            return bool(row)
        finally:
            conn.close()

    def _normalize_state(self, state: Mapping[str, Any]) -> dict[str, Any]:
        wallet = dict(state.get("wallet") or {})
        inventory = [dict(p) for p in (state.get("inventory") or [])]
        max_gid = max((int(p.get("gid", 0)) for p in inventory), default=0)
        next_gid = int(state.get("next_gid", max_gid + 1))
        if next_gid <= max_gid:
            next_gid = max_gid + 1
        return {
            "version": 1,
            "experience": _experience_value(state.get("experience", 0)),
            "wallet": {
                "ap": max(0, int(wallet.get("ap", 0))),
                "gp": max(0, int(wallet.get("gp", 0))),
                "mp": max(0, int(wallet.get("mp", 0))),
            },
            "current_role_gid": _i64(state.get("current_role_gid", 0)),
            "current_bag_gid": _i64(state.get("current_bag_gid", 0)),
            "next_gid": _i64(next_gid),
            "inventory": inventory,
        }

    def ensure_player_state(
        self,
        uin: int,
        default_state: Mapping[str, Any],
        *,
        legacy_state: Mapping[str, Any] | None = None,
        legacy_source: str | None = None,
    ) -> tuple[dict[str, Any], bool, bool]:
        """Ensure state exists. Returns (state, created, imported_legacy)."""
        uin = int(uin)
        if self.has_player_state(uin):
            return self.load_player_state(uin), False, False

        imported_legacy = False
        seed = dict(default_state)
        self.init_schema()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            # Re-check under the write lock.
            row = conn.execute(
                "SELECT 1 FROM player_profiles WHERE uin = ?",
                (uin,),
            ).fetchone()
            if row:
                conn.commit()
                return self.load_player_state(uin), False, False

            marker = conn.execute(
                "SELECT value FROM meta WHERE key='legacy_mall_state_imported'"
            ).fetchone()
            if legacy_state is not None and marker is None:
                seed = dict(legacy_state)
                imported_legacy = True

            normalized = self._normalize_state(seed)
            now = _utc_now()
            nickname = None
            conn.execute(
                """
                INSERT INTO player_profiles(
                    uin, nickname, experience, current_role_gid, current_bag_gid,
                    next_gid, created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    uin,
                    nickname,
                    normalized["experience"],
                    normalized["current_role_gid"],
                    normalized["current_bag_gid"],
                    normalized["next_gid"],
                    now,
                    now,
                ),
            )
            conn.execute(
                "INSERT INTO player_wallets(uin, ap, gp, mp, updated_at) VALUES(?, ?, ?, ?, ?)",
                (
                    uin,
                    normalized["wallet"]["ap"],
                    normalized["wallet"]["gp"],
                    normalized["wallet"]["mp"],
                    now,
                ),
            )
            self._replace_inventory_conn(conn, uin, normalized["inventory"])
            if imported_legacy:
                conn.execute(
                    "INSERT INTO meta(key, value) VALUES('legacy_mall_state_imported', ?)",
                    (json.dumps({"uin": uin, "source": legacy_source or "legacy-json", "at": now}),),
                )
            conn.commit()
            return self.load_player_state(uin), True, imported_legacy
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _replace_inventory_conn(self, conn: sqlite3.Connection, uin: int, inventory) -> None:
        conn.execute("DELETE FROM player_inventory WHERE uin = ?", (int(uin),))
        rows = []
        seen = set()
        for raw in inventory:
            p = dict(raw)
            gid = _i64(p.get("gid", 0))
            if gid <= 0 or gid in seen:
                raise PlayerDBError(f"invalid/duplicate inventory gid {gid}")
            seen.add(gid)
            rows.append(
                (
                    int(uin),
                    gid,
                    int(p.get("item_id", 0)),
                    _i64(p.get("owner_gid", 0)),
                    int(p.get("location", 0)),
                    max(0, int(p.get("durability", 0))),
                    max(0, int(p.get("durability_max", 0))),
                    int(p.get("avail_hours", 0)),
                    int(p.get("validity", 0)),
                    max(0, int(p.get("gain_type", 1))),
                    max(0, int(p.get("obtained_at", 0) or 0)),
                    max(0, int(p.get("expires_at", 0) or 0)),
                )
            )
        conn.executemany(
            """
            INSERT INTO player_inventory(
                uin, gid, item_id, owner_gid, location, durability,
                durability_max, avail_hours, validity, gain_type,
                obtained_at, expires_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )

    def load_player_state(self, uin: int) -> dict[str, Any]:
        uin = int(uin)
        conn = self._connect()
        try:
            p = conn.execute(
                """
                SELECT nickname, Experience AS experience, current_role_gid,
                       current_bag_gid, next_gid
                FROM player_profiles WHERE uin = ?
                """,
                (uin,),
            ).fetchone()
            w = conn.execute(
                "SELECT ap, gp, mp FROM player_wallets WHERE uin = ?",
                (uin,),
            ).fetchone()
            if not p or not w:
                raise PlayerDBError(f"player state missing for uin={uin}")
            inv = conn.execute(
                """
                SELECT gid, item_id, owner_gid, location, durability,
                       durability_max, avail_hours, validity, gain_type,
                       obtained_at, expires_at
                FROM player_inventory WHERE uin = ? ORDER BY gid
                """,
                (uin,),
            ).fetchall()
            return {
                "version": 1,
                "nickname": p["nickname"],
                "experience": _experience_value(p["experience"]),
                "wallet": {"ap": int(w["ap"]), "gp": int(w["gp"]), "mp": int(w["mp"])},
                "current_role_gid": int(p["current_role_gid"]),
                "current_bag_gid": int(p["current_bag_gid"]),
                "next_gid": int(p["next_gid"]),
                "inventory": [
                    {
                        "gid": int(r["gid"]),
                        "item_id": int(r["item_id"]),
                        "owner_gid": int(r["owner_gid"]),
                        "location": int(r["location"]),
                        "durability": int(r["durability"]),
                        "durability_max": int(r["durability_max"]),
                        "avail_hours": int(r["avail_hours"]),
                        "validity": int(r["validity"]),
                        "gain_type": int(r["gain_type"]),
                        "obtained_at": int(r["obtained_at"]),
                        "expires_at": int(r["expires_at"]),
                    }
                    for r in inv
                ],
            }
        finally:
            conn.close()

    @staticmethod
    def _normalize_moneyflow_rows(rows) -> list[dict[str, Any]]:
        normalized = []
        for raw in rows or ():
            row = dict(raw)
            money_type = int(row.get("money_type", 0))
            if money_type not in (1, 2, 3):
                raise PlayerDBError(
                    f"invalid moneyflow money_type={money_type}"
                )
            reason = int(row.get("reason", 0))
            if reason < 0 or reason > 0xFF:
                raise PlayerDBError(
                    f"invalid moneyflow reason={reason}"
                )
            occurred_at = int(row.get("occurred_at", 0))
            if occurred_at < 0:
                raise PlayerDBError(
                    f"invalid moneyflow occurred_at={occurred_at}"
                )
            current_balance = int(row.get("current", row.get("current_balance", 0)))
            if current_balance < 0:
                raise PlayerDBError(
                    f"invalid moneyflow current_balance={current_balance}"
                )
            details = str(row.get("details", "") or "")
            if len(details) > 512:
                details = details[:512]
            commodity_ids = [
                int(value)
                for value in (row.get("commodity_ids") or ())
            ]
            normalized.append(
                {
                    "occurred_at": occurred_at,
                    "money_type": money_type,
                    "number": int(row.get("number", 0)),
                    "current_balance": current_balance,
                    "reason": reason,
                    "details": details,
                    "commodity_ids": commodity_ids,
                }
            )
        return normalized

    def load_moneyflow(self, uin: int, *, limit: int = 900) -> list[dict[str, Any]]:
        """Load the newest persisted Consumer List rows in chronological order."""
        uin = int(uin)
        limit = max(0, min(int(limit), 5000))
        if limit == 0:
            return []
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT id, occurred_at, money_type, number, current_balance,
                       reason, details, commodity_ids
                FROM (
                    SELECT id, occurred_at, money_type, number, current_balance,
                           reason, details, commodity_ids
                    FROM player_moneyflow
                    WHERE uin = ?
                    ORDER BY id DESC
                    LIMIT ?
                )
                ORDER BY id ASC
                """,
                (uin, limit),
            ).fetchall()
            out = []
            for row in rows:
                try:
                    commodity_ids = json.loads(str(row["commodity_ids"] or "[]"))
                except (TypeError, ValueError, json.JSONDecodeError):
                    commodity_ids = []
                if not isinstance(commodity_ids, list):
                    commodity_ids = []
                out.append(
                    {
                        "id": int(row["id"]),
                        "occurred_at": int(row["occurred_at"]),
                        "money_type": int(row["money_type"]),
                        "number": int(row["number"]),
                        "current": int(row["current_balance"]),
                        "reason": int(row["reason"]),
                        "details": str(row["details"] or ""),
                        "commodity_ids": [
                            int(value) for value in commodity_ids
                        ],
                    }
                )
            return out
        finally:
            conn.close()

    def save_player_state(
        self,
        uin: int,
        state: Mapping[str, Any],
        *,
        reason: str = "update",
        moneyflow_rows=None,
    ) -> None:
        uin = int(uin)
        normalized = self._normalize_state(state)
        normalized_moneyflow = self._normalize_moneyflow_rows(moneyflow_rows)
        now = _utc_now()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT nickname, created_at FROM player_profiles WHERE uin = ?",
                (uin,),
            ).fetchone()
            nickname = existing["nickname"] if existing else None
            created_at = existing["created_at"] if existing else now
            conn.execute(
                """
                INSERT INTO player_profiles(
                    uin, nickname, experience, current_role_gid, current_bag_gid,
                    next_gid, created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(uin) DO UPDATE SET
                    experience=excluded.experience,
                    current_role_gid=excluded.current_role_gid,
                    current_bag_gid=excluded.current_bag_gid,
                    next_gid=excluded.next_gid,
                    updated_at=excluded.updated_at
                """,
                (
                    uin,
                    nickname,
                    normalized["experience"],
                    normalized["current_role_gid"],
                    normalized["current_bag_gid"],
                    normalized["next_gid"],
                    created_at,
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO player_wallets(uin, ap, gp, mp, updated_at)
                VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(uin) DO UPDATE SET
                    ap=excluded.ap, gp=excluded.gp, mp=excluded.mp,
                    updated_at=excluded.updated_at
                """,
                (
                    uin,
                    normalized["wallet"]["ap"],
                    normalized["wallet"]["gp"],
                    normalized["wallet"]["mp"],
                    now,
                ),
            )
            # Wallet + inventory + role/bag + Consumer List rows commit
            # in the same SQLite transaction. A failed purchase never reaches
            # this call, so it cannot create history.
            self._replace_inventory_conn(conn, uin, normalized["inventory"])
            if normalized_moneyflow:
                conn.executemany(
                    """
                    INSERT INTO player_moneyflow(
                        uin, occurred_at, money_type, number, current_balance,
                        reason, details, commodity_ids, created_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            uin,
                            row["occurred_at"],
                            row["money_type"],
                            row["number"],
                            row["current_balance"],
                            row["reason"],
                            row["details"],
                            json.dumps(
                                row["commodity_ids"],
                                separators=(",", ":"),
                            ),
                            now,
                        )
                        for row in normalized_moneyflow
                    ],
                )
            conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)",
                (f"player_state_last_save:{uin}", json.dumps({"reason": str(reason), "at": now})),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def load_nickname(self, uin: int) -> str | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT nickname FROM player_profiles WHERE uin = ?",
                (int(uin),),
            ).fetchone()
            if not row or row["nickname"] is None:
                return None
            value = str(row["nickname"]).strip()
            return value or None
        finally:
            conn.close()

    @staticmethod
    def _normalize_nickname(nickname: str) -> str:
        value = str(nickname or "").strip()
        if not value or len(value) > 31 or "\x00" in value:
            raise PlayerDBError("invalid nickname")
        return value

    @staticmethod
    def _nickname_owner_uin(
        conn: sqlite3.Connection,
        nickname: str,
        *,
        exclude_uin: int | None = None,
    ) -> int | None:
        """Find a casefold-equivalent nickname using Python Unicode rules.

        SQLite's built-in NOCASE collation only folds ASCII. PH accepts
        single-byte accented nicknames, so uniqueness must use the same
        casefold comparison as the server's nickname flow.
        """
        key = nickname.casefold()
        rows = conn.execute(
            """SELECT uin, nickname FROM player_profiles
               WHERE nickname IS NOT NULL AND nickname <> ''"""
        )
        for row in rows:
            uin = int(row["uin"])
            if exclude_uin is not None and uin == int(exclude_uin):
                continue
            if str(row["nickname"]).casefold() == key:
                return uin
        return None

    def nickname_available(
        self,
        nickname: str,
        *,
        exclude_uin: int | None = None,
    ) -> bool:
        try:
            nickname = self._normalize_nickname(nickname)
        except PlayerDBError:
            return False

        conn = self._connect()
        try:
            return self._nickname_owner_uin(
                conn,
                nickname,
                exclude_uin=exclude_uin,
            ) is None
        finally:
            conn.close()

    def claim_nickname(
        self,
        uin: int,
        nickname: str,
        *,
        require_unclaimed: bool = False,
    ) -> str:
        """Atomically claim a nickname for one player.

        BEGIN IMMEDIATE serializes nickname writers before the availability
        check, so two concurrent A002/F301 requests cannot both claim the same
        case-insensitive nickname. Re-claiming the same nickname for the same
        UIN is intentionally idempotent. First-login callers can require that
        the existing profile is still blank, preventing stale sessions from
        overwriting a nickname claimed by another session for the same UIN.
        """
        uin = int(uin)
        nickname = self._normalize_nickname(nickname)
        now = _utc_now()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")

            current = conn.execute(
                "SELECT nickname FROM player_profiles WHERE uin = ?",
                (uin,),
            ).fetchone()
            if not current:
                raise PlayerDBError(f"profile missing for uin={uin}")

            other_uin = self._nickname_owner_uin(
                conn,
                nickname,
                exclude_uin=uin,
            )
            if other_uin is not None:
                raise PlayerDBError(
                    f"nickname unavailable: {nickname!r} "
                    f"already owned by uin={other_uin}"
                )

            current_value = (
                str(current["nickname"]).strip()
                if current["nickname"] is not None
                else ""
            )
            if (
                require_unclaimed
                and current_value
                and current_value.casefold() != nickname.casefold()
            ):
                raise PlayerDBError(
                    f"first nickname already claimed for uin={uin}"
                )

            if current_value != nickname:
                conn.execute(
                    """
                    UPDATE player_profiles
                    SET nickname=?, updated_at=?
                    WHERE uin=?
                    """,
                    (nickname, now, uin),
                )

            # Keep the website/account profile mirror coherent when that table
            # exists for this UIN. player_profiles remains the game authority.
            has_profiles = conn.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type='table' AND name='profiles'
                """
            ).fetchone()
            if has_profiles:
                conn.execute(
                    "UPDATE profiles SET nickname=? WHERE uin=?",
                    (nickname, uin),
                )

            conn.commit()
            return nickname
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def save_nickname(self, uin: int, nickname: str) -> str:
        """Compatibility wrapper; all nickname writes use atomic claiming."""
        return self.claim_nickname(uin, nickname)

    def counts(self) -> dict[str, int]:
        conn = self._connect()
        try:
            out = {}
            for table in (
                "game_identities",
                "player_profiles",
                "player_wallets",
                "player_inventory",
                "player_moneyflow",
            ):
                row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
                out[table] = int(row["n"])
            return out
        finally:
            conn.close()
