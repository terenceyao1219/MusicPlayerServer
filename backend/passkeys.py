"""WebAuthn ceremonies: signed challenges, verified origins, no biometric storage."""
import json
import secrets
import time
from urllib.parse import urlsplit

from flask import Blueprint, g, jsonify, request
from werkzeug.security import check_password_hash
from webauthn import (
    generate_registration_options, generate_authentication_options,
    verify_registration_response, verify_authentication_response, options_to_json,
)
from webauthn.helpers import base64url_to_bytes
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria, ResidentKeyRequirement,
    UserVerificationRequirement, PublicKeyCredentialDescriptor,
)


def install_passkeys(app, db, lock, fail, body, issue_session):
    bp = Blueprint("passkeys", __name__, url_prefix="/api/v1/mplayer/passkeys")
    settings = app.config["SETTINGS"].get("passkeys", {})
    rp_id = settings.get("rp_id", "")
    origin = settings.get("origin", "")
    parsed = urlsplit(origin)
    configured = bool(settings.get("enabled", False) and rp_id and parsed.hostname == rp_id
                      and not parsed.username and not parsed.password and not parsed.query
                      and not parsed.fragment and parsed.path == ""
                      and (parsed.scheme == "https" or (rp_id == "localhost" and parsed.scheme == "http")))
    with app.app_context():
        db().executescript("""
            CREATE TABLE IF NOT EXISTS passkeys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                credential_id BLOB UNIQUE NOT NULL, public_key BLOB NOT NULL,
                sign_count INTEGER NOT NULL, name TEXT NOT NULL,
                created REAL NOT NULL, last_used REAL);
            CREATE TABLE IF NOT EXISTS passkey_challenges (
                id TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                purpose TEXT NOT NULL, challenge BLOB NOT NULL, expires REAL NOT NULL,
                session_token TEXT, name TEXT);
        """)

    def require_origin():
        if not configured:
            fail("尚未設定通行密鑰的站台網域與 HTTPS 網址", 503)
        if request.headers.get("Origin") != origin:
            fail("此網址與通行密鑰設定不符", 403)

    def payload():
        if (request.content_length or 0) > 65536:
            fail("通行密鑰資料過大", 413)
        return body()

    def issue_challenge(user_id, purpose, challenge, name=None):
        ticket = secrets.token_urlsafe(32)
        db().execute("DELETE FROM passkey_challenges WHERE expires<=?", (time.time(),))
        # Bound pending work per account, including requests from abandoned browser prompts.
        db().execute("DELETE FROM passkey_challenges WHERE user_id=? AND purpose=?", (user_id, purpose))
        db().execute("INSERT INTO passkey_challenges VALUES(?,?,?,?,?,?,?)",
                     (ticket, user_id, purpose, challenge, time.time() + 120,
                      g.token if purpose == "register" else None, name))
        db().commit()
        return ticket

    def consume(data, purpose):
        ticket = data.get("challenge_id")
        if not isinstance(ticket, str) or len(ticket) > 100:
            fail("無效的通行密鑰請求", 400)
        row = db().execute("SELECT * FROM passkey_challenges WHERE id=?", (ticket,)).fetchone()
        if not row or row["purpose"] != purpose or row["expires"] <= time.time():
            fail("通行密鑰請求已過期或已使用，請重試", 400)
        if purpose == "register" and (row["user_id"] != g.user["id"] or row["session_token"] != g.token):
            fail("通行密鑰請求不屬於此登入", 403)
        db().execute("DELETE FROM passkey_challenges WHERE id=?", (ticket,))
        db().commit()  # Consume even when signature verification subsequently fails.
        return row

    @bp.get("/status")
    def status():
        return {"enabled": configured, "origin": origin if configured else None}

    @bp.get("")
    def list_keys():
        return jsonify([dict(row) for row in db().execute(
            "SELECT id,name,created,last_used FROM passkeys WHERE user_id=? ORDER BY id", (g.user["id"],))])

    @bp.delete("/<int:ident>")
    def delete_key(ident):
        with lock:
            result = db().execute("DELETE FROM passkeys WHERE id=? AND user_id=?", (ident, g.user["id"]))
            db().commit()
            if not result.rowcount:
                fail("找不到通行密鑰", 404)
        return {"ok": True}

    @bp.post("/register/options")
    def register_options():
        require_origin()
        data = payload()
        password = data.get("password")
        if not isinstance(password, str) or not check_password_hash(g.user["password"], password):
            fail("請輸入正確的目前密碼以新增通行密鑰", 403)
        label = data.get("name", "我的通行密鑰")
        if not isinstance(label, str) or not label.strip() or len(label) > 80:
            fail("通行密鑰名稱須為 1–80 字元")
        with lock:
            keys = db().execute("SELECT credential_id FROM passkeys WHERE user_id=?", (g.user["id"],)).fetchall()
            if len(keys) >= 20:
                fail("最多可註冊 20 組通行密鑰", 409)
            options = generate_registration_options(
                rp_id=rp_id, rp_name="SONORA", user_id=str(g.user["id"]).encode(),
                user_name=g.user["username"], timeout=120000,
                exclude_credentials=[PublicKeyCredentialDescriptor(id=row[0]) for row in keys],
                authenticator_selection=AuthenticatorSelectionCriteria(
                    resident_key=ResidentKeyRequirement.REQUIRED,
                    user_verification=UserVerificationRequirement.REQUIRED))
            ticket = issue_challenge(g.user["id"], "register", options.challenge, label.strip())
        return {"challenge_id": ticket, "options": json.loads(options_to_json(options))}

    @bp.post("/register/verify")
    def register_verify():
        require_origin()
        data = payload()
        with lock:
            pending = consume(data, "register")
            try:
                verified = verify_registration_response(
                    credential=data.get("credential"), expected_challenge=pending["challenge"],
                    expected_rp_id=rp_id, expected_origin=origin, require_user_verification=True)
            except Exception:
                fail("通行密鑰註冊驗證失敗，請重試", 400)
            db().execute("INSERT INTO passkeys(user_id,credential_id,public_key,sign_count,name,created) VALUES(?,?,?,?,?,?)",
                         (g.user["id"], verified.credential_id, verified.credential_public_key,
                          verified.sign_count, pending["name"], time.time()))
            db().commit()
        return {"ok": True}, 201

    @bp.post("/login/options")
    def login_options():
        require_origin()
        data = payload()
        username = data.get("username")
        if not isinstance(username, str) or not username:
            fail("請先輸入帳號")
        with lock:
            user = db().execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
            keys = db().execute("SELECT credential_id FROM passkeys WHERE user_id=?", (user[0],)).fetchall() if user else []
            if not keys:
                fail("無法使用通行密鑰登入，請使用帳號密碼並確認已註冊通行密鑰", 400)
            options = generate_authentication_options(
                rp_id=rp_id, timeout=120000, user_verification=UserVerificationRequirement.REQUIRED,
                allow_credentials=[PublicKeyCredentialDescriptor(id=row[0]) for row in keys])
            ticket = issue_challenge(user[0], "login", options.challenge)
        return {"challenge_id": ticket, "options": json.loads(options_to_json(options))}

    @bp.post("/login/verify")
    def login_verify():
        require_origin()
        data = payload()
        with lock:
            pending = consume(data, "login")
            credential = data.get("credential")
            try:
                credential_id = base64url_to_bytes(credential["id"])
            except Exception:
                fail("無效的通行密鑰", 400)
            key = db().execute("SELECT * FROM passkeys WHERE credential_id=? AND user_id=?",
                               (credential_id, pending["user_id"])).fetchone()
            if not key:
                fail("通行密鑰驗證失敗", 400)
            try:
                handle = credential.get("response", {}).get("userHandle")
                if handle is not None and base64url_to_bytes(handle) != str(pending["user_id"]).encode():
                    raise ValueError("Wrong account")
                verified = verify_authentication_response(
                    credential=credential, expected_challenge=pending["challenge"],
                    expected_rp_id=rp_id, expected_origin=origin,
                    credential_public_key=key["public_key"], credential_current_sign_count=key["sign_count"],
                    require_user_verification=True)
            except Exception:
                fail("通行密鑰驗證失敗，請重試或使用密碼登入", 400)
            db().execute("UPDATE passkeys SET sign_count=?,last_used=? WHERE id=?",
                         (verified.new_sign_count, time.time(), key["id"]))
            user = db().execute("SELECT * FROM users WHERE id=?", (pending["user_id"],)).fetchone()
            return issue_session(user)

    app.register_blueprint(bp)
