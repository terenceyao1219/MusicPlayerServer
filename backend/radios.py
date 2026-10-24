"""Personal resumable stations, with one active playback lease per station."""
import base64
import hashlib
import hmac
import json
import os
import random
import re
import secrets
import threading
import time
from functools import lru_cache
from flask import Response, g, jsonify, request, send_file
from werkzeug.exceptions import HTTPException
from .radio_assets import RadioAudio, artwork

PREFIX = '/api/v1/mplayer/radios'
LEASE_SECONDS = 45
REWIND_SECONDS = 12


def install_radios(app, db, library_lock, fail, body, path, database):
    normalization = app.extensions['normalization']
    guard, active = threading.RLock(), {}
    last_clean = [0.]
    settings = app.config['SETTINGS'].get('hls', {})
    cache = RadioAudio(database.parent / 'radio-cache', settings.get('ffmpeg', 'ffmpeg'),
                       max(32, int(settings.get('cache_mb', 2048))) * 1024 * 1024, normalization)
    keyfile = database.parent / 'radio-secret.key'
    if not keyfile.exists():
        with app.app_context():
            exists = db().execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='radios'").fetchone()
            if exists and db().execute('SELECT 1 FROM radios LIMIT 1').fetchone():
                raise RuntimeError('缺少 radio-secret.key，請一併恢復電台金鑰備份。')
    try:
        fd = os.open(keyfile, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, 'wb') as f:
            f.write(secrets.token_bytes(32))
    secret = keyfile.read_bytes()
    if len(secret) != 32:
        raise RuntimeError('radio-secret.key 必須為 32 bytes；請由備份恢復。')
    with app.app_context():
        db().execute('''CREATE TABLE IF NOT EXISTS radios (
            id TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name TEXT NOT NULL, scope TEXT NOT NULL, folders TEXT NOT NULL, ordering TEXT NOT NULL,
            policy TEXT NOT NULL, enabled INTEGER NOT NULL, resume INTEGER NOT NULL,
            background TEXT NOT NULL, foreground TEXT NOT NULL, nonce TEXT NOT NULL,
            progress TEXT NOT NULL DEFAULT '{}')''')
        db().execute('''CREATE TABLE IF NOT EXISTS radio_accounts (
            user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            nonce TEXT NOT NULL)''')
        for row in db().execute('SELECT DISTINCT user_id FROM radios').fetchall():
            db().execute('INSERT OR IGNORE INTO radio_accounts VALUES(?,?)',
                         (row['user_id'], secrets.token_hex(16)))
        db().execute("UPDATE radios SET policy='replace'")
        db().commit()
    app.extensions['radios'] = {'active': active, 'cache': cache}

    def get(ident, owner=False):
        row = db().execute('SELECT * FROM radios WHERE id=?', (ident,)).fetchone()
        if not row or (owner and row['user_id'] != g.user['id']):
            fail('找不到續播電台。', 404)
        return dict(row)

    def password(row):
        account = db().execute('SELECT nonce FROM radio_accounts WHERE user_id=?', (row['user_id'],)).fetchone()
        digest = hmac.new(secret, ('radio-user:' + str(row['user_id']) + ':' + account['nonce']).encode(), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).decode().rstrip('=')

    def basic(ident):
        row = get(ident)
        auth = request.authorization
        valid = auth and auth.type == 'basic' and hmac.compare_digest(
            (auth.username or '').encode(), ('listener-' + str(row['user_id'])).encode()) and hmac.compare_digest(
                (auth.password or '').encode(), password(row).encode())
        if not valid:
            return None, Response('需要電台播放帳號與密碼。', 401,
                                  {'WWW-Authenticate': 'Basic realm="SONORA Radio", charset="UTF-8"'})
        if not row['enabled']:
            fail('此電台已停用。', 403)
        return row, None

    def available(row):
        user = db().execute('SELECT role FROM users WHERE id=?', (row['user_id'],)).fetchone()
        if not user:
            return []
        rows = db().execute('''SELECT t.id,t.folder_id,t.name FROM tracks t JOIN folders f ON f.id=t.folder_id
            WHERE (?='admin' OR EXISTS(SELECT 1 FROM grants WHERE user_id=? AND folder_id=t.folder_id))
            ORDER BY f.name,t.name,t.id''', (user['role'], row['user_id'])).fetchall()
        selected, result = set(json.loads(row['folders'])), []
        for track in rows:
            if row['scope'] == 'folders' and track['folder_id'] not in selected:
                continue
            try:
                source = path(track['folder_id'], track['name'])
                if source.is_file():
                    result.append((track['id'], source))
            except (OSError, HTTPException):
                pass
        return result

    def serialize(row):
        result = {k: row[k] for k in ('id', 'name', 'scope', 'ordering', 'background', 'foreground')}
        result.update(enabled=bool(row['enabled']), resume=bool(row['resume']), folder_ids=json.loads(row['folders']),
                      stream_url=f"/radio/{row['id']}/index.m3u8", username='listener-' + str(row['user_id']),
                      password=password(row), icon_url=f"/radio/{row['id']}/icon.png",
                      progress=json.loads(row['progress']))
        session = active.get(row['id'])
        result['connected'] = bool(session and time.monotonic() - session['touch'] < LEASE_SECONDS)
        return result

    def validate(data):
        name = data.get('name', '')
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 80 or any(ord(c) < 32 for c in name):
            fail('電台名稱須為 1–80 個字元。')
        scope, order, policy = data.get('scope'), data.get('ordering'), 'replace'
        if scope not in ('all', 'folders') or order not in ('sequential', 'shuffle'):
            fail('播放範圍或順序不正確。')
        folders = data.get('folder_ids', [])
        if not isinstance(folders, list) or len(folders) > 1000 or any(type(x) is not int or x <= 0 for x in folders):
            fail('資料夾清單不正確。')
        folders = sorted(set(folders)) if scope == 'folders' else []
        if scope == 'folders' and not folders:
            fail('請至少選擇一個資料夾。')
        allowed = {r[0] for r in db().execute('''SELECT id FROM folders WHERE ?='admin' OR EXISTS
                    (SELECT 1 FROM grants WHERE user_id=? AND folder_id=folders.id)''', (g.user['role'], g.user['id']))}
        if not set(folders) <= allowed:
            fail('選取的資料夾已不存在或權限已收回，請重新選擇。', 403)
        for key in ('enabled', 'resume'):
            if type(data.get(key)) is not bool:
                fail('啟用與續播設定必須為布林值。')
        for key in ('background', 'foreground'):
            if not isinstance(data.get(key), str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', data[key]):
                fail('圖示配色必須為六位十六進位色碼。')
        return (name.strip(), scope, json.dumps(folders), order, policy, int(data['enabled']), int(data['resume']),
                data['background'].lower(), data['foreground'].lower())

    def plan(row, state, ids):
        current = state.get('track_id')
        remaining = [x for x in state.get('remaining', []) if x in ids]
        seen = [x for x in state.get('seen', []) if x in ids]
        if row['ordering'] == 'shuffle':
            added = [x for x in ids if x not in remaining and x not in seen]
            random.SystemRandom().shuffle(added)
            remaining.extend(added)
            if not remaining:
                remaining = list(ids)
                random.SystemRandom().shuffle(remaining)
                seen = []
                if len(remaining) > 1 and remaining[0] == current:
                    remaining[0], remaining[1] = remaining[1], remaining[0]
            selected = remaining.pop(0)
            seen.append(selected)
        else:
            selected = ids[(ids.index(current) + 1) % len(ids)] if current in ids else ids[0]
            remaining, seen = [], []
        return {'track_id': selected, 'offset': 0, 'remaining': remaining, 'seen': seen}

    def checkpoint(row, session):
        if not row['resume'] or not session['segments'] or session['origin'] is None:
            return
        elapsed = session['base'] + max(0, min(time.monotonic(), session['touch']) - session['origin'])
        point = max(0, min(elapsed, session['requested']) - REWIND_SECONDS)
        segment = next((s for s in reversed(session['segments']) if s['start'] <= point), session['segments'][0])
        progress = dict(segment['state'])
        progress['offset'] = segment['offset']
        db().execute('UPDATE radios SET progress=? WHERE id=?', (json.dumps(progress), row['id']))
        db().commit()

    def warm(row):
        sources = available(row)
        if sources and row['enabled']:
            progress = json.loads(row['progress']) if row['resume'] else {}
            if progress.get('track_id') not in dict(sources):
                progress = plan(row, progress, [x[0] for x in sources])
                if row['resume']:
                    row['progress'] = json.dumps(progress)
                    db().execute('UPDATE radios SET progress=? WHERE id=?', (row['progress'], row['id']))
                    db().commit()
            source = dict(sources)[progress['track_id']]
            try:
                cache.get(source, normalization.preferences(row['user_id']))
            except ValueError:
                pass

    @app.route(PREFIX, methods=['GET', 'POST'])
    def radios():
        with guard, library_lock:
            if request.method == 'GET':
                return jsonify([serialize(dict(r)) for r in db().execute(
                    'SELECT * FROM radios WHERE user_id=? ORDER BY name,id', (g.user['id'],))])
            if db().execute('SELECT COUNT(*) FROM radios WHERE user_id=?', (g.user['id'],)).fetchone()[0] >= 20:
                fail('每個帳號最多建立 20 個續播電台。')
            values = validate(body())
            db().execute('INSERT OR IGNORE INTO radio_accounts VALUES(?,?)',
                         (g.user['id'], secrets.token_hex(16)))
            ident = secrets.token_hex(16)
            db().execute('''INSERT INTO radios(id,user_id,name,scope,folders,ordering,policy,enabled,resume,
                background,foreground,nonce) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
                         (ident, g.user['id'], *values, secrets.token_hex(16)))
            db().commit()
            row = get(ident)
            warm(row)
            return serialize(row), 201

    @app.route(PREFIX + '/<ident>', methods=['PUT', 'DELETE'])
    def radio_edit(ident):
        with guard, library_lock:
            row = get(ident, True)
            if request.method == 'DELETE':
                active.pop(ident, None)
                db().execute('DELETE FROM radios WHERE id=?', (ident,))
                db().commit()
                return {'ok': True}
            values = validate(body())
            session = active.pop(ident, None)
            if session:
                checkpoint(row, session)
            reset = (row['scope'], row['folders'], row['ordering']) != (values[1], values[2], values[3])
            db().execute('''UPDATE radios SET name=?,scope=?,folders=?,ordering=?,policy=?,enabled=?,resume=?,
                background=?,foreground=? WHERE id=?''', (*values, ident))
            if reset or not values[6]:
                db().execute("UPDATE radios SET progress='{}' WHERE id=?", (ident,))
            db().commit()
            row = get(ident)
            warm(row)
            return serialize(row)

    @app.post(PREFIX + '/<ident>/<action>')
    def radio_action(ident, action):
        with guard, library_lock:
            row = get(ident, True)
            if action not in ('reset', 'credentials', 'disconnect', 'toggle'):
                fail('找不到此操作。', 404)
            if action == 'toggle':
                enabled = body().get('enabled')
                if type(enabled) is not bool:
                    fail('enabled 必須為布林值。')
            session = active.pop(ident, None)
            if session and action != 'reset':
                checkpoint(row, session)
            if action == 'toggle':
                db().execute('UPDATE radios SET enabled=? WHERE id=?', (int(enabled), ident))
            if action == 'reset':
                db().execute("UPDATE radios SET progress='{}' WHERE id=?", (ident,))
            if action == 'credentials':
                for owned in db().execute('SELECT * FROM radios WHERE user_id=?', (row['user_id'],)).fetchall():
                    other = active.pop(owned['id'], None)
                    if other:
                        checkpoint(dict(owned), other)
                db().execute('UPDATE radio_accounts SET nonce=? WHERE user_id=?',
                             (secrets.token_hex(16), row['user_id']))
            db().commit()
            return serialize(get(ident))

    @app.after_request
    def radio_headers(response):
        if request.path.startswith('/radio/'):
            response.headers['Cache-Control'] = 'no-store'
            response.headers['X-Robots-Tag'] = 'noindex, nofollow'
            if response.status_code == 503:
                response.headers['Retry-After'] = '2'
        return response

    @lru_cache(maxsize=64)
    def icon(bg, fg):
        return artwork(bg, fg)

    @app.get('/radio/<ident>/icon.png')
    def radio_icon(ident):
        row = get(ident)
        return Response(icon(row['background'], row['foreground']), mimetype='image/png')

    def retire_idle():
        now = time.monotonic()
        for ident, session in list(active.items()):
            if now - session['touch'] >= LEASE_SECONDS:
                active.pop(ident)
        trim_cache()

    def trim_cache():
        now = time.monotonic()
        if now - last_clean[0] >= 60:
            pinned = {s['key'] for session in active.values() for s in session['segments']}
            pinned.update(session['pending']['next_key'] for session in active.values()
                          if session['pending'] and session['pending'].get('next_key'))
            cache.cleanup(pinned)
            last_clean[0] = now

    @app.route('/radio/<ident>/index.m3u8', methods=['GET', 'HEAD'])
    def radio_master(ident):
        with guard, library_lock:
            row, error = basic(ident)
            if error is not None:
                return error
            if request.method == 'HEAD':
                return Response('', mimetype='application/vnd.apple.mpegurl')
            retire_idle()
            old = active.get(ident)
            cookie = request.cookies.get('sonora_radio_' + ident)
            if old and (cookie != old['id'] or old['normalization'] != normalization.preferences(row['user_id'])):
                checkpoint(row, old)
                old = None
            if not old or cookie != old['id']:
                if not old and ident not in active and len(active) >= 32:
                    fail('目前播放人數較多，請稍後再試。', 503)
                sources = available(row)
                if not sources:
                    fail('沒有可播放的歌曲，請檢查資料夾權限。', 404)
                state = json.loads(get(ident)['progress']) if row['resume'] else {}
                ids = [x[0] for x in sources]
                if state.get('track_id') not in ids:
                    state = plan(row, state, ids)
                if row['resume']:
                    # Cold-start retries must keep the same shuffled selection.
                    db().execute('UPDATE radios SET progress=? WHERE id=?', (json.dumps(state), ident))
                    db().commit()
                old = dict(id=secrets.token_urlsafe(24), touch=time.monotonic(), origin=None, base=0,
                           requested=0, segments=[], state=state, duration=0., seq=0, disc=0,
                           pending=None, last_track=None, normalization=normalization.preferences(row['user_id']))
                active[ident] = old
            response = Response('#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-STREAM-INF:BANDWIDTH=210000,CODECS="mp4a.40.2"\n'
                                + f"{old['id']}/live.m3u8\n", mimetype='application/vnd.apple.mpegurl')
            secure = app.config['SETTINGS'].get('security', {}).get('cookie_secure', False) or request.is_secure
            response.set_cookie('sonora_radio_' + ident, old['id'], httponly=True, secure=secure,
                                samesite='Lax', path=f'/radio/{ident}/', max_age=86400)
            return response

    def lease(ident, sid):
        row, error = basic(ident)
        if error is not None:
            return None, None, error
        session = active.get(ident)
        if not session or session['id'] != sid or time.monotonic() - session['touch'] >= LEASE_SECONDS:
            if session and session['id'] == sid:
                active.pop(ident, None)
            fail('播放連線已被取代或逾期，請重新播放電台。', 410)
        return row, session, None

    def fill(row, session, sources, horizon):
        ids = list(sources)
        if not ids:
            fail('資料夾權限已收回或沒有可播放的歌曲。', 403)
        for _ in range(128):
            if session['duration'] >= horizon:
                break
            pending = session['pending']
            if pending is None:
                state = session['state']
                if state.get('track_id') not in ids:
                    state = plan(row, state, ids)
                    session['state'] = state
                try:
                    asset = cache.get(sources[state['track_id']], session['normalization'])
                except ValueError as error:
                    fail(str(error), 503)
                if asset is None:
                    break
                key, parts = asset
                offset = max(0, float(state.get('offset', 0)))
                position, selected = 0., []
                for filename, duration in parts:
                    if position + duration > offset:
                        selected.append((filename, duration, position))
                    position += duration
                if not selected:
                    session['state'] = plan(row, state, ids)
                    continue
                pending = {'parts': selected, 'key': key, 'state': dict(state),
                           'next_state': plan(row, state, ids)}
                # Start preparing the next song while the current song is playing.
                next_source = sources[pending['next_state']['track_id']]
                pending['next_key'] = cache.key(next_source, session['normalization'])
                try:
                    cache.get(next_source, session['normalization'])
                except ValueError:
                    pass
                session['pending'] = pending
            if pending['state']['track_id'] not in ids:
                session['pending'] = None
                session['state'] = plan(row, pending['state'], ids)
                continue
            filename, duration, offset = pending['parts'].pop(0)
            track_id = pending['state']['track_id']
            boundary = session['last_track'] is not None and offset == 0
            if boundary:
                session['disc'] += 1
            session['segments'].append(dict(seq=session['seq'], start=session['duration'], duration=duration,
                offset=offset, key=pending['key'], filename=filename, track_id=track_id,
                state=pending['state'], disc=session['disc'], boundary=boundary))
            session['seq'] += 1
            session['duration'] += duration
            session['last_track'] = track_id
            if not pending['parts']:
                session['pending'] = None
                session['state'] = pending['next_state']

    @app.get('/radio/<ident>/<sid>/live.m3u8')
    def radio_playlist(ident, sid):
        deadline = time.monotonic() + app.config.get('RADIO_STARTUP_WAIT', 8)
        while True:
            with guard, library_lock:
                row, session, error = lease(ident, sid)
                if error is not None:
                    return error
                if session['origin'] is None:
                    session['touch'] = time.monotonic()
                sources = dict(available(row))
                if any(s['track_id'] not in sources for s in session['segments']):
                    active.pop(ident, None)
                    fail('資料夾權限或曲目已變更，請重新播放以略過無權限歌曲。', 410)
                elapsed = 0 if session['origin'] is None else session['base'] + time.monotonic() - session['origin']
                fill(row, session, sources, elapsed + 24)
                # Native HLS clients need at least three target durations to start.
                if session['origin'] is None and session['duration'] < 21:
                    if time.monotonic() >= deadline:
                        fail('歌曲正在準備，請稍後重試。', 503)
                    waiting = True
                else:
                    waiting = False
                if not waiting:
                    cutoff = min(max(0, elapsed - 18), max(0, session['duration'] - 21))
                    visible = [s for s in session['segments'] if s['start'] + s['duration'] > cutoff]
                    if not visible:
                        fail('歌曲正在準備，請稍後重試。', 503)
                    session['segments'] = visible
                    trim_cache()
                    first = visible[0]
                    lines = ['#EXTM3U', '#EXT-X-VERSION:6', '#EXT-X-TARGETDURATION:7',
                             f"#EXT-X-MEDIA-SEQUENCE:{first['seq']}",
                             f"#EXT-X-DISCONTINUITY-SEQUENCE:{first['disc'] - int(first['boundary'])}",
                             '#EXT-X-START:TIME-OFFSET=0,PRECISE=YES']
                    for segment in visible:
                        if segment['boundary']:
                            lines.append('#EXT-X-DISCONTINUITY')
                        lines.extend([f"#EXTINF:{segment['duration']:.6f},", f"{segment['seq']}.ts"])
                    return Response('\n'.join(lines) + '\n', mimetype='application/vnd.apple.mpegurl')
            # Never hold the library/session locks while waiting for the encoder.
            time.sleep(.05)

    @app.get('/radio/<ident>/<sid>/<int:sequence>.ts')
    def radio_segment(ident, sid, sequence):
        with guard, library_lock:
            row, session, error = lease(ident, sid)
            if error is not None:
                return error
            segment = next((s for s in session['segments'] if s['seq'] == sequence), None)
            if not segment:
                fail('音訊片段已超出播放視窗，請重新播放。', 404)
            source = dict(available(row)).get(segment['track_id'])
            if source is None:
                fail('此歌曲的權限已收回或檔案已移除。', 403)
            if cache.key(source, session['normalization']) != segment['key']:
                active.pop(ident, None)
                fail('歌曲檔案已更新，請重新播放。', 410)
            if session['origin'] is None:
                session['origin'] = time.monotonic()
                session['base'] = segment['start']
            session['touch'] = time.monotonic()
            session['requested'] = max(session['requested'], segment['start'])
            checkpoint(row, session)
            return send_file(cache.root / segment['key'] / segment['filename'],
                             mimetype='video/mp2t', conditional=False)

