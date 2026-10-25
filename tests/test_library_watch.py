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


def test_library_revision_mutations_analysis_and_rollback(setup, monkeypatch):
    import sqlite3
    app, client, headers, root = setup
    def revision():
        response = client.get(P + '/library/revision', headers=headers)
        assert response.status_code == 200
        return response.json['revision']
    assert client.get(P + '/library/revision').status_code == 401
    before = revision()
    assert revision() == before
    folder_id = folder(client, headers)
    assert revision() != before
    before = revision()
    ident = upload(client, headers, folder_id)
    assert revision() != before
    before = revision()
    assert client.patch(P + f'/tracks/{ident}', headers=headers, json={'title': 'renamed'}).status_code == 200
    assert revision() != before
    service = app.extensions['normalization']
    fingerprint = service.fingerprint(root / 'music/Jazz/renamed.wav')
    before = revision()
    monkeypatch.setattr('backend.normalization.subprocess.run', lambda *a, **kw: SimpleNamespace(stderr=b'{"input_i":"-18","input_tp":"-2"}'))
    service.ffmpeg = 'ffmpeg'
    with app.app_context():
        service.analyze(root / 'music/Jazz/renamed.wav')
    assert revision() != before
    assert client.get(P + '/tracks', headers=headers).json[0]['analyzed'] is True
    before = revision()
    with sqlite3.connect(root / 'db.sqlite3') as connection:
        connection.execute("UPDATE tracks SET name='rollback.wav'")
        connection.rollback()
    assert revision() == before
    # A same-name external overwrite is still noticed when synchronization runs.
    (root / 'music/Jazz/renamed.wav').write_bytes(b'changed')
    assert client.post(P + '/library/sync', headers=headers).status_code == 200
    assert revision() != before
    assert client.get(P + '/tracks', headers=headers).json[0]['analyzed'] is False
    destination = folder(client, headers, 'Other')
    before = revision()
    assert client.post(P + '/tracks/move', headers=headers, json={'track_ids': [ident], 'folder_id': destination}).status_code == 200
    assert revision() != before
    before = revision()
    assert client.post(P + '/tracks/delete', headers=headers, json={'track_ids': [ident]}).status_code == 200
    assert revision() != before


def test_tracks_uses_constant_number_of_queries(setup, monkeypatch):
    import sqlite3
    app, client, headers, root = setup
    folder_id = folder(client, headers)
    for index in range(6):
        upload(client, headers, folder_id, f'{index}.wav')
    statements = []
    connect = sqlite3.connect
    def traced(*args, **kwargs):
        connection = connect(*args, **kwargs)
        connection.set_trace_callback(statements.append)
        return connection
    monkeypatch.setattr(sqlite3, 'connect', traced)
    assert len(client.get(P + '/tracks', headers=headers).json) == 6
    selects = [s for s in statements if s.lstrip().upper().startswith('SELECT')]
    assert len(selects) == 3  # Authentication, joined tracks, all measurements.

    statements.clear()
    assert client.get(P + '/library/revision', headers=headers).status_code == 200
    selects = [s for s in statements if s.lstrip().upper().startswith('SELECT')]
    assert len(selects) == 1  # Only authentication; revision lives in memory.


def test_revision_permissions_failures_and_restart(setup):
    from test_api import user
    app, client, headers, root = setup
    folder_id = folder(client, headers)
    user_id, listener, auth = user(app, client, headers)
    def revision(c=client, h=headers):
        return c.get(P + '/library/revision', headers=h).json['revision']
    before = revision()
    assert client.patch(P + f'/folders/{folder_id}', headers=headers, json={'name': '../invalid'}).status_code == 400
    assert revision() == before
    assert client.put(P + f'/folders/{folder_id}/permissions', headers=headers, json={'user_ids': [user_id]}).status_code == 200
    assert revision(listener, auth) != before
    before = revision()
    assert client.put(P + f'/users/{user_id}/permissions', headers=headers, json={'folder_ids': []}).status_code == 200
    assert revision(listener, auth) != before
    before = revision()
    restarted = create_app(overrides={'storage': {'database': str(root / 'db.sqlite3'), 'music': str(root / 'music')}})
    assert revision(restarted.test_client(), headers) != before
