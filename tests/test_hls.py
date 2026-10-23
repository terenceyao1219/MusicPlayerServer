import io
import shutil
import subprocess
import time
import wave
from pathlib import Path

import pytest
from backend.app import create_app

P = '/api/v1/mplayer'


def audio_bytes(seconds=1):
    output = io.BytesIO()
    with wave.open(output, 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b'\x00\x00' * (8000 * seconds))
    return output.getvalue()


@pytest.fixture
def hls(tmp_path, request):
    if not shutil.which('ffmpeg'):
        pytest.skip('real FFmpeg required')
    app = create_app(overrides={'storage': {'database': str(tmp_path / 'db.sqlite3'), 'music': str(tmp_path / 'music')},
                                'hls': {'cache': str(tmp_path / 'cache'), 'enabled': True, 'ahead_seconds': 0, **getattr(request, 'param', {})}})
    app.config['TESTING'] = True
    client = app.test_client()
    login = client.post(P + '/login', json={'username': 'super', 'password': 'admin'}).json
    headers = {'Authorization': 'Bearer ' + login['access_token']}
    tracks = []
    folders = []
    for name in ['A', 'B']:
        folder = client.post(P + '/folders', headers=headers, json={'name': name}).json['id']
        folders.append(folder)
        track = client.post(P + f'/folders/{folder}/tracks', headers=headers,
                            data={'files': (io.BytesIO(audio_bytes()), name + '.wav')}).json[0]['id']
        tracks.append(track)
    yield app, client, headers, tracks, folders, tmp_path
    app.extensions['hls']['sessions'].clear()
    app.extensions['hls']['executor'].shutdown(wait=True)


def wait(client, headers, sid, count=3):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        response = client.get(P + '/hls/sessions/' + sid, headers=headers)
        assert response.status_code == 200, response.json
        assert response.json['error'] is None, response.json
        if len(response.json['entries']) >= count:
            return response.json
        time.sleep(.05)
    pytest.fail('HLS did not prepare in time')


def start(client, headers, track, mode='global', position=0):
    response = client.post(P + '/hls/sessions', headers=headers,
                           json={'track_id': track, 'mode': mode, 'position': position})
    assert response.status_code == 202, response.json
    return wait(client, headers, response.json['id'])


def test_continuous_playlist_real_aac_and_server_side_extension(hls):
    app, client, headers, tracks, _, root = hls
    state = start(client, headers, tracks[0], position=.4)
    assert [e['track_id'] for e in state['entries']] == [tracks[0], tracks[1], tracks[0]]
    assert state['position'] == .4
    manifest = client.get(state['playlist_url'])  # native audio cookie only
    assert manifest.status_code == 200
    assert manifest.headers['Cache-Control'] == 'no-store'
    text = manifest.text
    assert '#EXT-X-DATERANGE:' in text and '#EXT-X-DISCONTINUITY' in text
    assert '#EXT-X-ENDLIST' not in text
    assert '#EXT-X-START:TIME-OFFSET=0.400000,PRECISE=YES' in text
    # Download the first prepared playlist and decode across its discontinuities.
    lines = []
    for line in text.splitlines():
        if line and not line.startswith('#'):
            response = client.get(state['playlist_url'].rsplit('/', 1)[0] + '/' + line)
            assert response.status_code == 200
            file = root / line.replace('/', '-')
            file.write_bytes(response.data)
            lines.append(str(file))
        else:
            lines.append(line)
    playlist = root / 'decode.m3u8'
    playlist.write_text('\n'.join(lines) + '\n#EXT-X-ENDLIST\n')
    decoded = subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(playlist), '-f', 'null', '-'],
                             capture_output=True, timeout=20)
    assert decoded.returncode == 0, decoded.stderr.decode()
    # Segment requests alone replenish the queue, without a browser resolve call.
    updated = wait(client, headers, state['id'], count=5)
    assert len(updated['entries']) >= 5
    assert len(list((root / 'cache').glob('*/index.m3u8'))) == 2
    assert all(updated['entries'][i]['start'] < updated['entries'][i+1]['start'] for i in range(4))


def test_sessions_are_independent_owned_and_revocable(hls):
    app, client, headers, tracks, folders, _ = hls
    first = start(client, headers, tracks[0], 'single')
    second = start(client, headers, tracks[1], 'single')
    assert first['id'] != second['id']
    assert all(e['track_id'] == tracks[0] for e in first['entries'])
    assert all(e['track_id'] == tracks[1] for e in second['entries'])
    uid = client.post(P + '/users', headers=headers, json={'username': 'listener', 'password': 'password123'}).json['id']
    other = app.test_client()
    token = other.post(P + '/login', json={'username': 'listener', 'password': 'password123'}).json['access_token']
    auth = {'Authorization': 'Bearer ' + token}
    assert other.get(first['playlist_url']).status_code == 404
    assert other.get(P + '/hls/sessions/' + first['id'], headers=auth).status_code == 404
    assert other.post(P + '/hls/sessions', headers=auth, json={'track_id': tracks[0]}).status_code == 403
    client.put(P + f'/users/{uid}/permissions', headers=headers, json={'folder_ids': [folders[0]]})
    owned = start(other, auth, tracks[0], 'folder')
    segment = owned['playlist_url'].rsplit('/', 1)[0] + '/0/0.ts'
    assert other.get(segment).status_code == 200
    client.put(P + f'/users/{uid}/permissions', headers=headers, json={'folder_ids': []})
    assert other.get(segment).status_code == 403
    assert other.get(owned['playlist_url']).status_code == 403
    assert client.delete(P + '/hls/sessions/' + first['id'], headers=headers).status_code == 200
    assert client.get(first['playlist_url']).status_code == 404
    assert client.get(second['playlist_url']).status_code == 200
    client.post(P + '/logout', headers=headers)
    assert client.get(second['playlist_url']).status_code == 401


def test_random_and_expiry_validation(hls):
    app, client, headers, tracks, _, _ = hls
    state = start(client, headers, tracks[0], 'random', position=999)
    ids = [entry['track_id'] for entry in state['entries']]
    assert ids[0] != ids[1] and ids[1] != ids[2]
    assert state['position'] < state['entries'][0]['duration']
    assert client.post(P + '/hls/sessions', headers=headers, json={'track_id': None}).status_code == 400
    assert client.post(P + '/hls/sessions', headers=headers, json={'track_id': tracks[0], 'mode': 'bad'}).status_code == 400
    app.extensions['hls']['sessions'][state['id']]['touched'] = 0
    assert client.get(state['playlist_url']).status_code == 404


def test_missing_ffmpeg_does_not_break_direct_playback(tmp_path):
    app = create_app(overrides={'storage': {'database': str(tmp_path / 'db'), 'music': str(tmp_path / 'music')},
                                'hls': {'ffmpeg': '/missing/ffmpeg', 'cache': str(tmp_path / 'cache')}})
    client = app.test_client()
    token = client.post(P + '/login', json={'username': 'super', 'password': 'admin'}).json['access_token']
    auth = {'Authorization': 'Bearer ' + token}
    assert client.get(P + '/hls/status', headers=auth).json['enabled'] is False
    assert client.post(P + '/hls/sessions', headers=auth, json={}).status_code == 503
    folder = client.post(P + '/folders', headers=auth, json={'name': 'A'}).json['id']
    ident = client.post(P + f'/folders/{folder}/tracks', headers=auth,
                        data={'files': (io.BytesIO(audio_bytes()), 'a.wav')}).json[0]['id']
    assert client.get(P + f'/tracks/{ident}/stream').data == audio_bytes()
    app.extensions['hls']['executor'].shutdown()


def test_selected_song_is_always_first_even_with_warm_cache(hls):
    _, client, headers, tracks, _, _ = hls
    start(client, headers, tracks[0])
    selected = start(client, headers, tracks[1], position=0)
    assert selected['entries'][0]['track_id'] == tracks[1]
    manifest = client.get(selected['playlist_url']).text
    assert '#EXT-X-START:TIME-OFFSET=0.000000,PRECISE=YES' in manifest
    first_marker = next(line for line in manifest.splitlines() if line.startswith('#EXT-X-DATERANGE:'))
    assert f'X-TRACK-ID="{tracks[1]}"' in first_marker


@pytest.mark.parametrize('hls', [{'ahead_seconds': 6}], indirect=True)
def test_time_based_prefetch_replenishes_without_frontend_polling(hls):
    app, client, headers, tracks, _, _ = hls
    state = start(client, headers, tracks[0])
    def prepared(seconds):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            session = app.extensions['hls']['sessions'][state['id']]
            if session['duration'] - session['requested_time'] >= seconds:
                return session
            time.sleep(.05)
        pytest.fail('time-based queue was not replenished')
    session = prepared(6)
    assert len(session['entries']) > 3
    response = client.get(state['playlist_url'].rsplit('/', 1)[0] + '/2/0.ts')
    assert response.status_code == 200
    session = prepared(6)
    assert session['requested_time'] > 3
    diagnostic = client.get(P + '/hls/sessions/' + state['id'], headers=headers).json['diagnostics']
    assert diagnostic['ahead_seconds'] >= 6


def test_event_playlist_grows_without_segment_requests_or_metadata_polling(hls, monkeypatch):
    from types import SimpleNamespace
    import backend.hls as module

    app, client, headers, tracks, _, _ = hls
    state = start(client, headers, tracks[0])
    session = app.extensions['hls']['sessions'][state['id']]
    clock = [100.]
    monkeypatch.setattr(module, 'time', SimpleNamespace(time=time.time, monotonic=lambda: clock[0]))
    first = client.get(state['playlist_url']).text
    # Wait for the encoder, without fetching metadata or audio segments.
    app.extensions['hls']['executor'].submit(lambda: None).result(timeout=20)
    assert client.get(state['playlist_url']).text == first
    previous = first
    for elapsed in (7, 14, 21):
        clock[0] = 100. + elapsed
        current = client.get(state['playlist_url']).text
        assert current.startswith(previous)  # EVENT history is immutable
        assert current.count('#EXTINF:') > previous.count('#EXTINF:')
        assert '#EXT-X-ENDLIST' not in current
        previous = current
    assert session['requested_time'] == 0
    assert session['published_time'] > session['publication_base']
    # A terminal stream must still publish its entire prepared tail.
    app.extensions['hls']['executor'].submit(lambda: None).result(timeout=20)
    session['finished'] = True
    assert '#EXT-X-ENDLIST' not in client.get(state['playlist_url']).text
    clock[0] += 1000
    final = client.get(state['playlist_url']).text
    assert final.startswith(previous)
    assert final.endswith('#EXT-X-ENDLIST\n')
