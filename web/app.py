"""Development-only local account website for the Assault Fire emulator.

This site is loopback-only and is started automatically only in development
mode. Production mode never starts it.
"""

from __future__ import annotations

import hmac
import html
import inspect
import os
import secrets
import sqlite3
import sys
from pathlib import Path

from flask import Flask, abort, jsonify, redirect, request, session, url_for

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server import account_db  # noqa: E402

DB_PATH = Path(
    os.environ.get(
        "AF_ACCOUNT_DB",
        str(ROOT / "server" / "assaultfire_accounts.sqlite3"),
    )
).resolve()

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("AF_WEB_SECRET") or secrets.token_hex(32),
    MAX_CONTENT_LENGTH=16 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    ADMIN_USERNAME=os.environ.get("AF_ADMIN_USERNAME", "admin"),
    ADMIN_PASSWORD=os.environ.get("AF_ADMIN_PASSWORD", ""),
)


def _esc(value) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _csrf() -> str:
    token = session.get("_csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf"] = token
    return token


def _valid_csrf() -> bool:
    sent = request.form.get("csrf_token", "")
    expected = session.get("_csrf", "")
    return bool(
        sent
        and expected
        and hmac.compare_digest(str(sent), str(expected))
    )


def _client_ip() -> str:
    return str(request.remote_addr or "127.0.0.1")


def _page(title: str, body: str) -> str:
    nav = (
        '<nav><a href="/register">Register</a> · '
        '<a href="/login">Login</a> · '
        '<a href="/status">Status</a></nav>'
    )
    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(title)} - Assault Fire Dev</title>
<style>
body{{font-family:Segoe UI,Arial,sans-serif;background:#101820;color:#e7f6ff;margin:0}}
main{{max-width:980px;margin:48px auto;padding:28px;background:#172733;border:1px solid #2c6175;border-radius:10px}}
h1,h2{{color:#62d9ff}} input,button{{font:inherit;padding:9px;margin:5px 0}}
input{{width:min(100%,420px);box-sizing:border-box}}
button{{background:#1d819e;color:white;border:0;border-radius:5px;cursor:pointer}}
a{{color:#6ee7ff}} .err{{color:#ff9b9b}} .ok{{color:#8dffa5}}
table{{width:100%;border-collapse:collapse}} th,td{{text-align:left;border-bottom:1px solid #32515d;padding:8px}}
small{{color:#a9bec8}} code{{color:#8dffa5}}
.ap-form{{display:flex;gap:6px;align-items:center;white-space:nowrap}}
.ap-form input{{width:125px;margin:0}}
.ap-form button{{margin:0;white-space:nowrap}}
.muted{{color:#a9bec8}}
</style>
</head>
<body><main>{nav}<h1>{_esc(title)}</h1>{body}</main></body>
</html>"""


def _form_token() -> str:
    return f'<input type="hidden" name="csrf_token" value="{_esc(_csrf())}">'


def _connect() -> sqlite3.Connection:
    account_db.init_db(DB_PATH)
    conn = sqlite3.connect(DB_PATH, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def _account_rows() -> list[dict]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT uin,username,status,created_at,last_login_at "
            "FROM accounts ORDER BY uin"
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def _nickname_for_uin(uin: int) -> str | None:
    conn = _connect()
    try:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='player_profiles'"
        ).fetchone()
        if exists:
            row = conn.execute(
                "SELECT nickname FROM player_profiles WHERE uin=?",
                (int(uin),),
            ).fetchone()
            if row and row["nickname"]:
                return str(row["nickname"])
        row = conn.execute(
            "SELECT nickname FROM profiles WHERE uin=?",
            (int(uin),),
        ).fetchone()
        return str(row["nickname"]) if row and row["nickname"] else None
    finally:
        conn.close()


def _wallet_for_uin(uin: int) -> dict[str, int] | None:
    """Return persisted AP/GP/MP for an initialized game wallet."""
    conn = _connect()
    try:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='player_wallets'"
        ).fetchone()
        if not exists:
            return None
        row = conn.execute(
            "SELECT ap,gp,mp FROM player_wallets WHERE uin=?",
            (int(uin),),
        ).fetchone()
        if row is None:
            return None
        return {
            "ap": int(row["ap"]),
            "gp": int(row["gp"]),
            "mp": int(row["mp"]),
        }
    finally:
        conn.close()


def _set_wallet_for_uin(uin: int, *, ap: int, gp: int, mp: int) -> None:
    """Update the persisted wallet without interrupting the active game session."""
    values = {"ap": int(ap), "gp": int(gp), "mp": int(mp)}
    if any(value < 0 or value > 2_147_483_647 for value in values.values()):
        raise ValueError("AP, GP and MP must be between 0 and 2147483647")

    conn = _connect()
    try:
        account = conn.execute(
            "SELECT 1 FROM accounts WHERE uin=?",
            (int(uin),),
        ).fetchone()
        if not account:
            raise ValueError("account not found")

        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='player_wallets'"
        ).fetchone()
        if not exists:
            raise ValueError(
                "player wallet is not initialized yet; sign in to the game once first"
            )

        cur = conn.execute(
            "UPDATE player_wallets "
            "SET ap=?, gp=?, mp=?, updated_at=CURRENT_TIMESTAMP "
            "WHERE uin=?",
            (values["ap"], values["gp"], values["mp"], int(uin)),
        )
        if cur.rowcount != 1:
            raise ValueError(
                "player wallet is not initialized yet; sign in to the game once first"
            )
        conn.commit()
    finally:
        conn.close()


def _set_account_status(uin: int, status: str) -> None:
    if status not in {"active", "disabled"}:
        raise ValueError("invalid account status")
    conn = _connect()
    try:
        cur = conn.execute(
            "UPDATE accounts SET status=? WHERE uin=?",
            (status, int(uin)),
        )
        if cur.rowcount != 1:
            raise ValueError("account not found")
        conn.commit()
    finally:
        conn.close()


def _record_web_ip(uin: int, ip: str, *, login: bool) -> None:
    conn = _connect()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS web_registration_meta(
                uin INTEGER PRIMARY KEY,
                registration_ip TEXT,
                last_web_login_ip TEXT,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        if login:
            conn.execute(
                """
                INSERT INTO web_registration_meta(
                    uin,registration_ip,last_web_login_ip,updated_at
                ) VALUES(?,NULL,?,CURRENT_TIMESTAMP)
                ON CONFLICT(uin) DO UPDATE SET
                    last_web_login_ip=excluded.last_web_login_ip,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (int(uin), str(ip)),
            )
        else:
            conn.execute(
                """
                INSERT INTO web_registration_meta(
                    uin,registration_ip,last_web_login_ip,updated_at
                ) VALUES(?,?,NULL,CURRENT_TIMESTAMP)
                ON CONFLICT(uin) DO UPDATE SET
                    registration_ip=COALESCE(
                        web_registration_meta.registration_ip,
                        excluded.registration_ip
                    ),
                    updated_at=CURRENT_TIMESTAMP
                """,
                (int(uin), str(ip)),
            )
        conn.commit()
    finally:
        conn.close()


@app.after_request
def _security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; style-src 'unsafe-inline'; "
        "form-action 'self'; frame-ancestors 'none'"
    )
    return response


@app.get("/")
def root():
    return redirect(url_for("register"))


@app.route("/register", methods=["GET", "POST"])
def register():
    error = ""
    registered = None
    username = ""
    if request.method == "POST":
        if not _valid_csrf():
            error = "Registration form expired. Refresh and try again."
        else:
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            confirm = request.form.get("confirm_password", "")
            if password != confirm:
                error = "Passwords do not match."
            else:
                try:
                    kwargs = {"db_path": DB_PATH}
                    sig = inspect.signature(account_db.create_account)
                    if "registration_ip" in sig.parameters:
                        kwargs["registration_ip"] = _client_ip()
                    registered = account_db.create_account(
                        username, password, **kwargs
                    )
                    _record_web_ip(
                        int(registered["uin"]), _client_ip(), login=False
                    )
                    session["_csrf"] = secrets.token_urlsafe(32)
                except account_db.AccountError as exc:
                    error = str(exc)

    message = ""
    if registered:
        message = (
            '<p class="ok"><strong>Registration complete.</strong></p>'
            f'<p>UIN: <code>{int(registered["uin"])}</code></p>'
        )
    body = f"""
{message}
{f'<p class="err">{_esc(error)}</p>' if error else ''}
<form method="post">
{_form_token()}
<label>Username</label><br>
<input name="username" value="{_esc(username)}" autocomplete="username"
       minlength="3" maxlength="24" pattern="[A-Za-z0-9._-]+"
       title="3-24 characters: letters, numbers, dot, underscore or hyphen"
       required><br>
<small>Username: 3-24 characters. Use letters, numbers, ., _ or -.</small><br>
<label>Password</label><br>
<input type="password" name="password" autocomplete="new-password"
       minlength="8" maxlength="72" required><br>
<label>Confirm password</label><br>
<input type="password" name="confirm_password" autocomplete="new-password"
       minlength="8" maxlength="72" required><br>
<button type="submit">Create account</button>
</form>
<p><small>Development-only local registration site. Bound to 127.0.0.1.</small></p>
"""
    return _page("Create your account", body)


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("admin_authenticated"):
        return redirect(url_for("admin"))
    if session.get("user_authenticated"):
        return redirect(url_for("account"))

    error = ""
    username = ""
    if request.method == "POST":
        if not _valid_csrf():
            error = "Login form expired. Refresh and try again."
        else:
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            admin_user = str(app.config["ADMIN_USERNAME"] or "")
            admin_pass = str(app.config["ADMIN_PASSWORD"] or "")
            if (
                admin_user
                and admin_pass
                and hmac.compare_digest(username, admin_user)
                and hmac.compare_digest(password, admin_pass)
            ):
                session.clear()
                session["admin_authenticated"] = True
                session["_csrf"] = secrets.token_urlsafe(32)
                return redirect(url_for("admin"))

            account = account_db.verify_account(
                username,
                password,
                db_path=DB_PATH,
                update_last_login=True,
            )
            if account is None:
                error = "Invalid username or password."
            else:
                session.clear()
                session["user_authenticated"] = True
                session["user_uin"] = int(account["uin"])
                session["user_username"] = str(account["username"])
                session["_csrf"] = secrets.token_urlsafe(32)
                _record_web_ip(
                    int(account["uin"]), _client_ip(), login=True
                )
                return redirect(url_for("account"))

    body = f"""
{f'<p class="err">{_esc(error)}</p>' if error else ''}
<form method="post">
{_form_token()}
<label>Username</label><br>
<input name="username" value="{_esc(username)}" autocomplete="username"
       minlength="3" maxlength="24" pattern="[A-Za-z0-9._-]+"
       title="3-24 characters: letters, numbers, dot, underscore or hyphen"
       required><br>
<label>Password</label><br>
<input type="password" name="password" autocomplete="current-password" required><br>
<button type="submit">Sign in</button>
</form>
"""
    if not app.config["ADMIN_PASSWORD"]:
        body += (
            "<p><small>Admin login is disabled until "
            "<code>AF_ADMIN_PASSWORD</code> is set.</small></p>"
        )
    return _page("Login", body)


@app.post("/logout")
def logout():
    if not _valid_csrf():
        return "Invalid CSRF token.", 400
    session.clear()
    return redirect(url_for("login"))


@app.get("/account")
def account():
    if not session.get("user_authenticated"):
        return redirect(url_for("login"))
    uin = int(session["user_uin"])
    nickname = _nickname_for_uin(uin)
    body = f"""
<p>Username: <strong>{_esc(session.get("user_username") or "")}</strong></p>
<p>UIN: <code>{uin}</code></p>
<p>Game nickname: <strong>{_esc(nickname or "(not chosen yet)")}</strong></p>
<form method="post" action="/logout">{_form_token()}
<button type="submit">Log out</button></form>
"""
    return _page("Account", body)


@app.get("/admin")
def admin():
    if not session.get("admin_authenticated"):
        abort(404)
    rows = _account_rows()
    rendered = []
    for row in rows:
        uin = int(row["uin"])
        target = "active" if row["status"] != "active" else "disabled"
        action = "Unban / Enable" if target == "active" else "Ban / Disable"
        wallet = _wallet_for_uin(uin)
        if wallet is None:
            wallet_cell = (
                '<span class="muted">Launch game once to initialize wallet</span>'
            )
        else:
            wallet_cell = (
                f'<form class="ap-form" method="post" '
                f'action="/admin/account/{uin}/wallet">'
                f'{_form_token()}'
                f'<label>AP <input name="ap" type="number" min="0" max="2147483647" '
                f'value="{int(wallet["ap"])}" required></label>'
                f'<label>GP <input name="gp" type="number" min="0" max="2147483647" '
                f'value="{int(wallet["gp"])}" required></label>'
                f'<label>MP <input name="mp" type="number" min="0" max="2147483647" '
                f'value="{int(wallet["mp"])}" required></label>'
                f'<button type="submit">Save wallet</button></form>'
            )
        rendered.append(
            "<tr>"
            f"<td>{uin}</td><td>{_esc(row['username'])}</td>"
            f"<td>{_esc(_nickname_for_uin(uin) or '')}</td>"
            f"<td>{wallet_cell}</td>"
            f"<td>{_esc(row['status'])}</td><td>"
            f'<form method="post" action="/admin/account/{uin}/status">'
            f"{_form_token()}"
            f'<input type="hidden" name="status" value="{target}">'
            f'<button type="submit">{action}</button></form></td></tr>'
        )
    body = (
        "<p>Admin access is available only after signing in through "
        "<code>/login</code>.</p>"
        "<p><small>Set AP, GP, or MP here while the player stays logged in. "
        "Then click the AP reload icon in Mall to request A50E and refresh "
        "the full wallet from SQLite.</small></p>"
        "<table><thead><tr><th>UIN</th><th>Username</th><th>Nickname</th>"
        "<th>Wallet (AP / GP / MP)</th><th>Status</th><th>Action</th></tr></thead><tbody>"
        + "".join(rendered)
        + "</tbody></table>"
        + f'<form method="post" action="/logout">{_form_token()}'
        + '<button type="submit">Log out</button></form>'
    )
    return _page("Admin", body)


@app.post("/admin/account/<int:uin>/wallet")
def admin_wallet(uin: int):
    if not session.get("admin_authenticated"):
        abort(404)
    if not _valid_csrf():
        return "Invalid CSRF token.", 400
    try:
        _set_wallet_for_uin(
            uin,
            ap=int(request.form.get("ap", "")),
            gp=int(request.form.get("gp", "")),
            mp=int(request.form.get("mp", "")),
        )
    except (TypeError, ValueError) as exc:
        return str(exc), 400
    return redirect(url_for("admin"))


@app.post("/admin/account/<int:uin>/status")
def admin_status(uin: int):
    if not session.get("admin_authenticated"):
        abort(404)
    if not _valid_csrf():
        return "Invalid CSRF token.", 400
    try:
        _set_account_status(uin, request.form.get("status", ""))
    except ValueError as exc:
        return str(exc), 400
    return redirect(url_for("admin"))


@app.get("/status")
def status():
    return _page(
        "Development status",
        "<p class='ok'>Account website is running.</p>"
        "<p>Runtime: <code>development</code></p>"
        "<p>Bind: <code>127.0.0.1</code></p>"
        f"<p>Database: <code>{_esc(DB_PATH)}</code></p>",
    )


@app.get("/healthz")
def healthz():
    return jsonify(
        {
            "ok": True,
            "mode": "development",
            "database": DB_PATH.name,
            "database_path": str(DB_PATH),
            "accounts": account_db.account_count(db_path=DB_PATH),
        }
    )


if __name__ == "__main__":
    mode = str(
        os.environ.get("AF_RUNTIME_MODE") or "development"
    ).strip().lower()
    if mode in {"production", "prod", "hosted", "live"}:
        print(
            "[WEB] REFUSED: development account website cannot run in "
            "production mode.",
            flush=True,
        )
        raise SystemExit(3)

    host = "127.0.0.1"
    port = int(os.environ.get("AF_WEB_PORT", "8080"))
    print(f"[WEB] Registration: http://{host}:{port}/register", flush=True)
    print(f"[WEB] Login:        http://{host}:{port}/login", flush=True)
    print(
        f"[WEB] Admin:        http://{host}:{port}/admin "
        "(404 unless signed in as admin)",
        flush=True,
    )
    print(f"[WEB] Account DB:   {DB_PATH}", flush=True)
    if not app.config["ADMIN_PASSWORD"]:
        print(
            "[WEB] WARNING: admin login disabled; set AF_ADMIN_PASSWORD.",
            flush=True,
        )
    app.run(host=host, port=port, debug=False)
