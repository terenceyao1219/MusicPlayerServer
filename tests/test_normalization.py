import io
import json
import math
import shutil
import sqlite3
import struct
import subprocess
import threading
import time
import wave

import pytest
from backend.app import create_app
from backend.normalization import DEFAULT, Normalization

P = '/api/v1/mplayer'


@pytest.fixture
def env(tmp_path):
    if not shutil.which('ffmpeg'):
        pytest.skip('FFmpeg required')
    app = create_app(overrides={'storage': {'database': str(tmp_path/'db'), 'music': str(tmp_path/'music')},
                                'hls': {'cache': str(tmp_path/'cache'), 'ahead_seconds': 0}})
    app.config['TESTING'] = True
    client = app.test_client()
    auth = {'Authorization': 'Bearer '+client.post(P+'/login', json={'username':'super','password':'admin'}).json['access_token']}
    folder = client.post(P+'/folders', headers=auth, json={'name':'A'}).json['id']
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as w:
        w.setparams((1, 2, 48000, 0, 'NONE', 'not compressed'))
        w.writeframes(b''.join(struct.pack('<h', int(2000*math.sin(2*math.pi*440*i/48000))) for i in range(48000*4)))
    track = client.post(P+f'/folders/{folder}/tracks', headers=auth,
                        data={'files':(io.BytesIO(buffer.getvalue()), 'tone.wav')}).json[0]['id']
    yield app, client, auth, tmp_path, track
    service = app.extensions['normalization']
    service.cancel.set()
    if hasattr(service, 'worker'):
        service.worker.join(timeout=10)
    app.extensions['hls']['sessions'].clear()
    app.extensions['hls']['executor'].shutdown(wait=True)
    app.extensions['radios']['cache'].pool.shutdown(wait=True)


def wait_for(fn):
    end = time.monotonic()+20
    while time.monotonic()<end:
        value=fn()
        if value:
            return value
        time.sleep(.05)
    pytest.fail('background work did not finish')


def test_preferences_validation_and_admin_boundary(env):
    app,c,a,root,track=env
    assert c.get(P+'/normalization',headers=a).json == DEFAULT
    assert c.put(P+'/normalization',headers=a,json={'enabled':True,'target':-16,'peak':-2}).status_code==200
    for data in ({'enabled':1,'target':-16,'peak':-2}, {'enabled':True,'target':-9,'peak':-2},
                 {'enabled':True,'target':-16,'peak':False}):
        assert c.put(P+'/normalization',headers=a,json=data).status_code==400
    c.post(P+'/users',headers=a,json={'username':'listener','password':'password123','role':'user'})
    other=app.test_client()
    b={'Authorization':'Bearer '+other.post(P+'/login',json={'username':'listener','password':'password123'}).json['access_token']}
    assert other.get(P+'/normalization',headers=b).json==DEFAULT
    for method in ('GET','POST','PUT','DELETE'):
        assert other.open(P+'/normalization/library',method=method,headers=b,json={'auto_analyze':True}).status_code==403
    with sqlite3.connect(root/'db') as db:
        assert db.execute('SELECT target,peak FROM normalization_preferences WHERE user_id=1').fetchone()==(-16,-2)


def test_batch_icon_invalidation_and_reuse(env,monkeypatch):
    app,c,a,root,track=env
    assert not c.get(P+'/tracks',headers=a).json[0]['analyzed']
    assert c.post(P+'/normalization/library',headers=a,json={}).status_code==200
    wait_for(lambda:not c.get(P+'/normalization/library',headers=a).json['running'])
    assert c.get(P+'/tracks',headers=a).json[0]['analyzed']
    service=app.extensions['normalization']
    with app.app_context():
        source=root/'music/A/tone.wav'
        with service.work.slot():
            result=service.analyze(source)
        assert math.isfinite(result['loudness'])
        def unexpected(*args,**kwargs):
            raise AssertionError('cached analysis must not execute FFmpeg')
        monkeypatch.setattr('backend.normalization.subprocess.run',unexpected)
        with service.work.slot():
            assert service.analyze(source)['loudness']==result['loudness']
        source.write_bytes(source.read_bytes()+b'changed')
    assert not c.get(P+'/tracks',headers=a).json[0]['analyzed']


def test_fixed_gain_peak_cap_and_disabled_path(env,monkeypatch):
    app,c,a,root,track=env
    service=app.extensions['normalization']
    source=root/'music/A/tone.wav'
    monkeypatch.setattr(service,'analyze',lambda source:{'loudness':-25.,'peak':-3.})
    assert service.filters(source,{'enabled':True,'target':-16,'peak':-2})[-1].endswith('volume=1.000000dB')
    assert service.filters(source,DEFAULT)==[]
    monkeypatch.setattr(service,'analyze',lambda source:{'loudness':None,'peak':None})
    assert service.filters(source,{'enabled':True,'target':-16,'peak':-2})[-1].endswith('volume=0.000000dB')
    assert Normalization.suffix(DEFAULT)==''
    assert Normalization.suffix({'enabled':True,'target':-16,'peak':-2})!=Normalization.suffix({'enabled':True,'target':-18,'peak':-2})


def test_normalized_hls_real_output_and_cache_isolation(env):
    app,c,a,root,track=env
    def start():
        r=c.post(P+'/hls/sessions',headers=a,json={'track_id':track,'mode':'single','position':0})
        assert r.status_code==202
        sid=r.json['id']
        def ready():
            state=c.get(P+'/hls/sessions/'+sid,headers=a).json
            assert not state['error']
            return state if state['ready'] else None
        return sid,wait_for(ready)
    sid,_=start()
    assert not c.get(P+'/tracks',headers=a).json[0]['analyzed']
    c.put(P+'/normalization',headers=a,json={'enabled':True,'target':-16,'peak':-2})
    normalized,_=start()
    sessions=app.extensions['hls']['sessions']
    old=sessions[sid]['entries'][0]['key']
    new=sessions[normalized]['entries'][0]['key']
    assert old!=new
    assert sessions[sid]['normalization']['enabled'] is False
    assert c.get(P+'/tracks',headers=a).json[0]['analyzed']
    result=subprocess.run(['ffmpeg','-hide_banner','-i',str(root/'cache'/new/'index.m3u8'),
        '-af','loudnorm=print_format=json','-f','null','-'],capture_output=True,check=True,timeout=20)
    output=result.stderr.decode()
    data=json.loads(output[output.rfind('{'):output.rfind('}')+1])
    assert abs(float(data['input_i'])-(-16))<.7
    assert float(data['input_tp'])<-1.5


def test_auto_analysis_on_library_sync_and_cancel(env,monkeypatch):
    app,c,a,root,track=env
    service=app.extensions['normalization']
    calls=[]
    monkeypatch.setattr(service,'start',lambda automatic=False:calls.append(automatic))
    assert c.put(P+'/normalization/library',headers=a,json={'auto_analyze':True}).status_code==200
    with app.app_context():
        service.automatic()
    assert calls==[True,True]
    c.put(P+'/normalization/library',headers=a,json={'auto_analyze':False})
    with app.app_context():
        service.automatic()
    assert calls==[True,True]
    assert c.delete(P+'/normalization/library',headers=a).status_code==200
    assert service.cancel.is_set()


def test_inotify_new_file_analysis(env):
    import sys
    if not sys.platform.startswith('linux'):
        pytest.skip('Linux inotify required')
    app,c,a,root,track=env
    watcher=app.extensions['library_watch']
    try:
        assert c.put(P+'/library/settings',headers=a,json={'auto_scan':True}).status_code==200
        assert c.put(P+'/normalization/library',headers=a,json={'auto_analyze':True}).status_code==200
        wait_for(lambda:not c.get(P+'/normalization/library',headers=a).json['running'])
        (root/'music/A/new.wav').write_bytes((root/'music/A/tone.wav').read_bytes())
        wait_for(lambda:any(t['name']=='new.wav' and t['analyzed'] for t in c.get(P+'/tracks',headers=a).json))
    finally:
        watcher.stop()


def test_radio_uses_owner_preference_and_snapshot(env):
    app,c,a,root,track=env
    from test_radios import create,open_stream,ready
    enabled={'enabled':True,'target':-18,'peak':-3}
    c.put(P+'/normalization',headers=a,json=enabled)
    row=create(c,a)
    listener,media=open_stream(app,row)
    _,segments=ready(listener,row,media)
    session=app.extensions['radios']['active'][row['id']]
    assert session['normalization']==enabled
    cache=app.extensions['radios']['cache']
    assert session['segments'][0]['key']==cache.key(root/'music/A/tone.wav',enabled)
    c.put(P+'/normalization',headers=a,json=DEFAULT)
    from test_radios import headers
    assert listener.get(segments[0],headers=headers(row)).status_code==200
    assert session['normalization']==enabled
    open_stream(app,row,listener)
    replacement=app.extensions['radios']['active'][row['id']]
    assert replacement['id']!=session['id']
    assert replacement['normalization']==DEFAULT


def test_resume_batch_after_restart_preserves_account_settings(env):
    app,c,a,root,track=env
    selected={'enabled':True,'target':-20,'peak':-3}
    c.put(P+'/normalization',headers=a,json=selected)
    with sqlite3.connect(root/'db') as db:
        db.execute('UPDATE normalization_library SET resume_batch=1 WHERE id=1')
    restarted=create_app(overrides={'storage':{'database':str(root/'db'),'music':str(root/'music')},
                                    'hls':{'cache':str(root/'cache')}})
    service=restarted.extensions['normalization']
    try:
        wait_for(lambda:not service.running)
        with restarted.app_context():
            assert service.preferences(1)==selected
            assert service.ready(root/'music/A/tone.wav')
    finally:
        service.cancel.set()
        if hasattr(service,'worker'):
            service.worker.join(timeout=10)
        restarted.extensions['hls']['executor'].shutdown(wait=True)
        restarted.extensions['radios']['cache'].pool.shutdown(wait=True)


def test_foreground_audio_has_priority_over_background(env):
    app,*_=env
    work=app.extensions['normalization'].work
    order=[]
    def job(label,background):
        with work.slot(background=background):
            order.append(label)
    with work.slot():
        background=threading.Thread(target=job,args=('background',True))
        foreground=threading.Thread(target=job,args=('foreground',False))
        background.start()
        foreground.start()
        wait_for(lambda:work.waiting==1)
    background.join(timeout=2)
    foreground.join(timeout=2)
    assert order==['foreground','background']
