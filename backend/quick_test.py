"""Revocable account test links, independent of library playback."""
import math
import secrets
import shutil
import struct
import subprocess
import threading
import time
import wave

from flask import Response, g, request, send_file


def install_quick_test(app, db, lock, fail, body, base, database):
    cache = database.parent / 'quick-test-audio-v1'
    guard = threading.Lock()
    with app.app_context():
        db().execute('''CREATE TABLE IF NOT EXISTS quick_tests (
            user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            token TEXT UNIQUE)''')
        db().commit()

    def prepare():
        with guard:
            manifest = cache / 'tone.m3u8'
            if not (manifest.exists() and (cache / 'tone0.ts').exists()):
                ffmpeg = shutil.which(app.config['SETTINGS'].get('hls', {}).get('ffmpeg', 'ffmpeg'))
                if not ffmpeg:
                    fail('測試音效需要 FFmpeg，請聯絡管理員安裝。', 503)
                cache.mkdir(parents=True, exist_ok=True)
                source = cache / 'tone.wav'
                with wave.open(str(source), 'wb') as wav:
                    wav.setparams((1, 2, 48000, 0, 'NONE', 'not compressed'))
                    samples = bytearray()
                    for i in range(48000 * 6):
                        t = i / 48000
                        phase = t % 1
                        gain = max(0, min(1, phase / .01, (.25 - phase) / .01))
                        value = int(2500 * gain * (math.sin(2 * math.pi * 697 * t) + math.sin(2 * math.pi * 1209 * t)))
                        samples.extend(struct.pack('<h', value))
                    wav.writeframes(samples)
                try:
                    subprocess.run([ffmpeg, '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
                                    '-i', str(source), '-c:a', 'aac', '-b:a', '96k', '-ar', '48000',
                                    '-f', 'hls', '-hls_time', '10', '-hls_playlist_type', 'vod',
                                    '-hls_segment_filename', str(cache / 'tone%d.ts'), str(manifest)],
                                   check=True, capture_output=True, timeout=30)
                except (subprocess.SubprocessError, OSError):
                    manifest.unlink(missing_ok=True)
                    fail('測試音效建立失敗，請稍後再試。', 503)
            return float(next(line.split(':')[1].rstrip(',') for line in manifest.read_text().splitlines()
                              if line.startswith('#EXTINF:')))

    def state():
        row = db().execute('SELECT token FROM quick_tests WHERE user_id=?', (g.user['id'],)).fetchone()
        token = row['token'] if row else None
        return {'enabled': bool(token), 'url': '/quick-test/' + token + '/index.m3u8' if token else None}

    @app.route('/api/v1/mplayer/quick-test', methods=['GET', 'PUT'])
    def quick_test_settings():
        if request.method == 'PUT':
            enabled = body().get('enabled')
            if type(enabled) is not bool:
                fail('enabled 必須為布林值')
            if enabled:
                prepare()
            with lock:
                old = state()
                token = old['url'].split('/')[-2] if old['enabled'] else secrets.token_urlsafe(32)
                db().execute('INSERT INTO quick_tests(user_id,token) VALUES(?,?) '
                             'ON CONFLICT(user_id) DO UPDATE SET token=excluded.token',
                             (g.user['id'], token if enabled else None))
                db().commit()
        return state()

    def authorize(token):
        if len(token) != 43 or not db().execute(
                'SELECT 1 FROM quick_tests q JOIN users u ON u.id=q.user_id WHERE q.token=?', (token,)).fetchone():
            fail('測試連結無效或已關閉。', 404)

    @app.after_request
    def private_test(response):
        if request.path.startswith('/quick-test/'):
            response.headers['Cache-Control'] = 'no-store'
            response.headers['X-Robots-Tag'] = 'noindex, nofollow'
        return response

    @app.get('/quick-test/<token>/index.m3u8')
    def quick_test_playlist(token):
        authorize(token)
        duration = prepare()
        sequence = max(0, int(time.time() / duration) - 3)
        lines = ['#EXTM3U', '#EXT-X-VERSION:3', f'#EXT-X-TARGETDURATION:{math.ceil(duration)}',
                 f'#EXT-X-MEDIA-SEQUENCE:{sequence}', f'#EXT-X-DISCONTINUITY-SEQUENCE:{sequence}']
        for index in range(sequence, sequence + 4):
            lines.extend(['#EXT-X-DISCONTINUITY', f'#EXTINF:{duration:.6f},', f'tone/{index}.ts'])
        return Response('\n'.join(lines) + '\n', mimetype='application/vnd.apple.mpegurl')

    @app.get('/quick-test/<token>/tone/<int:index>.ts')
    def quick_test_segment(token, index):
        authorize(token)
        return send_file(cache / 'tone0.ts', mimetype='video/mp2t', conditional=False)