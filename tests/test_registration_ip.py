from pathlib import Path
import inspect
import sqlite3
import tempfile
import unittest

from server import account_db


class RegistrationIPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "accounts.sqlite3"
        self.old_rounds = account_db.PBKDF2_ITERATIONS
        account_db.PBKDF2_ITERATIONS = 1000

    def tearDown(self):
        account_db.PBKDF2_ITERATIONS = self.old_rounds
        self.tmp.cleanup()

    def stored_ip(self, username):
        conn = account_db.connect(self.db)
        try:
            row = conn.execute(
                "SELECT registration_ip FROM accounts WHERE username_norm=?",
                (username.casefold(),),
            ).fetchone()
            return row["registration_ip"]
        finally:
            conn.close()

    def test_ip_is_stored_and_returned(self):
        acct = account_db.create_account(
            "alpha", "password1", db_path=self.db, registration_ip="203.0.113.7"
        )
        self.assertEqual(acct["registration_ip"], "203.0.113.7")
        self.assertEqual(self.stored_ip("alpha"), "203.0.113.7")

    def test_ip_is_optional(self):
        acct = account_db.create_account("bravo", "password1", db_path=self.db)
        self.assertIsNone(acct["registration_ip"])
        self.assertIsNone(self.stored_ip("bravo"))

    def test_blank_ip_is_treated_as_not_provided(self):
        for value in (None, "", "   "):
            self.assertIsNone(account_db.normalize_registration_ip(value))

    def test_ipv6_is_stored_in_canonical_form(self):
        acct = account_db.create_account(
            "charlie", "password1", db_path=self.db,
            registration_ip="2001:0DB8:0000:0000:0000:0000:0000:0001",
        )
        self.assertEqual(acct["registration_ip"], "2001:db8::1")

    def test_invalid_ip_is_rejected_and_creates_no_account(self):
        for bad in ("not-an-ip", "1.2.3.4, 5.6.7.8", "999.1.1.1", "1.2.3.4:80"):
            with self.assertRaises(account_db.InvalidRegistrationIP):
                account_db.create_account(
                    "delta", "password1", db_path=self.db, registration_ip=bad
                )
        self.assertEqual(account_db.account_count(db_path=self.db), 0)

    def test_invalid_ip_is_an_account_error_for_the_web_form(self):
        self.assertTrue(
            issubclass(account_db.InvalidRegistrationIP, account_db.AccountError)
        )

    def test_existing_database_without_the_column_is_migrated(self):
        conn = sqlite3.connect(self.db)
        conn.executescript(
            """
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                uin INTEGER NOT NULL UNIQUE CHECK (uin >= 10001),
                username TEXT NOT NULL,
                username_norm TEXT NOT NULL UNIQUE,
                password_hash BLOB NOT NULL,
                password_salt BLOB NOT NULL,
                password_iterations INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                last_login_at TEXT
            );
            CREATE TABLE profiles (
                uin INTEGER PRIMARY KEY, nickname TEXT, created_at TEXT NOT NULL
            );
            INSERT INTO accounts(uin, username, username_norm, password_hash,
                password_salt, password_iterations, created_at)
            VALUES (10001, 'Old', 'old', x'00', x'00', 1, '2020-01-01');
            """
        )
        conn.commit()
        conn.close()

        account_db.init_db(self.db)
        self.assertIsNone(self.stored_ip("old"))
        acct = account_db.create_account(
            "newer", "password1", db_path=self.db, registration_ip="198.51.100.2"
        )
        self.assertEqual(acct["registration_ip"], "198.51.100.2")
        self.assertEqual(acct["uin"], 10002)

    def test_web_hook_now_receives_the_ip(self):
        # web/app.py only passes registration_ip when create_account accepts it.
        params = inspect.signature(account_db.create_account).parameters
        self.assertIn("registration_ip", params)
        self.assertIsNone(params["registration_ip"].default)


if __name__ == "__main__":
    unittest.main()
