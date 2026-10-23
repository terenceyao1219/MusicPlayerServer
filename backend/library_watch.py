"""Linux inotify library synchronization. No filesystem polling fallback."""
import atexit
import sys
import threading
import time

PREFIX = '/api/v1/mplayer'


class LibraryWatch:
    def __init__(self, app, music, reconcile):
        self.app, self.music, self.reconcile = app, music, reconcile
        self.observer = None
        self.condition = threading.Condition()
        self.worker = None
        self.pending = False
        self.stopped = True
        self.error = None

    @property
    def supported(self):
        return sys.platform.startswith('linux')

    def changed(self, event):
        # Reading/scanning files must not trigger another scan. Wait for writers
        # to close files; directory creation and moves also cover imported trees.
        if event.event_type not in {'closed', 'deleted', 'moved'} and not (
                event.is_directory and event.event_type == 'created'):
            return
        with self.condition:
            self.pending = True
            self.deadline = time.monotonic() + .5
            self.condition.notify()

    def start(self):
        if self.observer is not None:
            return
        if not self.supported:
            raise RuntimeError('自動掃描需要 Linux，這台伺服器僅支援手動同步。')
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers.inotify import InotifyObserver
        handler = FileSystemEventHandler()
        handler.on_any_event = self.changed
        observer = InotifyObserver()
        observer.schedule(handler, str(self.music), recursive=True)
        try:
            observer.start()
        except Exception:
            observer.stop()
            if observer.is_alive():
                observer.join()
            raise
        self.observer = observer
        self.stopped = False
        self.error = None
        # Watch first, then reconcile changes made while the server was stopped.
        self.pending = True
        self.deadline = time.monotonic()
        self.worker = threading.Thread(target=self.run, daemon=True, name='library-sync')
        self.worker.start()

    def stop(self):
        with self.condition:
            self.stopped = True
            self.condition.notify()
        if self.observer is not None:
            self.observer.stop()
            self.observer.join()
            self.observer = None
        if self.worker is not None:
            self.worker.join()
            self.worker = None

    def run(self):
        while True:
            with self.condition:
                while not self.stopped:
                    if not self.pending:
                        self.condition.wait()
                    else:
                        remaining = self.deadline - time.monotonic()
                        if remaining <= 0:
                            break
                        self.condition.wait(remaining)
                if self.stopped:
                    return
                self.pending = False
            try:
                with self.app.app_context():
                    self.reconcile()
                self.error = None
            except Exception:
                self.app.logger.exception('Automatic library synchronization failed')
                self.error = '自動同步失敗，請確認音樂目錄可存取，再手動同步。'


def install_library_watch(app, db, lock, admin, body, fail, music, reconcile):
    watcher = LibraryWatch(app, music, reconcile)
    app.extensions['library_watch'] = watcher
    settings_lock = threading.Lock()
    with app.app_context():
        db().execute('CREATE TABLE IF NOT EXISTS library_settings (id INTEGER PRIMARY KEY CHECK(id=1), auto_scan INTEGER NOT NULL)')
        db().execute('INSERT OR IGNORE INTO library_settings VALUES(1,0)')
        db().commit()
        enabled = bool(db().execute('SELECT auto_scan FROM library_settings WHERE id=1').fetchone()[0])
    if enabled:
        try:
            watcher.start()
        except Exception:
            app.logger.exception('Unable to start inotify library watcher')
            watcher.error = '自動掃描未能啟動，請確認伺服器支援並重新啟用。'
    atexit.register(watcher.stop)

    @app.route(PREFIX + '/library/settings', methods=['GET', 'PUT'])
    def library_settings():
        from flask import request
        admin()
        with settings_lock:
            if request.method == 'PUT':
                enabled = body().get('auto_scan')
                if type(enabled) is not bool:
                    fail('auto_scan 必須為布林值')
                if enabled:
                    try:
                        watcher.start()
                    except Exception:
                        app.logger.exception('Unable to enable inotify')
                        fail('無法啟用自動掃描，請確認使用 Linux 且音樂目錄可監控。', 503)
                else:
                    watcher.stop()
                    watcher.error = None
                try:
                    with lock:
                        db().execute('UPDATE library_settings SET auto_scan=? WHERE id=1', (int(enabled),))
                        db().commit()
                except Exception:
                    watcher.stop()
                    raise
            enabled = bool(db().execute('SELECT auto_scan FROM library_settings WHERE id=1').fetchone()[0])
            return {'auto_scan': enabled, 'supported': watcher.supported,
                    'running': watcher.observer is not None and watcher.observer.is_alive(),
                    'error': watcher.error}
