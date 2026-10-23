import shutil
import subprocess

import pytest

from backend.app import create_app

P = '/api/v1/mplayer'


def test_quick_link_lifecycle_and_real_hls(tmp_path):
    if not shutil.which('ffmpeg'):
        pytest.skip('real FFmpeg required')
    overrides = {'storage': {'database': str(tmp_path/'db.sqlite3'), 'music': str(tmp_path/'music')}}
    app = create_app(overrides=overrides)
    client = app.test_client()
    public = app.test_client()
    login = client.post(P+'/login', json={'username':'super','password':'admin'}).json
    headers = {'Authorization':'Bearer '+login['access_token']}
    assert public.put(P+'/quick-test', json={'enabled':True}).status_code == 401
    assert client.get(P+'/quick-test', headers=headers).json == {'enabled':False,'url':None}
    assert client.put(P+'/quick-test', headers=headers, json={'enabled':'yes'}).status_code == 400
    state = client.put(P+'/quick-test', headers=headers, json={'enabled':True}).json
    url = state['url']
    assert url.endswith('/index.m3u8')
    base_url = url.rsplit('/', 1)[0] + '/'
    assert public.get(base_url).status_code == 404
    assert public.get('/assets/quick-test.html').status_code == 404
    assert public.get('/assets/quick-test.js').status_code == 404
    assert state['enabled']
    assert client.put(P+'/quick-test', headers=headers, json={'enabled':True}).json['url'] == url
    response = public.get(url)
    assert response.status_code == 200
    assert response.headers['Cache-Control'] == 'no-store'
    manifest = public.get(url)
    text = manifest.text
    assert manifest.status_code == 200 and '#EXT-X-ENDLIST' not in text
    segments = [line for line in text.splitlines() if line.endswith('.ts')]
    assert len(segments) == 4
    media = public.get(base_url+segments[0])
    assert media.status_code == 200 and media.data[0] == 0x47
    assert media.headers['Cache-Control'] == 'no-store'
    probe = subprocess.run(['ffmpeg','-v','error','-i',str(tmp_path/'quick-test-audio-v1/tone0.ts'),
                            '-f','null','-'],capture_output=True)
    assert probe.returncode == 0, probe.stderr
    assert public.get('/quick-test/'+'x'*43+'/').status_code == 404
    # A test link does not grant access to the normal authenticated API.
    assert public.get(P+'/tracks', headers={'Authorization':'Bearer '+url.split('/')[-2]}).status_code == 401
    new = client.post(P+'/users', headers=headers, json={'username':'alice','password':'password123','role':'user'}).json
    other = app.test_client()
    other_token = other.post(P+'/login',json={'username':'alice','password':'password123'}).json['access_token']
    other_headers = {'Authorization':'Bearer '+other_token}
    assert other.get(P+'/quick-test',headers=other_headers).json['enabled'] is False
    other_url = other.put(P+'/quick-test',headers=other_headers,json={'enabled':True}).json['url']
    assert other_url != url
    # The enabled link survives a new application instance.
    restarted = create_app(overrides=overrides).test_client()
    assert restarted.get(url).status_code == 200
    assert restarted.get(P+'/quick-test',headers=headers).json['url'] == url
    client.put(P+'/quick-test',headers=headers,json={'enabled':False})
    for target in [base_url, url, base_url+segments[0]]:
        assert public.get(target).status_code == 404
    assert public.get(other_url).status_code == 200
    assert create_app(overrides=overrides).test_client().get(P+'/quick-test',headers=headers).json['enabled'] is False
    replacement = client.put(P+'/quick-test',headers=headers,json={'enabled':True}).json['url']
    assert replacement != url
    assert public.get(url).status_code == 404
    client.delete(P+'/users/'+str(new['id']),headers=headers)
    assert public.get(other_url).status_code == 404


def test_missing_encoder_does_not_enable(tmp_path):
    app=create_app(overrides={'storage':{'database':str(tmp_path/'db'),'music':str(tmp_path/'music')},
                              'hls':{'ffmpeg':'sonora-nonexistent-encoder'}})
    client=app.test_client()
    token=client.post(P+'/login',json={'username':'super','password':'admin'}).json['access_token']
    headers={'Authorization':'Bearer '+token}
    assert client.put(P+'/quick-test',headers=headers,json={'enabled':True}).status_code == 503
    assert client.get(P+'/quick-test',headers=headers).json['enabled'] is False
