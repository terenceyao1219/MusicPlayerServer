import base64
import io
import json
import shutil
import time
import wave
from urllib.parse import urljoin

import pytest
from backend.app import create_app

P = '/api/v1/mplayer'


def headers(row):
    value = base64.b64encode((row['username'] + ':' + row['password']).encode()).decode()
    return {'Authorization': 'Basic ' + value}


def settings(**overrides):
    return dict(name='通勤', scope='all', folder_ids=[], ordering='sequential',
                enabled=True, resume=True, background='#183e38', foreground='#f3d78c', **overrides)


@pytest.fixture
def radio(tmp_path):
    if not shutil.which('ffmpeg'):
        pytest.skip('FFmpeg required')
    options = {'storage': {'database': str(tmp_path/'db'), 'music': str(tmp_path/'music')}}
    app = create_app(overrides=options)
    app.config['TESTING'] = True
    app.config['RADIO_STARTUP_WAIT'] = 0
    client = app.test_client()
    token = client.post(P+'/login', json={'username':'super','password':'admin'}).json['access_token']
    auth = {'Authorization':'Bearer '+token}
    folder = client.post(P+'/folders', headers=auth, json={'name':'A'}).json['id']
    output = io.BytesIO()
    with wave.open(output,'wb') as w:
        w.setparams((1,2,8000,0,'NONE','not compressed'))
        w.writeframes(b'\0\0'*8000*9)
    tracks = []
    for i in range(3):
        result = client.post(P+f'/folders/{folder}/tracks',headers=auth,
                             data={'files':(io.BytesIO(output.getvalue()),f'{i}.wav')})
        tracks.append(result.json[0]['id'])
    yield app,client,auth,folder,tracks,options
    app.extensions['radios']['cache'].pool.shutdown(wait=True)
    app.extensions['hls']['executor'].shutdown(wait=True)


def create(client, auth, **overrides):
    data=settings()
    data.update(overrides)
    result=client.post(P+'/radios',headers=auth,json=data)
    assert result.status_code==201,result.json
    return result.json


def open_stream(app, row, client=None):
    client=client or app.test_client()
    response=client.get(row['stream_url'],headers=headers(row))
    assert response.status_code==200,response.text
    media=urljoin(row['stream_url'],response.text.splitlines()[-1])
    return client,media


def ready(client, row, media, count=1):
    deadline=time.monotonic()+20
    while time.monotonic()<deadline:
        response=client.get(media,headers=headers(row))
        if response.status_code==200:
            segments=[urljoin(media,s) for s in response.text.splitlines() if s.endswith('.ts')]
            if len(segments)>=count:
                return response,segments
        else:
            assert response.status_code==503,response.text
        time.sleep(.05)
    pytest.fail('radio audio did not prepare')


def test_management_auth_and_artwork(radio):
    app,c,a,folder,tracks,options=radio
    row=create(c,a)
    second=create(c,a,name='另一個電台')
    assert row['id']!=second['id']
    assert row['username']==second['username'] and row['password']==second['password']
    assert 'policy' not in row
    assert app.test_client().head(second['stream_url'],headers=headers(row)).status_code==200
    assert len(c.get(P+'/radios',headers=a).json)==2
    anonymous=app.test_client()
    assert anonymous.get(P+'/radios').status_code==401
    response=anonymous.get(row['stream_url'])
    assert response.status_code==401 and 'Basic' in response.headers['WWW-Authenticate']
    assert anonymous.get(P+'/tracks',headers=headers(row)).status_code==401
    assert anonymous.head(row['stream_url'],headers=headers(row)).status_code==200
    assert not app.extensions['radios']['active']
    png=anonymous.get(row['icon_url'])
    assert png.status_code==200 and png.data.startswith(b'\x89PNG\r\n\x1a\n')
    assert png.mimetype=='image/png'
    data=settings();data.update(name='新名稱',foreground='#ffffff')
    updated=c.put(P+'/radios/'+row['id'],headers=a,json=data).json
    assert updated['stream_url']==row['stream_url'] and updated['password']==row['password']
    assert anonymous.get(row['icon_url']).data!=png.data
    token=app.test_client().post(P+'/login',json={'username':'super','password':'admin'}).json['access_token']
    assert token
    c.post(P+'/users',headers=a,json={'username':'other','password':'password123','role':'user'})
    other=app.test_client()
    t=other.post(P+'/login',json={'username':'other','password':'password123'}).json['access_token']
    oh={'Authorization':'Bearer '+t}
    assert other.get(P+'/radios',headers=oh).json==[]
    assert other.delete(P+'/radios/'+row['id'],headers=oh).status_code==404
    assert other.post(P+'/radios/'+row['id']+'/credentials',headers=oh).status_code==404
    assert other.post(P+'/radios',headers=oh,json={**settings(),'scope':'folders','folder_ids':[folder]}).status_code==403


def test_new_connection_replaces_old_and_basic_on_every_request(radio):
    app,c,a,folder,tracks,options=radio
    row=create(c,a)
    listener,media=open_stream(app,row)
    manifest,segments=ready(listener,row,media)
    assert listener.get(media).status_code==401
    assert listener.get(segments[0]).status_code==401
    assert listener.get(segments[0],headers=headers(row)).status_code==200
    newcomer=app.test_client()
    again=listener.get(row['stream_url'],headers=headers(row))
    assert urljoin(row['stream_url'],again.text.splitlines()[-1])==media
    newcomer,newmedia=open_stream(app,row,newcomer)
    assert newmedia!=media
    assert listener.get(media,headers=headers(row)).status_code==410
    assert listener.get(segments[0],headers=headers(row)).status_code==410
    assert newcomer.get(newmedia,headers=headers(row)).status_code in (200,503)


def test_credential_rotation_disable_delete(radio):
    app,c,a,folder,tracks,options=radio
    row=create(c,a)
    listener,media=open_stream(app,row)
    _,segments=ready(listener,row,media)
    new=c.post(P+'/radios/'+row['id']+'/credentials',headers=a).json
    assert new['password']!=row['password']
    assert listener.get(row['stream_url'],headers=headers(row)).status_code==401
    assert listener.get(segments[0],headers=headers(new)).status_code==410
    c.post(P+'/radios/'+row['id']+'/toggle',headers=a,json={'enabled':False})
    assert listener.get(row['stream_url'],headers=headers(new)).status_code==403
    c.delete(P+'/radios/'+row['id'],headers=a)
    assert listener.get(row['stream_url'],headers=headers(new)).status_code==404
    assert listener.get(row['icon_url']).status_code==404


def test_revoked_folders_block_prepared_segments_and_new_selection(radio):
    app,c,a,folder,tracks,options=radio
    account=c.post(P+'/users',headers=a,json={'username':'alice','password':'password123','role':'user'}).json
    c.put(P+f"/users/{account['id']}/permissions",headers=a,json={'folder_ids':[folder]})
    owner=app.test_client()
    t=owner.post(P+'/login',json={'username':'alice','password':'password123'}).json['access_token']
    auth={'Authorization':'Bearer '+t}
    row=create(owner,auth,scope='folders',folder_ids=[folder])
    listener,media=open_stream(app,row)
    _,segments=ready(listener,row,media)
    assert listener.get(segments[0],headers=headers(row)).status_code==200
    c.put(P+f"/users/{account['id']}/permissions",headers=a,json={'folder_ids':[]})
    assert listener.get(segments[0],headers=headers(row)).status_code==403
    assert listener.get(media,headers=headers(row)).status_code==410
    assert listener.get(row['stream_url'],headers=headers(row)).status_code==404
    assert owner.post(P+'/radios/'+row['id']+'/toggle',headers=auth,json={'enabled':False}).status_code==200


def test_shuffle_rounds_and_persistent_resume(radio):
    app,c,a,folder,tracks,options=radio
    row=create(c,a,ordering='shuffle')
    listener,media=open_stream(app,row)
    ready(listener,row,media,count=5)
    session=app.extensions['radios']['active'][row['id']]
    first_round=[]
    for segment in session['segments']:
        if segment['offset']==0:
            first_round.append(segment['track_id'])
    assert len(first_round)>=3 and set(first_round[:3])==set(tracks)
    segments=session['segments']
    # Start at the first segment; advance the server's estimated playback clock.
    listener.get(urljoin(media,str(segments[0]['seq'])+'.ts'),headers=headers(row))
    session['origin']-=20
    for s in segments:
        listener.get(urljoin(media,str(s['seq'])+'.ts'),headers=headers(row))
    saved=c.get(P+'/radios',headers=a).json[0]['progress']
    assert saved['track_id'] in tracks and saved['offset']>=0
    # A new process reads both credentials and the conservative shuffle checkpoint.
    restarted=create_app(overrides=options)
    try:
        resumed_row=restarted.test_client().get(P+'/radios',headers=a).json[0]
        assert resumed_row['password']==row['password']
        assert resumed_row['progress']==saved
        new_listener,new_media=open_stream(restarted,resumed_row)
        ready(new_listener,resumed_row,new_media)
        resumed=restarted.extensions['radios']['active'][row['id']]
        assert resumed['segments'][0]['track_id']==saved['track_id']
        assert resumed['segments'][0]['state']['remaining']==saved['remaining']
    finally:
        restarted.extensions['radios']['cache'].pool.shutdown(wait=True)
        restarted.extensions['hls']['executor'].shutdown(wait=True)


def test_live_window_bounded_and_idle_release(radio):
    app,c,a,folder,tracks,options=radio
    row=create(c,a)
    listener,media=open_stream(app,row)
    _,segments=ready(listener,row,media,count=5)
    listener.get(segments[0],headers=headers(row))
    session=app.extensions['radios']['active'][row['id']]
    for elapsed in (20,40,60,80,100):
        session['origin']=time.monotonic()-elapsed
        ready(listener,row,media,count=5)
        assert len(session['segments'])<=16
        assert len({s['seq'] for s in session['segments']})==len(session['segments'])
    session['touch']-=46
    assert listener.get(media,headers=headers(row)).status_code==410
    assert app.test_client().get(row['stream_url'],headers=headers(row)).status_code==200



def test_account_password_rotation_covers_all_owned_stations(radio):
    app,c,a,folder,tracks,options=radio
    first=create(c,a)
    second=create(c,a,name='第二台')
    listener,media=open_stream(app,second)
    _,segments=ready(listener,second,media)
    listener.get(segments[0],headers=headers(second))
    renewed=c.post(P+'/radios/'+first['id']+'/credentials',headers=a).json
    current=c.get(P+'/radios',headers=a).json
    assert all(s['password']==renewed['password'] for s in current)
    assert all(s['username']==renewed['username'] for s in current)
    assert listener.get(second['stream_url'],headers=headers(second)).status_code==401
    assert listener.get(segments[0],headers=headers(renewed)).status_code==410
    assert listener.head(second['stream_url'],headers=headers(renewed)).status_code==200
    c.post(P+'/users',headers=a,json={'username':'bob','password':'password123','role':'user'})
    other=app.test_client()
    t=other.post(P+'/login',json={'username':'bob','password':'password123'}).json['access_token']
    another=create(other,{'Authorization':'Bearer '+t})
    assert another['username']!=renewed['username']
    assert other.head(first['stream_url'],headers=headers(another)).status_code==401


def test_shuffle_cold_retries_keep_first_song_and_prepare_next(radio,monkeypatch):
    app,c,a,folder,tracks,options=radio
    cache=app.extensions['radios']['cache']
    requested=[]
    original=cache.get
    monkeypatch.setattr(cache,'get',lambda source:requested.append(source) or None)
    row=create(c,a,ordering='shuffle')
    first=row['progress']['track_id']
    assert first in tracks
    for _ in range(5):
        listener,media=open_stream(app,row)
        session=app.extensions['radios']['active'][row['id']]
        assert session['state']['track_id']==first
        assert listener.get(media,headers=headers(row)).status_code==503
    assert len(set(requested))==1
    monkeypatch.setattr(cache,'get',original)
    _,segments=ready(listener,row,media,count=5)
    session=app.extensions['radios']['active'][row['id']]
    assert session['segments'][0]['track_id']==first
    assert session['segments'][0]['state']['seen']==[first]


def test_upgrade_creates_account_credentials_and_preserves_station_data(radio):
    import sqlite3
    app,c,a,folder,tracks,options=radio
    first=create(c,a,ordering='shuffle')
    second=create(c,a,name='第二台')
    app.extensions['radios']['cache'].pool.shutdown(wait=True)
    with sqlite3.connect(options['storage']['database']) as connection:
        connection.execute('DROP TABLE radio_accounts')
        connection.execute("UPDATE radios SET policy='reject'")
    upgraded=create_app(overrides=options)
    try:
        rows=upgraded.test_client().get(P+'/radios',headers=a).json
        assert {r['stream_url'] for r in rows}=={first['stream_url'],second['stream_url']}
        assert len({r['username'] for r in rows})==1
        assert len({r['password'] for r in rows})==1
        assert all('policy' not in r for r in rows)
        assert next(r for r in rows if r['id']==first['id'])['progress']==first['progress']
        with sqlite3.connect(options['storage']['database']) as connection:
            assert {r[0] for r in connection.execute('SELECT policy FROM radios')}=={'replace'}
    finally:
        upgraded.extensions['radios']['cache'].pool.shutdown(wait=True)
        upgraded.extensions['hls']['executor'].shutdown(wait=True)


def test_initial_playlist_waits_for_encoder_without_503(radio,monkeypatch):
    app,c,a,folder,tracks,options=radio
    app.config['RADIO_STARTUP_WAIT']=4
    row=create(c,a,ordering='shuffle')
    cache=app.extensions['radios']['cache']
    original=cache.get
    calls=[0]
    def delayed(source):
        calls[0]+=1
        return None if calls[0]<3 else original(source)
    monkeypatch.setattr(cache,'get',delayed)
    listener,media=open_stream(app,row)
    response=listener.get(media,headers=headers(row))
    assert response.status_code==200,response.text
    durations=[float(line.split(':')[1].rstrip(',')) for line in response.text.splitlines() if line.startswith('#EXTINF:')]
    assert sum(durations)>=21
