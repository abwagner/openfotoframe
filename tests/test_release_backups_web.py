"""Scoped browser exports retain API authorization, queueing and restore rules."""
import io
import re
from sqlalchemy import select, func
from release_backend import models as m
from release_backend.jobs import process_export
from tests.test_release_web import web, upload
from tests.test_release_backend import release, auth, user, invite, accept, collection


def csrf(client, headers):
    response = client.get('/backup', headers=headers)
    assert response.status_code == 200, response.get_data(as_text=True)
    return re.search(r'name="csrf_token" value="([^"]+)"', response.get_data(as_text=True))[1]


def queue(client, headers, key):
    response = client.post('/backup', headers=headers, data={'csrf_token': key, 'action': 'export'})
    assert response.status_code == 303


def test_queue_is_explicit_csrf_scoped_and_never_runs_inline(web, monkeypatch):
    app, owner, hid, cid, fid, headers = web
    import release_backend.jobs
    monkeypatch.setattr(release_backend.jobs, 'process_export', lambda *a, **kw: (_ for _ in ()).throw(AssertionError('inline worker')))
    client = app.test_client()
    assert client.get('/backup', headers=auth(owner)).status_code == 409
    key = csrf(client, headers)
    assert client.post('/backup', headers=headers, data={'action': 'export'}).status_code == 403
    queue(client, headers, key)
    with app.extensions['release_store'].transaction() as db:
        job = db.execute(select(m.jobs)).mappings().one()
        assert job['state'] == 'queued' and not job['result_key']
        jid = job['id']
    page = client.get('/backup', headers=headers).get_data(as_text=True)
    assert 'Waiting for the background worker' in page
    assert 'Download private archive' not in page
    assert client.get('/backup/jobs/' + jid + '/download', headers=headers).status_code == 409
    assert client.post('/backup/jobs/' + jid + '/delete', headers=headers).status_code == 403
    assert client.post('/backup/jobs/' + jid + '/delete', headers=headers, data={'csrf_token': key}).status_code == 303


def test_other_user_and_other_selected_collection_cannot_access_job(web):
    app, owner, hid, cid, fid, headers = web
    client = app.test_client(); key = csrf(client, headers); queue(client, headers, key)
    with app.extensions['release_store'].transaction() as db:
        jid = db.execute(select(m.jobs.c.id)).scalar_one()
    member = user(app)
    assert accept(app, member, invite(app, owner, hid, collections=[{'id': cid, 'role': 'editor'}])).status_code == 200
    member_headers = headers | auth(member)
    other_client = app.test_client(); member_key = csrf(other_client, member_headers)
    assert jid not in other_client.get('/backup', headers=member_headers).get_data(as_text=True)
    assert other_client.get('/backup/jobs/' + jid + '/download', headers=member_headers).status_code == 404
    assert other_client.post('/backup/jobs/' + jid + '/delete', headers=member_headers,
        data={'csrf_token': member_key}).status_code == 404
    other = collection(app, owner, hid, 'Other')
    assert client.get('/backup/jobs/' + jid + '/download',
        headers=headers | {'X-OpenFotoFrame-Collection': other}).status_code == 404
    assert other_client.post('/backup', headers=member_headers, data={'csrf_token': member_key,
        'action': 'restore', 'confirm_restore': 'yes', 'file': (io.BytesIO(b'fixture'), 'archive.zip')}).status_code == 403


def test_completed_export_download_and_multipart_restore_use_existing_scope(web):
    app, owner, hid, cid, fid, headers = web
    upload(app, headers)
    client = app.test_client(); key = csrf(client, headers); queue(client, headers, key)
    with app.extensions['release_store'].transaction() as db:
        job = db.execute(select(m.jobs)).mappings().one()
        jid, uid = job['id'], job['requested_by']
    assert process_export(app, jid, hid, uid)
    download = client.get('/backup/jobs/' + jid + '/download', headers=headers)
    assert download.status_code == 200 and download.mimetype == 'application/zip'
    payload = {'csrf_token': key, 'action': 'restore', 'confirm_restore': 'yes',
        'file': (io.BytesIO(download.data), 'collection.zip')}
    response = client.post('/backup', headers=headers, data=payload)
    assert response.status_code == 303
    response = client.get('/backup', headers=headers)
    assert b'Restored 1 photos' in response.data
    with app.extensions['release_store'].transaction() as db:
        assert db.execute(select(func.count()).select_from(m.assets).where(m.assets.c.collection_id == cid)).scalar_one() == 2
    assert client.post('/backup', headers=headers, data={'csrf_token': key, 'action': 'restore',
        'file': (io.BytesIO(download.data), 'collection.zip')}).status_code == 400
