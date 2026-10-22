import hashlib
import io
import sqlite3
import time
import wave

import pytest
from backend.app import create_app

P = "/api/v1/mplayer"


def wav():
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b"\x00\x00" * 8000)
    return output.getvalue()


@pytest.fixture
def setup(tmp_path):
    app = create_app(overrides={"storage": {"database": str(tmp_path / "db.sqlite3"), "music": str(tmp_path / "music")}})
    app.config["TESTING"] = True
    client = app.test_client()
    result = client.post(P + "/login", json={"username": "super", "password": "admin"})
    headers = {"Authorization": "Bearer " + result.json["access_token"]}
    return app, client, headers, tmp_path


def folder(client, headers, name="Jazz"):
    response = client.post(P + "/folders", headers=headers, json={"name": name})
    assert response.status_code == 201
    return response.json["id"]


def upload(client, headers, ident, name="tone.wav"):
    response = client.post(P + f"/folders/{ident}/tracks", headers=headers,
                           data={"files": (io.BytesIO(wav()), name)})
    assert response.status_code == 201
    return response.json[0]["id"]


def user(app, client, headers, username="alice"):
    response = client.post(P + "/users", headers=headers, json={"username": username, "password": "password123", "role": "user"})
    assert response.status_code == 201
    other = app.test_client()
    login = other.post(P + "/login", json={"username": username, "password": "password123"})
    return response.json["id"], other, {"Authorization": "Bearer " + login.json["access_token"]}


def test_auth_expiry_and_logout(setup):
    app, client, headers, root = setup
    assert client.get(P + "/me").status_code == 401  # media cookie does not authorize API
    assert client.get(P + "/me", headers=headers).json["role"] == "admin"
    assert client.post(P + "/login", json={"username": "super", "password": "wrong"}).status_code == 401
    token = headers["Authorization"][7:]
    with sqlite3.connect(root / "db.sqlite3") as db:
        row = db.execute("SELECT token,expires FROM sessions").fetchone()
        assert row[0] == hashlib.sha256(token.encode()).hexdigest()
        assert 86390 < row[1] - time.time() <= 86400
        db.execute("UPDATE sessions SET expires=0")
    assert client.get(P + "/me", headers=headers).status_code == 401
    result = client.post(P + "/login", json={"username": "super", "password": "admin"})
    headers = {"Authorization": "Bearer " + result.json["access_token"]}
    assert client.post(P + "/logout", headers=headers).status_code == 200
    assert client.get(P + "/me", headers=headers).status_code == 401


def test_permission_isolation_and_revocation(setup):
    app, client, headers, root = setup
    f = folder(client, headers)
    t = upload(client, headers, f)
    uid, other, auth = user(app, client, headers)
    assert other.get(P + "/folders", headers=auth).json == []
    assert other.get(P + "/tracks", headers=auth).json == []
    assert other.post(P + "/playback/state", headers=auth, json={"track_id": t}).status_code == 403
    assert other.post(P + "/folders", headers=auth, json={"name": "x"}).status_code == 403
    assert other.get(P + "/users", headers=auth).status_code == 403
    assert client.put(P + f"/folders/{f}/permissions", headers=headers, json={"user_ids": [uid]}).status_code == 200
    assert len(other.get(P + "/tracks", headers=auth).json) == 1
    assert other.post(P + "/playback/state", headers=auth, json={"track_id": t}).status_code == 200
    assert other.get(P + f"/tracks/{t}/stream").data == wav()
    client.put(P + f"/folders/{f}/permissions", headers=headers, json={"user_ids": []})
    assert other.get(P + f"/tracks/{t}/stream").status_code == 403
    assert other.post(P + "/playback/restore", headers=auth, json={"track_id": t}).json["state"]["track_id"] is None


def test_filesystem_rename_move_delete(setup):
    _, client, headers, root = setup
    f = folder(client, headers)
    dest = folder(client, headers, "Ambient")
    first = upload(client, headers, f)
    second = upload(client, headers, f, "second.wav")
    assert client.patch(P + f"/tracks/{first}", headers=headers, json={"name": "renamed.wav"}).status_code == 200
    assert (root / "music/Jazz/renamed.wav").exists()
    assert not (root / "music/Jazz/tone.wav").exists()
    assert client.patch(P + f"/folders/{f}", headers=headers, json={"name": "New Jazz"}).status_code == 200
    assert (root / "music/New Jazz/renamed.wav").exists()
    assert client.post(P + "/tracks/move", headers=headers, json={"track_ids": [first, second], "folder_id": dest}).json["count"] == 2
    assert (root / "music/Ambient/renamed.wav").exists()
    assert not list((root / "music/New Jazz").iterdir())
    assert client.delete(P + f"/folders/{dest}", headers=headers).status_code == 409
    client.post(P + "/playback/state", headers=headers, json={"track_id": first})
    assert client.post(P + "/tracks/delete", headers=headers, json={"track_ids": [first, second]}).status_code == 200
    assert not list((root / "music/Ambient").iterdir())
    assert client.post(P + "/playback/restore", headers=headers, json={"track_id": first}).json["state"]["track_id"] is None
    assert client.delete(P + f"/folders/{dest}", headers=headers).status_code == 200


def test_bulk_conflicts_are_atomic(setup):
    _, client, headers, root = setup
    f = folder(client, headers)
    d = folder(client, headers, "Other")
    a = upload(client, headers, f)
    b = upload(client, headers, f, "second.wav")
    upload(client, headers, d)
    result = client.post(P + "/tracks/move", headers=headers, json={"track_ids": [b, a], "folder_id": d})
    assert result.status_code == 409
    assert (root / "music/Jazz/second.wav").exists()
    assert not (root / "music/Other/second.wav").exists()
    result = client.post(P + f"/folders/{f}/tracks", headers=headers,
                         data={"files": [(io.BytesIO(wav()), "new.wav"), (io.BytesIO(wav()), "tone.wav")]})
    assert result.status_code == 409
    assert not (root / "music/Jazz/new.wav").exists()
    assert client.post(P + "/tracks/delete", headers=headers, json={"track_ids": [a, 999]}).status_code == 404
    assert (root / "music/Jazz/tone.wav").exists()


def test_playback_range_modes_and_independence(setup):
    _, client, headers, root = setup
    f = folder(client, headers)
    d = folder(client, headers, "Zzz")
    a = upload(client, headers, f, "a.wav")
    b = upload(client, headers, f, "b.wav")
    c = upload(client, headers, d)
    # Different bytes make an accidental switch observable.
    (root / "music/Jazz/b.wav").write_bytes(wav() + b"second track")
    response = client.get(P + f"/tracks/{a}/stream", headers={**headers, "Range": "bytes=0-31"})
    assert response.status_code == 206 and response.data == wav()[:32]
    assert response.headers["Content-Range"] == f"bytes 0-31/{len(wav())}"
    def resolve(ident, mode="folder", direction=1, trigger="manual"):
        response = client.post(P + "/playback/resolve", headers=headers,
            json={"track_id": ident, "mode": mode, "direction": direction, "trigger": trigger})
        assert response.status_code == 200
        assert response.json["stream_url"] == P + f"/tracks/{response.json['track_id']}/stream"
        return response.json["track_id"]
    assert resolve(a, "single", trigger="ended") == a
    assert resolve(a, "single") == b
    assert resolve(b) == a
    assert resolve(a, direction=-1) == b
    assert resolve(b, "global") == c
    assert resolve(c, "global") == a
    assert resolve(a, "global", -1) == c
    assert resolve(None) == a
    assert client.get(P + f"/tracks/{a}/stream", headers={**headers, "Range": "bytes=999999-"}).status_code == 416
    # Another login for the same account resolves/restores B without changing A's stream.
    second = create_app(overrides={"storage": {"database": str(root / "db.sqlite3"), "music": str(root / "music")}}).test_client()
    login = second.post(P + "/login", json={"username": "super", "password": "admin"})
    auth = {"Authorization": "Bearer " + login.json["access_token"]}
    saved = {"schema_version": 1, "track_id": b, "position": .5, "mode": "global"}
    assert second.post(P + "/playback/state", headers=auth, json=saved).json["state"] == saved
    assert second.post(P + "/playback/restore", headers=auth, json=saved).json["state"] == saved
    assert second.get(P + f"/tracks/{b}/stream").data == wav() + b"second track"
    assert client.get(P + f"/tracks/{a}/stream", headers={"Range": "bytes=32-63"}).data == wav()[32:64]
    with sqlite3.connect(root / "db.sqlite3") as db:
        assert db.execute("SELECT 1 FROM sqlite_master WHERE name='playback'").fetchone() is None


def test_playback_restore_validation_and_removed_routes(setup):
    _, client, headers, root = setup
    f = folder(client, headers)
    t = upload(client, headers, f)
    for route in ("state", "restore"):
        for payload in ([], None, {"schema_version": 2}, {"schema_version": True},
                        {"position": float("inf")}, {"position": True}, {"track_id": 0}):
            assert client.post(P + "/playback/" + route, headers=headers, json=payload).status_code == 400
    for payload in ({"direction": True}, {"direction": 0}, {"trigger": "bad"}):
        assert client.post(P + "/playback/resolve", headers=headers, json=payload).status_code == 400
    (root / "music/Jazz/tone.wav").unlink()
    result = client.post(P + "/playback/restore", headers=headers, json={"track_id": t, "position": 50, "mode": "single"}).json
    assert result == {"state": {"schema_version": 1, "track_id": None, "position": 0, "mode": "single"}, "stream_url": None, "reason": "unavailable"}
    assert client.post(P + "/playback/state", headers=headers, json={"track_id": t}).status_code == 404
    # The media cookie is accepted only by the explicit stream route.
    assert client.get(P + "/tracks").status_code == 401
    assert client.post(P + "/playback/restore", json={}).status_code == 401
    for method, route in (("GET", "/playback"), ("PUT", "/playback"), ("POST", "/playback/next"), ("GET", "/playback/stream")):
        assert client.open(P + route, method=method, headers=headers).status_code == 404


@pytest.mark.parametrize("value", ["../escape", "a/b", "a\\b", ".", "..", "bad:", " trailing ", ""])
def test_path_validation(setup, value):
    _, client, headers, _ = setup
    assert client.post(P + "/folders", headers=headers, json={"name": value}).status_code == 400


def test_users_and_password_revocation(setup):
    app, client, headers, _ = setup
    assert client.patch(P + "/users/1", headers=headers, json={"role": "user"}).status_code == 409
    assert client.delete(P + "/users/1", headers=headers).status_code == 409
    uid, other, auth = user(app, client, headers)
    assert client.patch(P + f"/users/{uid}", headers=headers, json={"password": "changed123"}).status_code == 200
    assert other.get(P + "/me", headers=auth).status_code == 401
    assert other.post(P + "/login", json={"username": "alice", "password": "changed123"}).status_code == 200
    assert client.delete(P + f"/users/{uid}", headers=headers).status_code == 200


def test_invalid_upload_and_state(setup):
    _, client, headers, root = setup
    f = folder(client, headers)
    for filename in ("a.exe", "../a.wav"):
        assert client.post(P + f"/folders/{f}/tracks", headers=headers, data={"files": (io.BytesIO(b"x"), filename)}).status_code == 400
    assert client.post(P + f"/folders/{f}/tracks", headers=headers, data={"files": (io.BytesIO(b""), "empty.wav")}).status_code == 400
    assert not list((root / "music/Jazz").iterdir())
    for data in ({"position": -1}, {"position": "nan"}, {"mode": "bad"}, {"track_id": True}):
        assert client.post(P + "/playback/state", headers=headers, json=data).status_code == 400
    assert client.get('/').status_code == 200
    assert client.get('/health').json == {"status": "ok"}
    assert client.get('/data/music.sqlite3').status_code == 404


def test_upload_failure_rolls_back_batch(setup):
    _, client, headers, root = setup
    f = folder(client, headers)
    response = client.post(P + f"/folders/{f}/tracks", headers=headers,
        data={"files": [(io.BytesIO(wav()), "good.wav"), (io.BytesIO(b""), "empty.wav")]})
    assert response.status_code == 400
    assert not list((root / "music/Jazz").iterdir())
    assert client.get(P + "/tracks", headers=headers).json == []


def test_move_failure_restores_files(setup, monkeypatch):
    from pathlib import Path
    _, client, headers, root = setup
    source = folder(client, headers)
    dest = folder(client, headers, "Other")
    a = upload(client, headers, source, "a.wav")
    b = upload(client, headers, source, "b.wav")
    original = Path.rename
    def rename(path, target):
        if path.name == "b.wav":
            raise OSError("simulated disk failure")
        return original(path, target)
    monkeypatch.setattr(Path, "rename", rename)
    response = client.post(P + "/tracks/move", headers=headers, json={"track_ids": [a,b], "folder_id": dest})
    assert response.status_code == 500
    assert (root / "music/Jazz/a.wav").exists()
    assert (root / "music/Jazz/b.wav").exists()
    assert not list((root / "music/Other").iterdir())
    assert all(t["folder_id"] == source for t in client.get(P + "/tracks", headers=headers).json)


def test_move_revokes_old_folder_access(setup):
    app, client, headers, _ = setup
    source = folder(client, headers)
    dest = folder(client, headers, "Private")
    t = upload(client, headers, source)
    uid, other, auth = user(app, client, headers)
    client.put(P + f"/folders/{source}/permissions", headers=headers, json={"user_ids":[uid]})
    other.post(P + "/playback/state", headers=auth, json={"track_id":t})
    client.post(P + "/tracks/move", headers=headers, json={"track_ids":[t], "folder_id":dest})
    assert other.get(P + "/tracks", headers=auth).json == []
    assert other.get(P + f"/tracks/{t}/stream").status_code == 403


def test_user_permissions_replace_multiple_folders(setup):
    app, client, headers, _ = setup
    a = folder(client, headers, "A")
    b = folder(client, headers, "B")
    c = folder(client, headers, "C")
    uid, listener, auth = user(app, client, headers)
    other_id, _, _ = user(app, client, headers, "bob")
    endpoint = P + f"/users/{uid}/permissions"
    assert client.get(endpoint, headers=headers).json == {"folder_ids": []}
    client.put(P + f"/folders/{a}/permissions", headers=headers, json={"user_ids": [uid, other_id]})
    assert client.get(endpoint, headers=headers).json == {"folder_ids": [a]}
    response = client.put(endpoint, headers=headers, json={"folder_ids": [c, b, b]})
    assert response.status_code == 200
    assert response.json == {"folder_ids": [b, c]}
    assert [f["id"] for f in listener.get(P + "/folders", headers=auth).json] == [b, c]
    assert client.get(P + f"/users/{other_id}/permissions", headers=headers).json == {"folder_ids": [a]}
    assert client.put(endpoint, headers=headers, json={"folder_ids": []}).json == {"folder_ids": []}
    assert listener.get(P + "/folders", headers=auth).json == []


def test_user_permissions_validation_preserves_grants(setup):
    app, client, headers, _ = setup
    f = folder(client, headers)
    uid, listener, auth = user(app, client, headers)
    endpoint = P + f"/users/{uid}/permissions"
    client.put(endpoint, headers=headers, json={"folder_ids": [f]})
    for body, status in [({"folder_ids": [99999]}, 404), ({}, 400),
                         ({"folder_ids": [True]}, 400), ({"folder_ids": "1"}, 400),
                         ({"folder_ids": [f] * 1001}, 400)]:
        assert client.put(endpoint, headers=headers, json=body).status_code == status
        assert client.get(endpoint, headers=headers).json == {"folder_ids": [f]}
    for method in ("GET", "PUT"):
        assert listener.open(endpoint, method=method, headers=auth, json={"folder_ids": []}).status_code == 403
        assert client.open(P + "/users/1/permissions", method=method, headers=headers, json={"folder_ids": []}).status_code == 400
        assert client.open(P + "/users/99999/permissions", method=method, headers=headers, json={"folder_ids": []}).status_code == 404


def test_user_permissions_revoke_stream(setup):
    app, client, headers, _ = setup
    f = folder(client, headers)
    t = upload(client, headers, f)
    uid, listener, auth = user(app, client, headers)
    endpoint = P + f"/users/{uid}/permissions"
    client.put(endpoint, headers=headers, json={"folder_ids": [f]})
    listener.post(P + "/playback/state", headers=auth, json={"track_id": t})
    assert listener.get(P + f"/tracks/{t}/stream").status_code == 200
    client.put(endpoint, headers=headers, json={"folder_ids": []})
    assert listener.get(P + f"/tracks/{t}/stream").status_code == 403


def test_sync_import_idempotency_and_default_permissions(setup):
    app, client, headers, root = setup
    existing = folder(client, headers, "Existing")
    tid = upload(client, headers, existing)
    uid, listener, auth = user(app, client, headers)
    client.put(P + f"/users/{uid}/permissions", headers=headers, json={"folder_ids": [existing]})
    client.post(P + "/playback/state", headers=headers, json={"track_id": tid, "position": 12})
    (root / "music/New").mkdir()
    (root / "music/Empty").mkdir()
    for suffix in ("MP3", "m4a", "wav", "ogg"):
        (root / f"music/New/song.{suffix}").write_bytes(wav())
    (root / "music/Existing/external.wav").write_bytes(wav())
    response = client.post(P + "/library/sync", headers=headers)
    assert response.status_code == 200
    assert response.json["folders_added"] == 2
    assert response.json["tracks_added"] == 5
    assert response.json["tracks_removed"] == 0
    assert client.post(P + "/playback/restore", headers=headers, json={"track_id": tid, "position": 12}).json["state"]["position"] == 12
    assert client.get(P + f"/users/{uid}/permissions", headers=headers).json == {"folder_ids": [existing]}
    assert [r["name"] for r in listener.get(P + "/folders", headers=auth).json] == ["Existing"]
    result = client.post(P + "/library/sync", headers=headers).json
    assert all(result[key] == 0 for key in ("folders_added", "folders_removed", "tracks_added", "tracks_removed"))
    assert (root / "music/New/song.MP3").read_bytes() == wav()


def test_sync_removed_tracks_folders_and_external_rename(setup):
    app, client, headers, root = setup
    fid = folder(client, headers)
    tid = upload(client, headers, fid)
    uid, _, _ = user(app, client, headers)
    client.put(P + f"/users/{uid}/permissions", headers=headers, json={"folder_ids": [fid]})
    client.post(P + "/playback/state", headers=headers, json={"track_id": tid, "position": 2})
    (root / "music/Jazz").rename(root / "music/Renamed")
    result = client.post(P + "/library/sync", headers=headers).json
    assert result["folders_removed"] == result["folders_added"] == 1
    assert result["tracks_removed"] == result["tracks_added"] == 1
    state = client.post(P + "/playback/restore", headers=headers, json={"track_id": tid, "position": 2}).json["state"]
    assert state["track_id"] is None and state["position"] == 0
    assert client.get(P + f"/users/{uid}/permissions", headers=headers).json == {"folder_ids": []}
    new_track = client.get(P + "/tracks", headers=headers).json[0]
    assert new_track["id"] != tid
    (root / "music/Renamed/tone.wav").unlink()
    result = client.post(P + "/library/sync", headers=headers).json
    assert result["tracks_removed"] == 1 and result["folders_removed"] == 0
    assert client.get(P + "/tracks", headers=headers).json == []


def test_sync_skips_nested_unsupported_and_symlinks(setup):
    _, client, headers, root = setup
    library = root / "music"
    (library / "Top/nested").mkdir(parents=True)
    (library / "Top/nested/hidden.wav").write_bytes(wav())
    (library / "Top/notes.txt").write_text("keep me")
    (library / "Top/empty.wav").touch()
    (library / "Top/bad:name.wav").write_bytes(wav())
    (library / "root.wav").write_bytes(wav())
    (root / "outside").mkdir()
    (root / "outside/secret.wav").write_bytes(wav())
    (library / "linked").symlink_to(root / "outside", target_is_directory=True)
    (library / "Top/link.wav").symlink_to(root / "outside/secret.wav")
    result = client.post(P + "/library/sync", headers=headers).json
    assert result["folders_added"] == 1 and result["tracks_added"] == 0
    assert result["skipped_count"] == 7
    assert len(result["skipped"]) == 7
    assert (library / "Top/notes.txt").read_text() == "keep me"
    assert (root / "outside/secret.wav").read_bytes() == wav()


def test_sync_read_failure_preserves_db(setup, monkeypatch):
    import os
    _, client, headers, root = setup
    fid = folder(client, headers)
    upload(client, headers, fid)
    (root / "music/New").mkdir()
    (root / "music/New/new.wav").write_bytes(wav())
    original = os.scandir
    def denied(path):
        if str(path).endswith("/New"):
            raise PermissionError("simulated unreadable directory")
        return original(path)
    monkeypatch.setattr(os, "scandir", denied)
    assert client.post(P + "/library/sync", headers=headers).status_code == 500
    assert len(client.get(P + "/folders", headers=headers).json) == 1
    assert len(client.get(P + "/tracks", headers=headers).json) == 1
    monkeypatch.setattr(os, "scandir", original)
    (root / "music").rename(root / "unmounted")
    assert client.post(P + "/library/sync", headers=headers).status_code == 500
    assert len(client.get(P + "/tracks", headers=headers).json) == 1


def test_sync_admin_only(setup):
    app, client, headers, _ = setup
    _, listener, auth = user(app, client, headers)
    assert listener.post(P + "/library/sync", headers=auth).status_code == 403
    assert client.post(P + "/library/sync").status_code == 401


def test_sync_transaction_failure_rolls_back(setup):
    _, client, headers, root = setup
    (root / "music/New").mkdir()
    (root / "music/New/a.wav").write_bytes(wav())
    (root / "music/New/b.wav").write_bytes(wav())
    with sqlite3.connect(root / "db.sqlite3") as connection:
        connection.executescript("CREATE TRIGGER fail_insert BEFORE INSERT ON tracks WHEN NEW.name='b.wav' BEGIN SELECT RAISE(ABORT, 'test failure'); END;")
    assert client.post(P + "/library/sync", headers=headers).status_code == 409
    assert client.get(P + "/folders", headers=headers).json == []
    assert client.get(P + "/tracks", headers=headers).json == []
    assert (root / "music/New/a.wav").exists()


def test_public_version_matches_source(setup):
    from backend.app import BASE
    app, client, headers, root = setup
    response = app.test_client().get(P + "/version")
    assert response.status_code == 200
    assert response.json == {"version": (BASE / "VERSION").read_text().strip()}
    assert response.headers["Cache-Control"] == "no-store"


def test_deleted_ids_never_reused_and_existing_db_keeps_data(setup):
    _, client, headers, root = setup
    f = folder(client, headers)
    old = upload(client, headers, f)
    client.post(P + "/tracks/delete", headers=headers, json={"track_ids": [old]})
    fresh = upload(client, headers, f)
    assert fresh > old
    assert client.post(P + "/playback/restore", headers=headers, json={"track_id": old}).json["state"]["track_id"] is None
    # Simulate an old installation without the additive counter table.
    with sqlite3.connect(root / "db.sqlite3") as db:
        db.execute("DROP TABLE id_counters")
        db.execute("CREATE TABLE playback(user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE, track_id INTEGER REFERENCES tracks(id) ON DELETE SET NULL, position REAL, mode TEXT DEFAULT 'folder')")
        db.execute("INSERT INTO playback(user_id,track_id,position) VALUES(1,?,42)", (fresh,))
    app = create_app(overrides={"storage": {"database": str(root / "db.sqlite3"), "music": str(root / "music")}})
    second = app.test_client()
    assert second.get(P + f"/tracks/{fresh}/stream", headers=headers).data == wav()
    assert upload(second, headers, f, "new.wav") > fresh
    with sqlite3.connect(root / "db.sqlite3") as db:
        assert db.execute("SELECT position FROM playback WHERE user_id=1").fetchone()[0] == 42
        assert not db.execute("PRAGMA foreign_key_check").fetchall()


def test_random_mode_excludes_current_and_unauthorized_tracks(setup):
    app, client, headers, root = setup
    first = folder(client, headers, "First")
    second = folder(client, headers, "Second")
    a = upload(client, headers, first)
    b = upload(client, headers, second)
    uid, listener, auth = user(app, client, headers)
    client.put(P + f"/users/{uid}/permissions", headers=headers, json={"folder_ids": [first]})
    for direction in (-1, 1):
        result = client.post(P + "/playback/resolve", headers=headers, json={"track_id": a, "mode": "random", "direction": direction})
        assert result.json["track_id"] == b
        assert listener.post(P + "/playback/resolve", headers=auth, json={"track_id": a, "mode": "random", "direction": direction}).json["track_id"] == a
    saved = {"schema_version": 1, "track_id": a, "position": 2, "mode": "random"}
    assert listener.post(P + "/playback/restore", headers=auth, json=saved).json["state"] == saved
