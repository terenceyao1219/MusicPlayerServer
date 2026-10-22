"""Run inside the built production image, using fixtures made by a full FFmpeg."""
import io
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, '/app')
from backend.app import create_app

assert os.getuid() == 10001
assert importlib.util.find_spec("pytest") is None
assert not Path('/usr/local/bin/ffprobe').exists()
assert not Path('/usr/bin/gcc').exists()
subprocess.run(['ffmpeg', '-version'], check=True, stdout=subprocess.DEVNULL)
root = Path(tempfile.mkdtemp(prefix='image-smoke-'))
results = Path('/tmp/image-results')
results.mkdir(exist_ok=True)
app = create_app(overrides={'storage': {'database': str(root / 'music.sqlite3'), 'music': str(root / 'music')},
                            'hls': {'cache': str(root / 'cache')}})
client = app.test_client()
prefix = '/api/v1/mplayer'
login = client.post(prefix + '/login', json={'username': 'super', 'password': 'admin'})
assert login.status_code == 200, login.json
headers = {'Authorization': 'Bearer ' + login.json['access_token']}
folder = client.post(prefix + '/folders', headers=headers, json={'name': 'Formats'}).json['id']
try:
    for source in sorted(Path('/tmp/image-fixtures').iterdir()):
        response = client.post(prefix + f'/folders/{folder}/tracks', headers=headers,
                               data={'files': (io.BytesIO(source.read_bytes()), source.name)})
        assert response.status_code == 201, (source.name, response.json)
        ident = response.json[0]['id']
        assert client.get(prefix + f'/tracks/{ident}/stream').data == source.read_bytes()
        response = client.post(prefix + '/hls/sessions', headers=headers, json={'track_id': ident, 'mode': 'single'})
        assert response.status_code == 202, response.json
        sid = response.json['id']
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            state = client.get(prefix + '/hls/sessions/' + sid, headers=headers).json
            assert not state['error'], (source.name, state)
            if state['ready']:
                break
            time.sleep(.05)
        else:
            raise AssertionError('HLS preparation timed out: ' + source.name)
        playlist = client.get(state['playlist_url'])
        assert playlist.status_code == 200
        assert '#EXT-X-DATERANGE:' in playlist.text
        segment = next(line for line in playlist.text.splitlines() if line and not line.startswith('#'))
        response = client.get(state['playlist_url'].rsplit('/', 1)[0] + '/' + segment)
        assert response.status_code == 200
        assert response.data[0] == 0x47 and len(response.data) > 188
        (results / (source.name + '.ts')).write_bytes(response.data)
        client.delete(prefix + '/hls/sessions/' + sid, headers=headers)
        print('PASS direct + HLS:', source.name, flush=True)
finally:
    app.extensions['hls']['sessions'].clear()
    app.extensions['hls']['executor'].shutdown(wait=True)
assert client.get('/health').json == {'status': 'ok'}
print('PASS non-root, SQLite initialization, password login, media auth and HLS')
