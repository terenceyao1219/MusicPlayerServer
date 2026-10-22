"""Exercise real WebAuthn signatures with a software test authenticator."""
import hashlib
import json
import secrets
import sqlite3

import cbor2
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from webauthn.helpers import bytes_to_base64url

from backend.app import create_app

P = "/api/v1/mplayer"
ORIGIN = "https://music.example.com"
RP = "music.example.com"


@pytest.fixture
def site(tmp_path):
    app = create_app(overrides={
        "storage": {"database": str(tmp_path / "db.sqlite3"), "music": str(tmp_path / "music")},
        "passkeys": {"enabled": True, "rp_id": RP, "origin": ORIGIN}})
    app.config["TESTING"] = True
    client = app.test_client()
    login = client.post(P + "/login", json={"username": "super", "password": "admin"})
    headers = {"Authorization": "Bearer " + login.json["access_token"], "Origin": ORIGIN}
    return app, client, headers, tmp_path


def client_data(kind, challenge, origin=ORIGIN):
    return json.dumps({"type": kind, "challenge": challenge, "origin": origin, "crossOrigin": False}).encode()


def registration(options, private, credential_id, origin=ORIGIN, uv=True):
    public = private.public_key().public_numbers()
    cose = cbor2.dumps({1: 2, 3: -7, -1: 1, -2: public.x.to_bytes(32, "big"), -3: public.y.to_bytes(32, "big")})
    auth = hashlib.sha256(RP.encode()).digest() + bytes([0x45 if uv else 0x41]) + bytes(4)
    auth += bytes(16) + len(credential_id).to_bytes(2, "big") + credential_id + cose
    return {"id": bytes_to_base64url(credential_id), "rawId": bytes_to_base64url(credential_id), "type": "public-key",
            "response": {"clientDataJSON": bytes_to_base64url(client_data("webauthn.create", options["challenge"], origin)),
                         "attestationObject": bytes_to_base64url(cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth}))}}


def enroll(client, headers):
    private = ec.generate_private_key(ec.SECP256R1())
    ident = secrets.token_bytes(32)
    pending = client.post(P + "/passkeys/register/options", headers=headers,
                          json={"password": "admin", "name": "Test Face ID"})
    assert pending.status_code == 200
    options = pending.json["options"]
    assert options["authenticatorSelection"]["userVerification"] == "required"
    result = client.post(P + "/passkeys/register/verify", headers=headers, json={
        "challenge_id": pending.json["challenge_id"], "credential": registration(options, private, ident)})
    assert result.status_code == 201, result.json
    return private, ident


def assertion(options, private, ident, count=1, origin=ORIGIN, uv=True, rp=RP):
    data = client_data("webauthn.get", options["challenge"], origin)
    auth = hashlib.sha256(rp.encode()).digest() + bytes([5 if uv else 1]) + count.to_bytes(4, "big")
    signature = private.sign(auth + hashlib.sha256(data).digest(), ec.ECDSA(hashes.SHA256()))
    return {"id": bytes_to_base64url(ident), "rawId": bytes_to_base64url(ident), "type": "public-key",
            "response": {"clientDataJSON": bytes_to_base64url(data), "authenticatorData": bytes_to_base64url(auth),
                         "signature": bytes_to_base64url(signature), "userHandle": bytes_to_base64url(b"1")}}


def test_real_registration_login_replay_and_delete(site):
    app, client, headers, root = site
    private, ident = enroll(client, headers)
    other = app.test_client()
    origin = {"Origin": ORIGIN}
    pending = other.post(P + "/passkeys/login/options", headers=origin, json={"username": "super"}).json
    payload = {"challenge_id": pending["challenge_id"], "credential": assertion(pending["options"], private, ident)}
    result = other.post(P + "/passkeys/login/verify", headers=origin, json=payload)
    assert result.status_code == 200, result.json
    assert result.json["user"]["username"] == "super"
    assert "HttpOnly" in result.headers["Set-Cookie"] and "Secure" in result.headers["Set-Cookie"]
    auth = {"Authorization": "Bearer " + result.json["access_token"]}
    assert other.get(P + "/me", headers=auth).status_code == 200
    assert other.post(P + "/passkeys/login/verify", headers=origin, json=payload).status_code == 400
    keys = client.get(P + "/passkeys", headers=headers).json
    assert len(keys) == 1 and keys[0]["last_used"] is not None
    assert "public_key" not in keys[0]
    assert client.delete(P + f"/passkeys/{keys[0]['id']}", headers=headers).status_code == 200
    assert other.post(P + "/passkeys/login/options", headers=origin, json={"username": "super"}).status_code == 400
    assert client.post(P + "/login", json={"username": "super", "password": "admin"}).status_code == 200


@pytest.mark.parametrize("variation", ["origin", "challenge", "signature", "uv", "rp", "handle", "expired"])
def test_invalid_assertions_never_issue_session(site, variation):
    app, client, headers, root = site
    private, ident = enroll(client, headers)
    origin = {"Origin": ORIGIN}
    pending = client.post(P + "/passkeys/login/options", headers=origin, json={"username": "super"}).json
    options = dict(pending["options"])
    if variation == "challenge":
        options["challenge"] = bytes_to_base64url(b"wrong")
    kwargs = {"origin": "https://evil.example"} if variation == "origin" else {}
    if variation == "uv": kwargs["uv"] = False
    if variation == "rp": kwargs["rp"] = "evil.example"
    credential = assertion(options, private, ident, **kwargs)
    if variation == "signature": credential["response"]["signature"] = bytes_to_base64url(b"wrong")
    if variation == "handle": credential["response"]["userHandle"] = bytes_to_base64url(b"2")
    if variation == "expired":
        with sqlite3.connect(root / "db.sqlite3") as db: db.execute("UPDATE passkey_challenges SET expires=0")
    response = client.post(P + "/passkeys/login/verify", headers=origin,
                           json={"challenge_id": pending["challenge_id"], "credential": credential})
    assert response.status_code == 400
    assert "Set-Cookie" not in response.headers
    with sqlite3.connect(root / "db.sqlite3") as db:
        assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1


def test_registration_requires_password_origin_and_session_binding(site):
    app, client, headers, root = site
    route = P + "/passkeys/register/options"
    assert client.post(route, json={}).status_code == 401
    assert client.post(route, headers=headers, json={"password": "wrong"}).status_code == 403
    assert client.post(route, headers={**headers, "Origin": "https://evil.example"}, json={"password": "admin"}).status_code == 403
    pending = client.post(route, headers=headers, json={"password": "admin"}).json
    login = client.post(P + "/login", json={"username": "super", "password": "admin"}).json
    other_headers = {**headers, "Authorization": "Bearer " + login["access_token"]}
    assert client.post(P + "/passkeys/register/verify", headers=other_headers,
                       json={"challenge_id": pending["challenge_id"], "credential": {}}).status_code == 403


@pytest.mark.parametrize("uv,origin", [(False, ORIGIN), (True, "https://evil.example")])
def test_invalid_registration_rejected(site, uv, origin):
    _, client, headers, _ = site
    pending = client.post(P + "/passkeys/register/options", headers=headers, json={"password": "admin"}).json
    credential = registration(pending["options"], ec.generate_private_key(ec.SECP256R1()), secrets.token_bytes(32), origin, uv)
    assert client.post(P + "/passkeys/register/verify", headers=headers,
                       json={"challenge_id": pending["challenge_id"], "credential": credential}).status_code == 400
    assert client.get(P + "/passkeys", headers=headers).json == []


def test_other_account_cannot_manage_or_use_keys(site):
    app, client, headers, _ = site
    private, ident = enroll(client, headers)
    key_id = client.get(P + "/passkeys", headers=headers).json[0]["id"]
    client.post(P + "/users", headers=headers, json={"username": "bob", "password": "Password123"})
    login = client.post(P + "/login", json={"username": "bob", "password": "Password123"}).json
    auth = {"Authorization": "Bearer " + login["access_token"]}
    assert client.get(P + "/passkeys", headers=auth).json == []
    assert client.delete(P + f"/passkeys/{key_id}", headers=auth).status_code == 404


def test_missing_configuration_disables_passkeys_without_breaking_password_login(tmp_path):
    app = create_app(overrides={"storage": {"database": str(tmp_path / "db"), "music": str(tmp_path / "music")}})
    client = app.test_client()
    assert client.get(P + "/passkeys/status").json["enabled"] is False
    assert client.post(P + "/passkeys/login/options", json={"username": "super"}).status_code == 503
    assert client.post(P + "/login", json={"username": "super", "password": "admin"}).status_code == 200
