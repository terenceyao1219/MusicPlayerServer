"""Persistent loudness measurements; serialized foreground-priority audio work."""
import hashlib
import json
import math
import shutil
import subprocess
import threading
from contextlib import contextmanager

from flask import g, request

PREFIX = '/api/v1/mplayer/normalization'
DEFAULT = {'enabled': False, 'target': -16, 'peak': -2}
VERSION = 'lufs-stereo48-v1'


class AudioWork:
    def __init__(self):
        self.condition = threading.Condition()
        self.busy = False
        self.waiting = 0

    @contextmanager
    def slot(self, background=False):
        with self.condition:
            if not background:
                self.waiting += 1
            try:
                while self.busy or (background and self.waiting):
                    self.condition.wait()
                self.busy = True
            finally:
                if not background:
                    self.waiting -= 1
        try:
            yield
        finally:
            with self.condition:
                self.busy = False
                self.condition.notify_all()


class Normalization:
    def __init__(self, app, db, lock, path):
        self.app, self.db, self.lock, self.path = app, db, lock, path
        self.ffmpeg = shutil.which(app.config['SETTINGS'].get('hls', {}).get('ffmpeg', 'ffmpeg'))
        self.work = AudioWork()
        self.guard = threading.RLock()
        self.running = False
        self.cancel = threading.Event()
        self.rescan = False
        self.progress = {'total': 0, 'completed': 0, 'failed': 0, 'current': None}
        with app.app_context():
            db().executescript("""
                CREATE TABLE IF NOT EXISTS normalization_preferences (
                    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                    enabled INTEGER NOT NULL, target INTEGER NOT NULL, peak INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS audio_analysis (
                    fingerprint TEXT PRIMARY KEY, loudness REAL, peak REAL);
                CREATE TABLE IF NOT EXISTS normalization_library (
                    id INTEGER PRIMARY KEY CHECK(id=1), auto_analyze INTEGER NOT NULL,
                    resume_batch INTEGER NOT NULL);
                INSERT OR IGNORE INTO normalization_library VALUES(1,0,0);
            """)
            db().commit()

    @staticmethod
    def fingerprint(source):
        info = source.stat()
        return hashlib.sha256(f'{VERSION}:{source}:{info.st_size}:{info.st_mtime_ns}'.encode()).hexdigest()

    @staticmethod
    def suffix(preferences):
        return f":{VERSION}:{preferences['target']}:{preferences['peak']}" if preferences['enabled'] else ''

    def preferences(self, user_id):
        row = self.db().execute('SELECT enabled,target,peak FROM normalization_preferences WHERE user_id=?',
                                (user_id,)).fetchone()
        return dict(row, enabled=bool(row['enabled'])) if row else dict(DEFAULT)

    def measured(self, source):
        return self.db().execute('SELECT loudness,peak FROM audio_analysis WHERE fingerprint=?',
                                 (self.fingerprint(source),)).fetchone()

    def ready(self, source):
        try:
            return self.measured(source) is not None
        except OSError:
            return False

    def analyze(self, source):
        # Caller holds shared slot. Analyze the same downmix as the encoder.
        fingerprint = self.fingerprint(source)
        row = self.measured(source)
        if row is not None:
            return row
        if not self.ffmpeg:
            raise ValueError('找不到 FFmpeg，無法分析音量。')
        result = subprocess.run([self.ffmpeg, '-nostdin', '-hide_banner', '-nostats', '-v', 'info',
            '-protocol_whitelist', 'file,pipe', '-i', str(source), '-map', '0:a:0', '-vn',
            '-af', 'aresample=48000,aformat=channel_layouts=stereo,loudnorm=print_format=json',
            '-threads', '1', '-filter_threads', '1', '-f', 'null', '-'], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE, check=True, timeout=300)
        output = result.stderr.decode('utf-8', errors='replace')
        data = json.loads(output[output.rfind('{'):output.rfind('}') + 1])
        loudness, peak = float(data['input_i']), float(data['input_tp'])
        # Silence / below-gate audio: no meaningful integrated loudness, no boost.
        if loudness == -math.inf:
            loudness = None
        elif not math.isfinite(loudness):
            raise ValueError('無效的響度分析結果')
        if peak == -math.inf:
            peak = None
        elif not math.isfinite(peak):
            raise ValueError('無效的峰值分析結果')
        if fingerprint != self.fingerprint(source):
            raise ValueError('歌曲分析期間已變更，請重試。')
        with self.lock:
            self.db().execute('INSERT OR REPLACE INTO audio_analysis VALUES(?,?,?)', (fingerprint, loudness, peak))
            self.db().commit()
            self.app.extensions['library_changed']()
        return {'loudness': loudness, 'peak': peak}

    def filters(self, source, preferences):
        if not preferences['enabled']:
            return []
        row = self.analyze(source)
        gain = 0. if row['loudness'] is None else preferences['target'] - row['loudness']
        if row['peak'] is not None:
            gain = min(gain, preferences['peak'] - row['peak'])
        return ['-af', f'aresample=48000,aformat=channel_layouts=stereo,volume={gain:.6f}dB']

    def status(self):
        with self.guard:
            auto = bool(self.db().execute('SELECT auto_analyze FROM normalization_library WHERE id=1').fetchone()[0])
            return dict(self.progress, running=self.running, stopping=self.cancel.is_set() and self.running,
                        auto_analyze=auto)

    def start(self, automatic=False):
        with self.guard:
            if self.running:
                if automatic:
                    self.rescan = True
                return
            self.cancel.clear()
            self.running = True
            self.progress = {'total': 0, 'completed': 0, 'failed': 0, 'current': None}
            with self.lock:
                self.db().execute('UPDATE normalization_library SET resume_batch=1 WHERE id=1')
                self.db().commit()
            self.worker = threading.Thread(target=self.batch, daemon=True, name='audio-analysis')
            self.worker.start()

    def automatic(self):
        if self.db().execute('SELECT auto_analyze FROM normalization_library WHERE id=1').fetchone()[0]:
            self.start(automatic=True)

    def batch(self):
        try:
            with self.app.app_context():
                while True:
                    rows = self.db().execute('SELECT id,folder_id,name FROM tracks ORDER BY id').fetchall()
                    with self.guard:
                        self.progress.update(total=len(rows), completed=0, failed=0)
                    for row in rows:
                        if self.cancel.is_set():
                            break
                        with self.guard:
                            self.progress['current'] = row['name']
                        try:
                            with self.lock:
                                source = self.path(row['folder_id'], row['name'])
                            if not self.ready(source):
                                before = self.fingerprint(source)
                                if self.cancel.wait(.5):
                                    break
                                if before != self.fingerprint(source):
                                    raise ValueError('歌曲仍在寫入，請稍後重新分析。')
                                with self.work.slot(background=True):
                                    if self.cancel.is_set():
                                        break
                                    self.analyze(source)
                            with self.guard:
                                self.progress['completed'] += 1
                        except Exception:
                            self.app.logger.exception('Audio analysis failed for track %s', row['id'])
                            with self.guard:
                                self.progress['failed'] += 1
                    with self.guard:
                        if self.rescan and not self.cancel.is_set():
                            self.rescan = False
                            continue
                        self.rescan = False
                        with self.lock:
                            self.db().execute('UPDATE normalization_library SET resume_batch=0 WHERE id=1')
                            self.db().commit()
                        self.running = False
                        self.progress['current'] = None
                        break
        except Exception:
            self.app.logger.exception('Audio analysis batch failed')
            with self.guard:
                self.running = False
                self.progress['current'] = None


def install_normalization(app, db, lock, path, admin, body, fail):
    service = Normalization(app, db, lock, path)
    app.extensions['normalization'] = service

    @app.route(PREFIX, methods=['GET', 'PUT'])
    def normalization_preferences():
        if request.method == 'PUT':
            data = body()
            if (type(data.get('enabled')) is not bool or type(data.get('target')) is not int
                    or data['target'] not in (-20, -18, -16, -14) or type(data.get('peak')) is not int
                    or data['peak'] not in (-3, -2, -1)):
                fail('請選擇有效的音量正規化設定')
            if data['enabled'] and (not service.ffmpeg or not app.config['SETTINGS'].get('hls', {}).get('enabled', True)):
                fail('音量正規化需要啟用 HLS 與 FFmpeg。', 503)
            with lock:
                db().execute('INSERT OR REPLACE INTO normalization_preferences VALUES(?,?,?,?)',
                             (g.user['id'], int(data['enabled']), data['target'], data['peak']))
                db().commit()
        return service.preferences(g.user['id'])

    @app.route(PREFIX + '/library', methods=['GET', 'PUT', 'POST', 'DELETE'])
    def normalization_library():
        admin()
        if request.method == 'PUT':
            enabled = body().get('auto_analyze')
            if type(enabled) is not bool:
                fail('auto_analyze 必須為布林值')
            with lock:
                db().execute('UPDATE normalization_library SET auto_analyze=? WHERE id=1', (int(enabled),))
                db().commit()
            if enabled:
                service.start(automatic=True)
        elif request.method == 'POST':
            if not service.ffmpeg:
                fail('找不到 FFmpeg，無法分析音量。', 503)
            service.start()
        elif request.method == 'DELETE':
            service.cancel.set()
            with lock:
                db().execute('UPDATE normalization_library SET resume_batch=0 WHERE id=1')
                db().commit()
        return service.status()

    return service
