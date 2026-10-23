import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from backend.app import create_app
from backend.library_watch import LibraryWatch
from test_api import setup, folder, upload, P


def test_playback_configuration_and_title(setup):
    app, client, headers, root = setup
    assert client.get(P + '/playback/config', headers=headers).json == {'hls_enabled': True}
    app.config['SETTINGS']['hls']['enabled'] = False
    assert client.get(P + '/playback/config', headers=headers).json == {'hls_enabled': False}
    ident = upload(client, headers, folder(client, headers), 'original.WAV')
    assert client.patch(P + f'/tracks/{ident}', headers=headers, json={'title': 'new.song'}).status_code == 200
    assert (root / 'music/Jazz/new.song.WAV').exists()
    assert client.patch(P + f'/tracks/{ident}', headers=headers, json={'title': '../bad'}).status_code == 400


def test_library_settings_access_and_validation(setup):
    app, client, headers, root = setup
    assert client.get(P + '/library/settings').status_code == 401
    assert client.get(P + '/library/settings', headers=headers).json['auto_scan'] is False
    assert client.put(P + '/library/settings', headers=headers, json={'auto_scan': 'true'}).status_code == 400
    client.post(P + '/users', headers=headers, json={'username': 'listener', 'password': 'password1', 'role': 'user'})
    login = client.post(P + '/login', json={'username': 'listener', 'password': 'password1'})
    user_headers = {'Authorization': 'Bearer ' + login.json['access_token']}
    for method in ['get', 'put']:
        assert getattr(client, method)(P + '/library/settings', headers=user_headers, json={'auto_scan': True}).status_code == 403


def wait_for(predicate):
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.05)
    assert predicate()


@pytest.mark.skipif(not sys.platform.startswith('linux'), reason='requires real Linux inotify')
def test_inotify_import_rename_delete_disable_and_restart(setup):
    app, client, headers, root = setup
    music = root / 'music'
    def names():
        return [t['name'] for t in client.get(P + '/tracks', headers=headers).json]
    try:
        assert client.put(P + '/library/settings', headers=headers, json={'auto_scan': True}).json['running']
        (music / 'External').mkdir()
        (music / 'External/song.mp3').write_bytes(b'music')
        wait_for(lambda: names() == ['song.mp3'])
        (music / 'External/song.mp3').rename(music / 'External/new.mp3')
        wait_for(lambda: names() == ['new.mp3'])
        (music / 'External/new.mp3').unlink()
        wait_for(lambda: names() == [])
        assert not client.put(P + '/library/settings', headers=headers, json={'auto_scan': False}).json['running']
        (music / 'External/off.mp3').write_bytes(b'music')
        time.sleep(.7)
        assert names() == []
        client.put(P + '/library/settings', headers=headers, json={'auto_scan': True})
        wait_for(lambda: names() == ['off.mp3'])
        app.extensions['library_watch'].stop()
        (music / 'External/restart.mp3').write_bytes(b'music')
        restarted = create_app(overrides={'storage': {'database': str(root / 'db.sqlite3'), 'music': str(music)}})
        try:
            wait_for(lambda: names() == ['off.mp3', 'restart.mp3'])
            assert restarted.extensions['library_watch'].observer.is_alive()
        finally:
            restarted.extensions['library_watch'].stop()
    finally:
        app.extensions['library_watch'].stop()


def test_read_events_do_not_schedule_scans(tmp_path):
    watcher = LibraryWatch(None, tmp_path, None)
    for kind in ['opened', 'closed_no_write', 'modified', 'created']:
        watcher.changed(SimpleNamespace(event_type=kind, is_directory=False))
        assert not watcher.pending
    watcher.changed(SimpleNamespace(event_type='closed', is_directory=False))
    assert watcher.pending
