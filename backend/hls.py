"""Per-listener HLS EVENT playlists; cached AAC segments and bounded conversion jobs."""
import hashlib
import secrets
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from flask import Blueprint, Response, g, send_file

PREFIX = '/api/v1/mplayer'


def install_hls(app, db, lock, fail, body, track, path, playback_state, base, database):
    bp = Blueprint('hls', __name__, url_prefix=PREFIX)
    settings = app.config['SETTINGS'].get('hls', {})
    root = (base / settings.get('cache', str(database.parent / 'hls-cache'))).resolve()
    enabled = settings.get('enabled', True)
    ffmpeg = shutil.which(settings.get('ffmpeg', 'ffmpeg'))
    ttl = max(60, int(settings.get('idle_seconds', 3600)))
    max_sessions = max(1, int(settings.get('max_sessions', 32)))
    ahead_seconds = max(0, min(1800, int(settings.get('ahead_seconds', 300))))
    cache_limit = max(32, int(settings.get('cache_mb', 2048))) * 1024 * 1024
    sessions, assets = {}, {}
    pinned = set()
    last_cleanup = 0.
    guard = threading.RLock()
    # One encoder avoids saturating a NAS. HTTP threads never wait for encoding.
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='hls')
    app.extensions['hls'] = {'executor': pool, 'sessions': sessions}

    def cleanup():
        nonlocal last_cleanup
        now = time.time()
        for sid, session in list(sessions.items()):
            if now - session['touched'] > ttl:
                sessions.pop(sid, None)
        if now - last_cleanup < 30 or not root.exists():
            return
        last_cleanup = now
        used = {entry['key'] for s in sessions.values() for entry in s['entries']}
        directories = [p for p in root.iterdir() if p.is_dir() and len(p.name) == 64 and all(c in '0123456789abcdef' for c in p.name)]
        sizes = {p: sum(f.stat().st_size for f in p.iterdir() if f.is_file()) for p in directories}
        total = sum(sizes.values())
        for directory in sorted(directories, key=lambda p: p.stat().st_mtime):
            if total <= cache_limit:
                break
            if directory.name not in used and directory.name not in pinned:
                shutil.rmtree(directory)
                assets.pop(directory.name, None)
                total -= sizes[directory]

    def permitted(user_id, ident):
        row = db().execute('''SELECT t.id,t.name,t.folder_id FROM tracks t JOIN users u ON u.id=?
            WHERE t.id=? AND (u.role='admin' OR EXISTS
            (SELECT 1 FROM grants WHERE user_id=u.id AND folder_id=t.folder_id))''', (user_id, ident)).fetchone()
        if not row:
            raise ValueError('歌曲已移除或無權存取，請重新選歌。')
        source = path(row['folder_id'], row['name'])
        if not source.is_file():
            raise ValueError('音樂檔案不存在。')
        return row, source

    def convert(source):
        info = source.stat()
        key = hashlib.sha256(f'aac-v1:{source}:{info.st_size}:{info.st_mtime_ns}'.encode()).hexdigest()
        destination = root / key
        with guard:
            pinned.add(key)
            if key in assets:
                return key, assets[key]
        manifest = destination / 'index.m3u8'
        if not manifest.exists():
            root.mkdir(parents=True, exist_ok=True)
            temporary = root / ('building-' + secrets.token_hex(12))
            temporary.mkdir()
            try:
                command = [ffmpeg, '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
                           '-protocol_whitelist', 'file,pipe', '-i', str(source), '-map', '0:a:0', '-vn',
                           '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-ac', '2', '-threads', '1',
                           '-f', 'hls', '-hls_time', '6', '-hls_playlist_type', 'vod',
                           '-hls_segment_filename', str(temporary / 'seg%06d.ts'), str(temporary / 'index.m3u8')]
                subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.PIPE, check=True, timeout=300)
                temporary.rename(destination)
            finally:
                if temporary.exists():
                    shutil.rmtree(temporary)
        segments = []
        duration = None
        for line in manifest.read_text().splitlines():
            if line.startswith('#EXTINF:'):
                duration = float(line.split(':', 1)[1].split(',')[0])
            elif line and not line.startswith('#') and duration is not None:
                if '/' in line or '\\' in line or not line.endswith('.ts') or not (destination / line).is_file():
                    raise ValueError('歌曲資料尚未完整，請重新播放。')
                segments.append({'file': line, 'duration': duration})
                duration = None
        if not segments:
            raise ValueError('找不到可轉換的音訊。')
        result = {'segments': segments, 'duration': sum(s['duration'] for s in segments)}
        with guard:
            assets[key] = result
        return key, result

    def next_track(session):
        previous = session['entries'][-1]['track_id'] if session['entries'] else session['first']
        if not session['entries'] or session['mode'] == 'single':
            return previous
        current, _ = permitted(session['user_id'], previous)
        folder_id = current['folder_id'] if session['mode'] == 'folder' else None
        rows = db().execute('''SELECT t.id FROM tracks t JOIN folders f ON f.id=t.folder_id JOIN users u ON u.id=?
            WHERE (? IS NULL OR t.folder_id=?) AND (u.role='admin' OR EXISTS
            (SELECT 1 FROM grants WHERE user_id=u.id AND folder_id=t.folder_id)) ORDER BY f.name,t.name,t.id''',
            (session['user_id'], folder_id, folder_id)).fetchall()
        queue = [r[0] for r in rows]
        if not queue:
            raise ValueError('沒有可播放的音樂。')
        if session['mode'] == 'random':
            return secrets.choice([i for i in queue if i != previous] or queue)
        return queue[(queue.index(previous) + 1) % len(queue)]

    def needs_fill(session):
        count = len(session['entries']) - session['requested']
        frontier = max(session['requested_time'], session['published_time'])
        return (count < 3 or (session['publication_started'] is not None and session['duration'] - session['published_time'] < 30)
                or (count < 20 and session['duration'] - frontier < ahead_seconds))

    def fill(session):
        try:
            with app.app_context():
                while True:
                    with guard:
                        if session['id'] not in sessions or not needs_fill(session):
                            return
                        if len(session['entries']) >= 1000 or session['duration'] >= 86400:
                            session['finished'] = True
                            return
                    with lock:
                        ident = next_track(session)
                        row, source = permitted(session['user_id'], ident)
                        title = row['name']
                    key, asset = convert(source)
                    with guard:
                        if session['id'] not in sessions:
                            pinned.discard(key)
                            return
                        entry = {'index': len(session['entries']), 'track_id': ident, 'name': title,
                                 'start': session['duration'], 'duration': asset['duration'], 'key': key,
                                 'segments': asset['segments']}
                        session['entries'].append(entry)
                        session['duration'] += asset['duration']
                        pinned.discard(key)
                        cleanup()
        except Exception:
            app.logger.exception('HLS preparation failed')
            with guard:
                session['error'] = '歌曲準備失敗，請稍後重試，或聯絡管理員檢查檔案與伺服器。'
        finally:
            with guard:
                session['building'] = False
                pinned.clear()
                if session['id'] in sessions:
                    schedule(session)

    def schedule(session):
        if not session['building'] and not session['error'] and not session['finished'] and needs_fill(session):
            session['building'] = True
            pool.submit(fill, session)

    def get_session(sid):
        cleanup()
        session = sessions.get(sid)
        if not session or session['user_id'] != g.user['id']:
            fail('播放連線已過期，請重新播放', 404)
        session['touched'] = time.time()
        return session

    def snapshot(session):
        entries = [{k: e[k] for k in ('index', 'track_id', 'name', 'start', 'duration')} for e in session['entries']]
        return {'id': session['id'], 'playlist_url': f"{PREFIX}/tracks/hls/{session['id']}/index.m3u8",
                'ready': bool(entries), 'entries': entries, 'mode': session['mode'], 'error': session['error'],
                'finished': session['finished'],
                'diagnostics': {'building': session['building'], 'prepared_seconds': session['duration'],
                                'requested_seconds': session['requested_time'],
                                'published_seconds': session['published_time'],
                                'ahead_seconds': max(0, session['duration'] - session['requested_time'])},
                'position': min(session['position'], max(0, entries[0]['duration'] - .1)) if entries else session['position']}

    @bp.get('/hls/status')
    def status():
        return {'enabled': bool(enabled and ffmpeg), 'reason': None if enabled and ffmpeg else '播放服務尚未就緒，請聯絡管理員'}

    @bp.post('/hls/sessions')
    def create():
        if not enabled or not ffmpeg:
            fail('播放服務尚未就緒，請聯絡管理員', 503)
        with lock:
            state, _ = playback_state(body())
            if not state['track_id']:
                fail('請先選擇歌曲')
        with guard:
            cleanup()
            if len(sessions) >= max_sessions:
                fail('目前播放人數較多，請稍後再試', 503)
            sid = secrets.token_urlsafe(24)
            session = dict(id=sid, user_id=g.user['id'], first=state['track_id'], position=state['position'],
                           mode=state['mode'], entries=[], duration=0., requested=0, requested_time=state['position'], touched=time.time(),
                           epoch=time.time(), publication_started=None, publication_base=0., published_time=0.,
                           building=False, error=None, finished=False)
            sessions[sid] = session
            schedule(session)
            return snapshot(session), 202

    @bp.get('/hls/sessions/<sid>')
    def state(sid):
        with guard:
            session = get_session(sid)
            # Metadata must not disclose names after permissions are revoked.
            with lock:
                for entry in session['entries']:
                    track(entry['track_id'])
            schedule(session)
            return snapshot(session)

    @bp.delete('/hls/sessions/<sid>')
    def stop(sid):
        with guard:
            get_session(sid)
            sessions.pop(sid)
            cleanup()
        return {'ok': True}

    @bp.get('/tracks/hls/<sid>/index.m3u8')
    def playlist(sid):
        with guard:
            session = get_session(sid)
            if not session['entries']:
                fail(session['error'] or '正在準備歌曲，請稍候', 503)
            with lock:
                for entry in session['entries']:
                    track(entry['track_id'])
            schedule(session)
            # Publish on a clock, independently of segment downloads or JS.
            # An unchanged, open EVENT playlist can be treated as a stale stream.
            now = time.monotonic()
            if session['publication_started'] is None:
                session['publication_started'] = now
                session['publication_base'] = session['duration']
            # Match TARGETDURATION; each tick exceeds a 6s segment plus its AAC frame.
            horizon = session['publication_base'] + int((now - session['publication_started']) / 7) * 7
            horizon = max(horizon, session['published_time'])
            # Six-second segments may include one AAC frame past the target.
            lines = ['#EXTM3U', '#EXT-X-VERSION:6', '#EXT-X-TARGETDURATION:7',
                     '#EXT-X-PLAYLIST-TYPE:EVENT', '#EXT-X-MEDIA-SEQUENCE:0',
                     f'#EXT-X-START:TIME-OFFSET={snapshot(session)["position"]:.6f},PRECISE=YES']
            published = 0.
            for entry in session['entries']:
                if published >= horizon - .000001:
                    break
                if entry['index']:
                    lines.append('#EXT-X-DISCONTINUITY')
                date = datetime.fromtimestamp(session['epoch'] + entry['start'], timezone.utc).isoformat()
                lines += [f'#EXT-X-PROGRAM-DATE-TIME:{date}',
                          f'#EXT-X-DATERANGE:ID="song-{entry["index"]}",CLASS="com.sonora.track",START-DATE="{date}",DURATION={entry["duration"]:.6f},X-TRACK-ID="{entry["track_id"]}"']
                for index, segment in enumerate(entry['segments']):
                    if published >= horizon - .000001:
                        break
                    lines += [f'#EXTINF:{segment["duration"]:.6f},', f'{entry["index"]}/{index}.ts']
                    published += segment['duration']
            session['published_time'] = published
            schedule(session)
            if (session['finished'] or session['error']) and published >= session['duration'] - .001:
                lines.append('#EXT-X-ENDLIST')
            return Response('\n'.join(lines) + '\n', mimetype='application/vnd.apple.mpegurl')

    @bp.get('/tracks/hls/<sid>/<int:entry_index>/<int:segment_index>.ts')
    def segment(sid, entry_index, segment_index):
        with guard:
            session = get_session(sid)
            if entry_index >= len(session['entries']):
                fail('找不到播放資料，請重新播放', 404)
            entry = session['entries'][entry_index]
            if segment_index >= len(entry['segments']):
                fail('找不到播放資料，請重新播放', 404)
            with lock:
                track(entry['track_id'])
            session['requested'] = max(session['requested'], entry_index)
            session['requested_time'] = max(session['requested_time'], entry['start'] + sum(s['duration'] for s in entry['segments'][:segment_index+1]))
            schedule(session)
            target = root / entry['key'] / entry['segments'][segment_index]['file']
            return send_file(target, mimetype='video/mp2t', conditional=True)

    app.register_blueprint(bp)
