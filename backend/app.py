from __future__ import annotations

import hashlib
import os
import secrets
import shutil
import sqlite3
import stat
import threading
import time
from pathlib import Path

import yaml
from flask import Flask, g, jsonify, request, send_file, send_from_directory
from werkzeug.exceptions import HTTPException
from werkzeug.security import check_password_hash, generate_password_hash

BASE = Path(__file__).resolve().parent.parent
PREFIX = "/api/v1/mplayer"
EXTENSIONS = {".mp3", ".m4a", ".wav", ".ogg"}


def create_app(config_path=None, overrides=None):
    with open(config_path or os.environ.get("MPLAYER_CONFIG", BASE / "config.yaml"), encoding="utf-8") as f:
        config = yaml.safe_load(f)
    if overrides:
        for key, value in overrides.items():
            config.setdefault(key, {}).update(value)
    app = Flask(__name__, static_folder=None)
    app.config["VERSION"] = (BASE / "VERSION").read_text(encoding="utf-8").strip()
    app.config["SETTINGS"] = config
    app.config["MAX_CONTENT_LENGTH"] = int(config["upload"]["max_mb"]) * 1024 * 1024
    database = (BASE / config["storage"]["database"]).resolve()
    music = (BASE / config["storage"]["music"]).resolve()
    database.parent.mkdir(parents=True, exist_ok=True)
    music.mkdir(parents=True, exist_ok=True)
    lock = threading.RLock()
    revision = secrets.token_hex(16)

    def library_changed():
        nonlocal revision
        revision = secrets.token_hex(16)

    app.extensions['library_changed'] = library_changed

    def db():
        if "db" not in g:
            g.db = sqlite3.connect(database, timeout=30)
            g.db.row_factory = sqlite3.Row
            g.db.execute("PRAGMA foreign_keys=ON")
        return g.db

    def next_id(table):
        # An additive high-water mark preserves IDs without rebuilding existing tables.
        return db().execute("UPDATE id_counters SET value=value+1 WHERE name=? RETURNING value", (table,)).fetchone()[0]

    @app.teardown_appcontext
    def close_db(error):
        connection = g.pop("db", None)
        if connection:
            connection.close()

    with app.app_context():
        db().executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS id_counters (name TEXT PRIMARY KEY, value INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('admin','user')));
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS folders (id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL);
            CREATE TABLE IF NOT EXISTS grants (
                folder_id INTEGER REFERENCES folders(id) ON DELETE CASCADE,
                user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                PRIMARY KEY(folder_id,user_id));
            CREATE TABLE IF NOT EXISTS tracks (
                id INTEGER PRIMARY KEY, folder_id INTEGER NOT NULL REFERENCES folders(id) ON DELETE CASCADE,
                name TEXT NOT NULL, UNIQUE(folder_id,name));
        """)
        for table in ("users", "tracks"):
            db().execute(f"INSERT INTO id_counters(name,value) VALUES(?,(SELECT COALESCE(MAX(id),0) FROM {table})) "
                         "ON CONFLICT(name) DO UPDATE SET value=MAX(value,excluded.value)", (table,))
        if not db().execute("SELECT 1 FROM users LIMIT 1").fetchone():
            db().execute("INSERT INTO users(id,username,password,role) VALUES(?,?,?,?)",
                         (next_id("users"), "super", generate_password_hash("admin"), "admin"))
        db().commit()

    def fail(message, status=400):
        from werkzeug.exceptions import abort
        abort(status, description=message)

    def body():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            fail("請提供 JSON object")
        return data

    def name(value):
        if not isinstance(value, str) or not value.strip() or value != value.strip() or len(value) > 160:
            fail("名稱必須為 1–160 字元，且前後不能有空白")
        if any(c in value for c in '/\\<>:"|?*') or value in {".", ".."} or any(ord(c) < 32 for c in value) or value.endswith('.'):
            fail("名稱不能包含路徑或特殊字元")
        return value

    def integer(value):
        if type(value) is not int or value <= 0:
            fail("ID 必須為正整數")
        return value

    def ids(value):
        if not isinstance(value, list) or not value or len(value) > 1000:
            fail("請選擇 1–1000 筆資料")
        return list(dict.fromkeys(integer(x) for x in value))

    def one(table, ident):
        row = db().execute(f"SELECT * FROM {table} WHERE id=?", (ident,)).fetchone()
        if not row:
            fail("找不到資料", 404)
        return row

    def admin():
        if g.user["role"] != "admin":
            fail("需要 admin 權限", 403)

    def allowed(folder_id):
        if g.user["role"] != "admin" and not db().execute(
                "SELECT 1 FROM grants WHERE folder_id=? AND user_id=?", (folder_id, g.user["id"])).fetchone():
            fail("沒有此資料夾的權限", 403)

    def track(ident):
        row = one("tracks", ident)
        allowed(row["folder_id"])
        return row

    def path(folder_id, filename=None):
        folder = one("folders", folder_id)
        target = music / folder["name"]
        if filename:
            target = target / filename
        if target.is_symlink() or not target.resolve().is_relative_to(music):
            fail("不允許存取符號連結或外部路徑", 400)
        return target

    def token_hash(token):
        return hashlib.sha256(token.encode()).hexdigest()

    @app.before_request
    def authenticate():
        if not request.path.startswith(PREFIX) or request.path in {PREFIX + "/login", PREFIX + "/version"} or request.endpoint in {"passkeys.status", "passkeys.login_options", "passkeys.login_verify"}:
            return
        token = request.headers.get("Authorization", "")
        token = token[7:] if token.startswith("Bearer ") else ""
        if request.endpoint in {"stream", "hls.playlist", "hls.segment"} and request.method in {"GET", "HEAD"} and not token:
            token = request.cookies.get("mplayer_media", "")
        g.token = token_hash(token)
        g.user = db().execute("SELECT u.* FROM users u JOIN sessions s ON s.user_id=u.id WHERE s.token=? AND s.expires>?",
                              (g.token, time.time())).fetchone()
        if not g.user:
            fail("請重新登入，認證已失效或過期", 401)

    @app.after_request
    def headers(response):
        if request.method in {'POST', 'PUT', 'PATCH', 'DELETE'} and 200 <= response.status_code < 300 and request.endpoint in {
                'create_folder', 'change_folder', 'upload', 'rename_track', 'move_tracks',
                'delete_tracks', 'permissions', 'user_permissions', 'change_user'}:
            library_changed()
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self'; script-src 'self'; media-src 'self' blob:; worker-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'"
        if request.path.startswith(PREFIX):
            response.headers["Cache-Control"] = "no-store"
        if request.endpoint in {"hls.playlist", "hls.segment"} and response.status_code >= 400:
            app.logger.warning('HLS media request failed: endpoint=%s status=%s', request.endpoint, response.status_code)
        return response

    @app.errorhandler(HTTPException)
    def http_error(error):
        return jsonify(error=error.description), error.code

    @app.errorhandler(sqlite3.IntegrityError)
    def conflict(error):
        return jsonify(error="名稱重複或資料關聯不正確"), 409

    @app.errorhandler(OSError)
    def file_error(error):
        app.logger.exception("Filesystem operation failed")
        return jsonify(error="檔案操作失敗，請確認儲存空間與存取權限"), 500

    @app.get(PREFIX + "/version")
    def version():
        return {"version": app.config["VERSION"]}

    @app.post(PREFIX + "/login")
    def login():
        data = body()
        username, password = data.get("username"), data.get("password")
        if not isinstance(username, str) or not isinstance(password, str):
            fail("請輸入帳號與密碼")
        user = db().execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if not user or not check_password_hash(user["password"], password):
            fail("帳號或密碼錯誤", 401)
        return issue_session(user)

    def issue_session(user):
        token = secrets.token_urlsafe(48)
        expires = time.time() + 86400
        db().execute("DELETE FROM sessions WHERE expires<=?", (time.time(),))
        db().execute("INSERT INTO sessions VALUES(?,?,?)", (token_hash(token), user["id"], expires))
        db().commit()
        response = jsonify(access_token=token, token_type="Bearer", expires_in=86400,
                           user={k: user[k] for k in ("id", "username", "role")})
        response.set_cookie("mplayer_media", token, max_age=86400, httponly=True,
                            secure=config["security"].get("cookie_secure", False) or config["server"].get("tls", False) or str(config.get("passkeys", {}).get("origin", "")).startswith("https://"),
                            samesite="Strict", path=PREFIX + "/tracks/")
        return response

    from .passkeys import install_passkeys
    install_passkeys(app, db, lock, fail, body, issue_session)

    @app.post(PREFIX + "/logout")
    def logout():
        db().execute("DELETE FROM sessions WHERE token=?", (g.token,))
        db().commit()
        response = jsonify(ok=True)
        response.delete_cookie("mplayer_media", path=PREFIX + "/tracks/")
        return response

    @app.get(PREFIX + "/me")
    def me():
        return {k: g.user[k] for k in ("id", "username", "role")}

    @app.get(PREFIX + "/users")
    def users():
        admin()
        return jsonify([dict(r) for r in db().execute("SELECT id,username,role FROM users ORDER BY id")])

    def user_values(data, existing=None):
        username = name(data.get("username", existing["username"] if existing else None))
        role = data.get("role", existing["role"] if existing else "user")
        if role not in ("admin", "user"):
            fail("角色必須為 admin 或 user")
        password = data.get("password")
        if password is not None and (not isinstance(password, str) or len(password) < 8 or len(password) > 128):
            fail("密碼長度須為 8–128 字元")
        if not existing and password is None:
            fail("請提供密碼")
        return username, generate_password_hash(password) if password is not None else existing["password"], role

    @app.post(PREFIX + "/users")
    def create_user():
        admin()
        values = user_values(body())
        with lock:
            cursor = db().execute("INSERT INTO users(id,username,password,role) VALUES(?,?,?,?)", (next_id("users"), *values))
            db().commit()
        return jsonify(id=cursor.lastrowid), 201

    @app.route(PREFIX + "/users/<int:ident>", methods=["PATCH", "DELETE"])
    def change_user(ident):
        admin()
        with lock:
            existing = one("users", ident)
            values = user_values(body(), existing) if request.method == "PATCH" else None
            removing_admin = existing["role"] == "admin" and (values is None or values[2] != "admin")
            if removing_admin and db().execute("SELECT COUNT(*) FROM users WHERE role='admin'").fetchone()[0] <= 1:
                fail("不能移除最後一位 admin", 409)
            if request.method == "DELETE":
                if ident == g.user["id"]:
                    fail("不能刪除目前登入的帳號", 409)
                db().execute("DELETE FROM users WHERE id=?", (ident,))
            else:
                db().execute("UPDATE users SET username=?,password=?,role=? WHERE id=?", (*values, ident))
                db().execute("DELETE FROM sessions WHERE user_id=?", (ident,))
                db().execute("DELETE FROM passkey_challenges WHERE user_id=?", (ident,))
            db().commit()
        return {"ok": True}

    @app.get(PREFIX + "/folders")
    def folders():
        rows = db().execute("""SELECT f.id,f.name,COUNT(t.id) AS track_count FROM folders f
            LEFT JOIN tracks t ON t.folder_id=f.id WHERE ?='admin' OR EXISTS
            (SELECT 1 FROM grants g WHERE g.folder_id=f.id AND g.user_id=?) GROUP BY f.id ORDER BY f.name""",
                            (g.user["role"], g.user["id"]))
        return jsonify([dict(r) for r in rows])

    @app.post(PREFIX + "/folders")
    def create_folder():
        admin()
        value = name(body().get("name"))
        with lock:
            target = music / value
            if target.exists():
                fail("資料夾已存在", 409)
            cursor = db().execute("INSERT INTO folders(name) VALUES(?)", (value,))
            try:
                target.mkdir()
                db().commit()
            except Exception:
                db().rollback()
                if target.exists():
                    target.rmdir()
                raise
        return jsonify(id=cursor.lastrowid), 201

    @app.route(PREFIX + "/folders/<int:ident>", methods=["PATCH", "DELETE"])
    def change_folder(ident):
        admin()
        with lock:
            old = path(ident)
            if request.method == "DELETE":
                if db().execute("SELECT 1 FROM tracks WHERE folder_id=?", (ident,)).fetchone():
                    fail("請先刪除或搬移資料夾內音樂", 409)
                old.rmdir()
                try:
                    db().execute("DELETE FROM folders WHERE id=?", (ident,))
                    db().commit()
                except Exception:
                    db().rollback()
                    old.mkdir()
                    raise
            else:
                value = name(body().get("name"))
                new = music / value
                if new == old:
                    return {"ok": True}
                if new.exists():
                    fail("資料夾已存在", 409)
                old.rename(new)
                try:
                    db().execute("UPDATE folders SET name=? WHERE id=?", (value, ident))
                    db().commit()
                except Exception:
                    db().rollback()
                    new.rename(old)
                    raise
        return {"ok": True}

    @app.route(PREFIX + "/users/<int:ident>/permissions", methods=["GET", "PUT"])
    def user_permissions(ident):
        admin()
        with lock:
            if one("users", ident)["role"] != "user":
                fail("只能設定 user 帳號的資料夾權限")
            if request.method == "PUT":
                value = body().get("folder_ids")
                values = [] if value == [] else ids(value)
                # Validate the entire selection before replacing any existing grants.
                for folder_id in values:
                    one("folders", folder_id)
                db().execute("DELETE FROM grants WHERE user_id=?", (ident,))
                db().executemany("INSERT INTO grants(folder_id,user_id) VALUES(?,?)",
                                 [(folder_id, ident) for folder_id in values])
                db().commit()
            return jsonify(folder_ids=[r[0] for r in db().execute(
                "SELECT folder_id FROM grants WHERE user_id=? ORDER BY folder_id", (ident,))])

    @app.route(PREFIX + "/folders/<int:ident>/permissions", methods=["GET", "PUT"])
    def permissions(ident):
        admin()
        one("folders", ident)
        if request.method == "PUT":
            value = body().get("user_ids")
            values = [] if value == [] else ids(value)
            with lock:
                for user_id in values:
                    if one("users", user_id)["role"] != "user":
                        fail("只能授權 user 帳號")
                db().execute("DELETE FROM grants WHERE folder_id=?", (ident,))
                db().executemany("INSERT INTO grants VALUES(?,?)", [(ident, u) for u in values])
                db().commit()
        return jsonify(user_ids=[r[0] for r in db().execute("SELECT user_id FROM grants WHERE folder_id=?", (ident,))])

    @app.post(PREFIX + "/library/sync")
    def sync_library():
        admin()
        return reconcile_library()

    def reconcile_library():
        summary = {"folders_added": 0, "folders_removed": 0,
                   "tracks_added": 0, "tracks_removed": 0, "skipped_count": 0, "skipped": []}

        def skip(relative, reason):
            summary["skipped_count"] += 1
            if len(summary["skipped"]) < 100:
                summary["skipped"].append({"path": relative, "reason": reason})

        def valid_name(relative, value):
            try:
                name(value)
                return True
            except HTTPException:
                skip(relative, "名稱不符合檔案管理規則")
                return False

        with lock:
            # Gather a complete snapshot before touching the DB. Missing/unreadable
            # storage must fail rather than look like an empty music library.
            snapshot = {}
            with os.scandir(music) as entries:
                for entry in entries:
                    info = entry.stat(follow_symlinks=False)
                    if not stat.S_ISDIR(info.st_mode):
                        skip(entry.name, "根目錄僅接受實體資料夾")
                        continue
                    if not valid_name(entry.name, entry.name):
                        continue
                    filenames = set()
                    with os.scandir(entry.path) as children:
                        for child in children:
                            relative = entry.name + "/" + child.name
                            child_info = child.stat(follow_symlinks=False)
                            if not stat.S_ISREG(child_info.st_mode):
                                skip(relative, "略過子資料夾、符號連結及非一般檔案")
                            elif not valid_name(relative, child.name):
                                continue
                            elif Path(child.name).suffix.lower() not in EXTENSIONS:
                                skip(relative, "不支援的音樂格式")
                            elif child_info.st_size == 0:
                                skip(relative, "空檔案")
                            else:
                                filenames.add(child.name)
                    snapshot[entry.name] = filenames
            connection = db()
            with connection:
                existing_folders = {r["name"]: r["id"] for r in connection.execute("SELECT id,name FROM folders")}
                removed_tracks = []
                # Insert before deleting so new paths cannot reuse just-removed IDs.
                for folder_name, filenames in sorted(snapshot.items()):
                    folder_id = existing_folders.get(folder_name)
                    if folder_id is None:
                        folder_id = connection.execute("INSERT INTO folders(name) VALUES(?)", (folder_name,)).lastrowid
                        summary["folders_added"] += 1
                        # Deliberately do not insert any grants for discovered folders.
                    existing_tracks = {r["name"]: r["id"] for r in connection.execute(
                        "SELECT id,name FROM tracks WHERE folder_id=?", (folder_id,))}
                    for filename in sorted(filenames - existing_tracks.keys()):
                        connection.execute("INSERT INTO tracks(id,folder_id,name) VALUES(?,?,?)", (next_id("tracks"), folder_id, filename))
                        summary["tracks_added"] += 1
                    removed_tracks.extend(track_id for filename, track_id in existing_tracks.items() if filename not in filenames)
                missing_folders = [folder_id for folder_name, folder_id in existing_folders.items() if folder_name not in snapshot]
                for folder_id in missing_folders:
                    removed_tracks.extend(r[0] for r in connection.execute("SELECT id FROM tracks WHERE folder_id=?", (folder_id,)))
                for track_id in removed_tracks:
                    connection.execute("DELETE FROM tracks WHERE id=?", (track_id,))
                for folder_id in missing_folders:
                    connection.execute("DELETE FROM folders WHERE id=?", (folder_id,))
                summary["tracks_removed"] = len(removed_tracks)
                summary["folders_removed"] = len(missing_folders)
        # Same-path external replacements can invalidate analysis too.
        library_changed()
        app.extensions['normalization'].automatic()
        return summary

    @app.get(PREFIX + "/library/revision")
    def library_revision():
        return {"revision": revision}

    @app.get(PREFIX + "/tracks")
    def tracks():
        folder_id = request.args.get("folder_id", type=int)
        if folder_id:
            allowed(folder_id)
        rows = db().execute("""SELECT t.id,t.name,t.folder_id,f.name AS folder_name FROM tracks t
            JOIN folders f ON f.id=t.folder_id WHERE (? IS NULL OR f.id=?) AND
            (?='admin' OR EXISTS(SELECT 1 FROM grants WHERE folder_id=f.id AND user_id=?))
            ORDER BY f.name,t.name,t.id""", (folder_id, folder_id, g.user["role"], g.user["id"]))
        measurements = {r[0] for r in db().execute("SELECT fingerprint FROM audio_analysis")}
        result = []
        for row in rows.fetchall():
            item = dict(row)
            try:
                source = music / row['folder_name'] / row['name']
                if source.is_symlink() or not source.resolve().is_relative_to(music):
                    fail("Invalid music path", 400)
                item['analyzed'] = app.extensions['normalization'].fingerprint(source) in measurements
            except (OSError, HTTPException):
                item['analyzed'] = False
            result.append(item)
        return jsonify(result)

    @app.post(PREFIX + "/folders/<int:ident>/tracks")
    def upload(ident):
        admin()
        uploads = request.files.getlist("files")
        if not uploads:
            fail("請使用 multipart files 上傳音樂")
        saved = []
        result = []
        with lock:
            folder = path(ident)
            filenames = [name(f.filename) for f in uploads]
            if len(set(filenames)) != len(filenames):
                fail("上傳檔名重複", 409)
            for filename in filenames:
                if Path(filename).suffix.lower() not in EXTENSIONS:
                    fail("只支援 mp3、m4a、wav、ogg")
                if (folder / filename).exists():
                    fail("檔案已存在：" + filename, 409)
            try:
                for file, filename in zip(uploads, filenames):
                    target = folder / filename
                    saved.append(target)
                    with target.open("xb") as output:
                        shutil.copyfileobj(file.stream, output)
                    if target.stat().st_size == 0:
                        fail("不能上傳空檔案")
                    cursor = db().execute("INSERT INTO tracks(id,folder_id,name) VALUES(?,?,?)", (next_id("tracks"), ident, filename))
                    result.append({"id": cursor.lastrowid, "name": filename})
                db().commit()
            except Exception:
                db().rollback()
                for target in saved:
                    target.unlink(missing_ok=True)
                raise
        app.extensions['normalization'].automatic()
        return jsonify(result), 201

    @app.patch(PREFIX + "/tracks/<int:ident>")
    def rename_track(ident):
        admin()
        data = body()
        with lock:
            row = one("tracks", ident)
            value = name(name(data["title"]) + Path(row["name"]).suffix) if "title" in data else name(data.get("name"))
            if Path(value).suffix.lower() != Path(row["name"]).suffix.lower():
                fail("改名不能變更音樂格式副檔名")
            old, new = path(row["folder_id"], row["name"]), path(row["folder_id"], value)
            if old == new:
                return {"ok": True}
            if new.exists():
                fail("檔名已存在", 409)
            old.rename(new)
            try:
                db().execute("UPDATE tracks SET name=? WHERE id=?", (value, ident))
                db().commit()
            except Exception:
                db().rollback()
                new.rename(old)
                raise
        return {"ok": True}

    @app.post(PREFIX + "/tracks/move")
    def move_tracks():
        admin()
        data = body()
        selected, destination = ids(data.get("track_ids")), integer(data.get("folder_id"))
        with lock:
            folder = path(destination)
            rows = [one("tracks", i) for i in selected]
            rows = [r for r in rows if r["folder_id"] != destination]
            names = [r["name"] for r in rows]
            if len(set(names)) != len(names) or any((folder / n).exists() for n in names):
                fail("目的地有同名檔案，未搬移任何檔案", 409)
            moved = []
            try:
                for row in rows:
                    old, new = path(row["folder_id"], row["name"]), folder / row["name"]
                    old.rename(new)
                    moved.append((old, new))
                    db().execute("UPDATE tracks SET folder_id=? WHERE id=?", (destination, row["id"]))
                db().commit()
            except Exception:
                db().rollback()
                for old, new in reversed(moved):
                    new.rename(old)
                raise
        return {"ok": True, "count": len(rows)}

    @app.post(PREFIX + "/tracks/delete")
    def delete_tracks():
        admin()
        selected = ids(body().get("track_ids"))
        with lock:
            rows = [one("tracks", i) for i in selected]
            staged = []
            try:
                for row in rows:
                    old = path(row["folder_id"], row["name"])
                    temporary = music / (".trash-" + secrets.token_hex(16))
                    old.rename(temporary)
                    staged.append((old, temporary))
                    db().execute("DELETE FROM tracks WHERE id=?", (row["id"],))
                db().commit()
            except Exception:
                db().rollback()
                for old, temporary in reversed(staged):
                    temporary.rename(old)
                raise
            for _, temporary in staged:
                temporary.unlink()
        return {"ok": True, "count": len(rows)}

    def playback_state(data, restore=False):
        import math
        schema = data.get("schema_version", 1)
        if type(schema) is not int or schema != 1:
            fail("不支援的播放狀態版本")
        ident = data.get("track_id")
        if ident is not None:
            integer(ident)
        position = data.get("position", 0)
        mode = data.get("mode", "folder")
        if type(position) not in (int, float) or not math.isfinite(position) or position < 0:
            fail("position 必須為非負有限秒數")
        if mode not in ("single", "folder", "global", "random"):
            fail("mode 必須為 single、folder、global 或 random")
        reason = None
        if ident is not None:
            try:
                row = track(ident)
                if not path(row["folder_id"], row["name"]).is_file():
                    fail("音樂檔案不存在", 404)
            except HTTPException as error:
                if not restore or error.code not in (403, 404):
                    raise
                ident, position = None, 0
                reason = "unavailable"
        if ident is None:
            position = 0
        return {"schema_version": 1, "track_id": ident, "position": position, "mode": mode}, reason

    def playback_response(data, restore=False):
        current, reason = playback_state(data, restore)
        ident = current["track_id"]
        return {"state": current, "stream_url": f"{PREFIX}/tracks/{ident}/stream" if ident else None,
                "reason": reason}

    @app.post(PREFIX + "/playback/state")
    def serialize_playback():
        # Validate and normalize only; never persist a user's playback on the server.
        with lock:
            return playback_response(body())

    @app.post(PREFIX + "/playback/restore")
    def restore_playback():
        with lock:
            return playback_response(body(), restore=True)

    @app.post(PREFIX + "/playback/resolve")
    def resolve_playback():
        data = body()
        direction = data.get("direction", 1)
        trigger = data.get("trigger", "manual")
        if type(direction) is not int or direction not in (-1, 1):
            fail("direction 必須為 1 或 -1")
        if trigger not in ("manual", "ended"):
            fail("trigger 必須為 manual 或 ended")
        with lock:
            current, _ = playback_state(data)
            current_track = track(current["track_id"]) if current["track_id"] else None
            folder_id = current_track["folder_id"] if current_track and current["mode"] in ("single", "folder") else None
            rows = db().execute("""SELECT t.id FROM tracks t JOIN folders f ON f.id=t.folder_id
                WHERE (? IS NULL OR f.id=?) AND (?='admin' OR EXISTS
                (SELECT 1 FROM grants WHERE folder_id=f.id AND user_id=?)) ORDER BY f.name,t.name,t.id""",
                (folder_id, folder_id, g.user["role"], g.user["id"])).fetchall()
            queue = [r[0] for r in rows]
            if not queue:
                fail("沒有可播放的音樂", 404)
            if current["mode"] == "random":
                ident = secrets.choice([item for item in queue if item != current["track_id"]] or queue)
            elif current["mode"] == "single" and current_track and trigger == "ended":
                ident = current_track["id"]
            else:
                index = queue.index(current["track_id"]) if current["track_id"] in queue else (-1 if direction == 1 else 0)
                ident = queue[(index + direction) % len(queue)]
            return {"track_id": ident, "stream_url": f"{PREFIX}/tracks/{ident}/stream"}

    @app.get(PREFIX + "/tracks/<int:ident>/stream")
    def stream(ident):
        # Each Range request addresses the explicit track, independent of other players.
        with lock:
            row = track(ident)
            target = path(row["folder_id"], row["name"])
            if not target.is_file():
                fail("音樂檔案不存在", 404)
            mime = {".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".wav": "audio/wav", ".ogg": "audio/ogg"}
            return send_file(target, mimetype=mime[target.suffix.lower()], conditional=True, etag=True)

    @app.get("/")
    def index():
        return send_from_directory(BASE / "frontend", "index.html")

    @app.get("/assets/<path:filename>")
    def assets(filename):
        return send_from_directory(BASE / "frontend", filename)

    @app.get("/health")
    def health():
        db().execute("SELECT 1")
        return {"status": "ok"}

    from .normalization import install_normalization
    normalization = install_normalization(app, db, lock, path, admin, body, fail)

    from .hls import install_hls
    install_hls(app, db, lock, fail, body, track, path, playback_state, BASE, database)

    from .quick_test import install_quick_test
    install_quick_test(app, db, lock, fail, body, BASE, database)

    @app.get(PREFIX + "/playback/config")
    def playback_config():
        return {"hls_enabled": bool(config.get("hls", {}).get("enabled", True)) or normalization.preferences(g.user["id"])["enabled"]}

    from .library_watch import install_library_watch
    install_library_watch(app, db, lock, admin, body, fail, music, reconcile_library)

    from .radios import install_radios
    install_radios(app, db, lock, fail, body, path, database)

    with app.app_context():
        if db().execute('SELECT resume_batch OR auto_analyze FROM normalization_library WHERE id=1').fetchone()[0]:
            normalization.start(automatic=True)
    return app
