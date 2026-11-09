"""Permanent removal is explicit, scoped and visibly asynchronous."""
from tests.test_release_backend import release, auth, user, invite, accept
from tests.test_release_web import web, upload
from tests.test_release_accounts_web import browser


def test_deleted_photo_restore_and_purge_require_scope_csrf_and_explicit_confirmation(web):
    app, owner, hid, cid, fid, headers = web
    aid = upload(app, headers)
    client, csrf = browser(app, owner, hid, cid, fid)
    path = '/trash?household_id=' + hid + '&collection_id=' + cid
    form = {'household_id': hid, 'collection_id': cid, 'asset_id': aid, 'action': 'restore'}
    assert client.delete('/api/gallery/' + aid, headers=headers).status_code == 200
    assert client.get(path).status_code == 200
    assert client.post('/trash', data=form).status_code == 403
    assert client.post('/trash', data=form | {'csrf_token': csrf}).status_code == 303
    assert len(client.get('/api/gallery', headers=headers).json['images']) == 1
    assert client.delete('/api/gallery/' + aid, headers=headers).status_code == 200
    form.update(action='purge', csrf_token=csrf)
    assert client.post('/trash', data=form).status_code == 400
    assert client.post('/trash', data=form | {'confirm_permanent_delete': 'yes'}).status_code == 303
    page = client.get(path)
    assert 'Permanent removal: queued' in page.text
    assert 'Storage is freed only after the background deletion finishes successfully' in page.text
    assert '<button>Restore photo</button>' not in page.text
    retained = client.get('/api/v1/households/' + hid + '/storage', headers=auth(owner)).json['usage']['retained_deleted_bytes']
    assert retained > 0


def test_partial_collection_member_cannot_read_or_mutate_deleted_files(web, monkeypatch):
    from release_web import trash
    app, owner, hid, cid, fid, _ = web
    member = user(app)
    assert accept(app, member, invite(app, owner, hid, collections=[{'id': cid, 'role': 'editor'}], frames=[{'id': fid, 'role': 'viewer'}])).status_code == 200
    client, csrf = browser(app, member, hid, cid, fid)
    calls = []
    def forbidden(*args, **kwargs):
        calls.append(args)
        raise AssertionError('A member cannot request the trash or its aggregate bytes')
    monkeypatch.setattr(trash, 'api', forbidden)
    assert client.get('/trash?household_id=' + hid + '&collection_id=' + cid).status_code == 403
    assert client.post('/trash', data={'household_id': hid, 'collection_id': cid, 'asset_id': 'any', 'action': 'purge', 'confirm_permanent_delete': 'yes', 'csrf_token': csrf}).status_code == 403
    assert calls == []
