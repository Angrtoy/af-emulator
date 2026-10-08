"""SQLite account storage for the Assault Fire Server Emulator.

This module is intentionally independent from the network protocol so local
account tooling and future frontends can share one authoritative account DB.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_DB_PATH = Path(
    os.environ.get(
        "AF_ACCOUNT_DB",
        str(Path(__file__).with_name("assaultfire_accounts.sqlite3")),
    )
)

USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{3,24}$")
AP_TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")
PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 72
PBKDF2_ITERATIONS = int(os.environ.get("AF_PASSWORD_ITERATIONS", "600000"))
FIRST_UIN = 10001


class AccountError(ValueError):
    """Base class for account validation/storage errors."""


class InvalidUsername(AccountError):
    pass


class InvalidPassword(AccountError):
    pass


class DuplicateUsername(AccountError):
    pass


class InvalidRegistrationIP(AccountError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_username(username: str) -> str:
    username = (username or "").strip()
    if not USERNAME_RE.fullmatch(username):
        raise InvalidUsername(
            "Username must be 3-24 characters and use only letters, numbers, '.', '_' or '-'."
        )
    return username.casefold()


def validate_password(password: str) -> None:
    if not isinstance(password, str):
        raise InvalidPassword("Password is required.")
    if len(password) < PASSWORD_MIN_LENGTH:
        raise InvalidPassword(
            f"Password must be at least {PASSWORD_MIN_LENGTH} characters."
        )
    if len(password) > PASSWORD_MAX_LENGTH:
        raise InvalidPassword(
            f"Password must be at most {PASSWORD_MAX_LENGTH} characters."
        )


def normalize_registration_ip(value: Any) -> str | None:
    """Return the canonical text form of an IPv4/IPv6 address, or None.

    ``None`` and blank strings mean "not provided". Anything else must be a
    single valid IP address; a proxy header list such as "1.2.3.4, 5.6.7.8"
    must be reduced to the client address by the caller.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return str(ipaddress.ip_address(text))
    except ValueError as exc:
        raise InvalidRegistrationIP(
            "registration_ip must be a single valid IPv4 or IPv6 address."
        ) from exc


def _hash_password(
    password: str,
    *,
    salt: bytes | None = None,
    iterations: int = PBKDF2_ITERATIONS,
) -> tuple[bytes, bytes, int]:
    validate_password(password)
    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
    )
    return digest, salt, iterations


def _hash_ap_password_token(
    password: str,
    *,
    iterations: int | None = None,
) -> tuple[bytes, bytes, int]:
    """Hash the legacy PH AP token without storing its raw MD5 value."""
    token = hashlib.md5(password.encode("utf-8")).hexdigest()
    return _hash_password(
        token,
        iterations=PBKDF2_ITERATIONS if iterations is None else iterations,
    )


def connect(db_path: str | os.PathLike[str] | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(db_path: str | os.PathLike[str] | None = None) -> Path:
    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    conn = connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                uin INTEGER NOT NULL UNIQUE CHECK (uin >= 10001),
                username TEXT NOT NULL,
                username_norm TEXT NOT NULL UNIQUE,
                password_hash BLOB NOT NULL,
                password_salt BLOB NOT NULL,
                password_iterations INTEGER NOT NULL,
                ap_token_hash BLOB,
                ap_token_salt BLOB,
                ap_token_iterations INTEGER,
                status TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'disabled')),
                created_at TEXT NOT NULL,
                last_login_at TEXT
            );

            CREATE TABLE IF NOT EXISTS profiles (
                uin INTEGER PRIMARY KEY,
                nickname TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (uin) REFERENCES accounts(uin) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_accounts_status
                ON accounts(status);
            """
        )
        # These nullable columns also migrate account databases created before
        # native PH AP password verification was enabled. A successful website
        # login backfills the verifier for an existing account.
        account_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(accounts)")
        }
        for column, sql_type in (
            ("ap_token_hash", "BLOB"),
            ("ap_token_salt", "BLOB"),
            ("ap_token_iterations", "INTEGER"),
            ("registration_ip", "TEXT"),
        ):
            if column not in account_columns:
                conn.execute(f"ALTER TABLE accounts ADD COLUMN {column} {sql_type}")
        conn.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES('schema_version', '1')"
        )
        conn.commit()
    finally:
        conn.close()
    return path


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (str(name),),
        ).fetchone()
    )


def _allocate_uin_for_username(conn: sqlite3.Connection, username_norm: str) -> int:
    """Allocate from the shared account/game-identity UIN namespace.

    If this login already has a local game identity, registration adopts that
    same UIN so the nickname/wallet/inventory profile remains attached to the
    account instead of creating a second identity.
    """
    if _table_exists(conn, "game_identities"):
        mapped = conn.execute(
            "SELECT uin FROM game_identities WHERE login_key = ?",
            (username_norm,),
        ).fetchone()
        if mapped:
            mapped_uin = int(mapped["uin"])
            occupied = conn.execute(
                "SELECT username_norm FROM accounts WHERE uin = ?",
                (mapped_uin,),
            ).fetchone()
            if occupied and str(occupied["username_norm"]) != username_norm:
                raise sqlite3.IntegrityError(
                    f"mapped game UIN {mapped_uin} already belongs to another account"
                )
            return mapped_uin

    row = conn.execute("SELECT MAX(uin) AS max_uin FROM accounts").fetchone()
    max_uin = row["max_uin"] if row and row["max_uin"] is not None else FIRST_UIN - 1

    if _table_exists(conn, "game_identities"):
        grow = conn.execute("SELECT MAX(uin) AS max_uin FROM game_identities").fetchone()
        if grow and grow["max_uin"] is not None:
            max_uin = max(int(max_uin), int(grow["max_uin"]))

    return max(FIRST_UIN, int(max_uin) + 1)


def create_account(
    username: str,
    password: str,
    *,
    db_path: str | os.PathLike[str] | None = None,
    registration_ip: str | None = None,
) -> dict[str, Any]:
    username = (username or "").strip()
    username_norm = normalize_username(username)
    registration_ip = normalize_registration_ip(registration_ip)
    digest, salt, iterations = _hash_password(
        password,
        iterations=PBKDF2_ITERATIONS,
    )
    ap_digest, ap_salt, ap_iterations = _hash_ap_password_token(
        password,
        iterations=PBKDF2_ITERATIONS,
    )
    created_at = _utc_now()

    init_db(db_path)
    conn = connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")

        existing = conn.execute(
            "SELECT 1 FROM accounts WHERE username_norm = ?",
            (username_norm,),
        ).fetchone()
        if existing:
            raise DuplicateUsername("That username is already registered.")

        uin = _allocate_uin_for_username(conn, username_norm)

        conn.execute(
            """
            INSERT INTO accounts(
                uin, username, username_norm,
                password_hash, password_salt, password_iterations,
                ap_token_hash, ap_token_salt, ap_token_iterations,
                status, created_at, registration_ip
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
            """,
            (
                uin,
                username,
                username_norm,
                digest,
                salt,
                iterations,
                ap_digest,
                ap_salt,
                ap_iterations,
                created_at,
                registration_ip,
            ),
        )
        conn.execute(
            "INSERT INTO profiles(uin, nickname, created_at) VALUES(?, NULL, ?)",
            (uin, created_at),
        )
        conn.commit()
        return {
            "uin": uin,
            "username": username,
            "status": "active",
            "created_at": created_at,
            "registration_ip": registration_ip,
        }
    except DuplicateUsername:
        conn.rollback()
        raise
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        if "username_norm" in str(exc).lower():
            raise DuplicateUsername("That username is already registered.") from exc
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_account_by_username(
    username: str,
    *,
    db_path: str | os.PathLike[str] | None = None,
) -> dict[str, Any] | None:
    try:
        username_norm = normalize_username(username)
    except InvalidUsername:
        return None

    init_db(db_path)
    conn = connect(db_path)
    try:
        row = conn.execute(
            """
            SELECT id, uin, username, username_norm, status,
                   created_at, last_login_at
            FROM accounts
            WHERE username_norm = ?
            """,
            (username_norm,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def verify_account(
    username: str,
    password: str,
    *,
    db_path: str | os.PathLike[str] | None = None,
    update_last_login: bool = False,
) -> dict[str, Any] | None:
    try:
        username_norm = normalize_username(username)
        validate_password(password)
    except AccountError:
        return None

    init_db(db_path)
    conn = connect(db_path)
    try:
        row = conn.execute(
            """
            SELECT id, uin, username, username_norm, status, created_at,
                   last_login_at, password_hash, password_salt,
                   password_iterations, ap_token_hash, ap_token_salt,
                   ap_token_iterations
            FROM accounts
            WHERE username_norm = ?
            """,
            (username_norm,),
        ).fetchone()

        if not row or row["status"] != "active":
            return None

        candidate = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes(row["password_salt"]),
            int(row["password_iterations"]),
        )
        if not hmac.compare_digest(candidate, bytes(row["password_hash"])):
            return None

        if update_last_login:
            last_login_at = _utc_now()
            if (
                row["ap_token_hash"] is None
                or row["ap_token_salt"] is None
                or row["ap_token_iterations"] is None
            ):
                ap_digest, ap_salt, ap_iterations = _hash_ap_password_token(
                    password
                )
                conn.execute(
                    """UPDATE accounts
                       SET last_login_at = ?, ap_token_hash = ?,
                           ap_token_salt = ?, ap_token_iterations = ?
                       WHERE id = ?""",
                    (
                        last_login_at,
                        ap_digest,
                        ap_salt,
                        ap_iterations,
                        row["id"],
                    ),
                )
            else:
                conn.execute(
                    "UPDATE accounts SET last_login_at = ? WHERE id = ?",
                    (last_login_at, row["id"]),
                )
            conn.commit()
        else:
            last_login_at = row["last_login_at"]

        return {
            "id": row["id"],
            "uin": row["uin"],
            "username": row["username"],
            "status": row["status"],
            "created_at": row["created_at"],
            "last_login_at": last_login_at,
        }
    finally:
        conn.close()


def verify_ap_credential(
    username: str,
    credential_token: str,
    *,
    db_path: str | os.PathLike[str] | None = None,
    update_last_login: bool = True,
) -> dict[str, Any] | None:
    """Verify the 32-hex MD5 token sent by PH's native AP login request.

    The game protocol does not send the plaintext password. Account creation
    therefore stores a salted PBKDF2 verifier of the protocol token separately
    from the website password verifier.
    """
    try:
        username_norm = normalize_username(username)
    except AccountError:
        return None

    token = str(credential_token or "").strip().lower()
    if not AP_TOKEN_RE.fullmatch(token):
        return None

    init_db(db_path)
    conn = connect(db_path)
    try:
        if _table_exists(conn, "account_bans"):
            row = conn.execute(
                """
                SELECT a.id, a.uin, a.username, a.status, a.created_at,
                       a.last_login_at, a.ap_token_hash, a.ap_token_salt,
                       a.ap_token_iterations
                FROM accounts a
                LEFT JOIN account_bans b ON b.uin = a.uin
                WHERE a.username_norm = ? AND b.uin IS NULL
                """,
                (username_norm,),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT id, uin, username, status, created_at, last_login_at,
                       ap_token_hash, ap_token_salt, ap_token_iterations
                FROM accounts
                WHERE username_norm = ?
                """,
                (username_norm,),
            ).fetchone()

        if (
            not row
            or row["status"] != "active"
            or row["ap_token_hash"] is None
            or row["ap_token_salt"] is None
            or row["ap_token_iterations"] is None
        ):
            return None

        candidate = hashlib.pbkdf2_hmac(
            "sha256",
            token.encode("ascii"),
            bytes(row["ap_token_salt"]),
            int(row["ap_token_iterations"]),
        )
        if not hmac.compare_digest(candidate, bytes(row["ap_token_hash"])):
            return None

        last_login_at = row["last_login_at"]
        if update_last_login:
            last_login_at = _utc_now()
            conn.execute(
                "UPDATE accounts SET last_login_at = ? WHERE id = ?",
                (last_login_at, row["id"]),
            )
            conn.commit()

        return {
            "id": row["id"],
            "uin": row["uin"],
            "username": row["username"],
            "status": row["status"],
            "created_at": row["created_at"],
            "last_login_at": last_login_at,
        }
    finally:
        conn.close()


def account_count(
    *, db_path: str | os.PathLike[str] | None = None
) -> int:
    init_db(db_path)
    conn = connect(db_path)
    try:
        row = conn.execute("SELECT COUNT(*) AS n FROM accounts").fetchone()
        return int(row["n"])
    finally:
        conn.close()
