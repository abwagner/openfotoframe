"""Household aggregate storage is never exposed to partial collection members."""
from tests.test_release_backend import release, auth, user, invite, accept
from tests.test_release_web import web, upload
from tests.test_release_accounts_web import browser


def test_owner_storage_includes_retained_deleted_bytes_and_selfhost_limit_explanation(web):
    app, owner, hid, cid, fid, headers = web
    aid = upload(app, headers)
    client, _ = browser(app, owner, hid, cid, fid)
    response = client.get('/storage?household_id=' + hid)
    assert response.status_code == 200
    assert 'No application storage limit is configured' in response.text
    assert 'Available disk capacity is not reported here' in response.text
    assert client.delete('/api/gallery/' + aid, headers=headers).status_code == 200
    value = client.get('/api/v1/households/' + hid + '/storage', headers=auth(owner)).json
    assert value['usage']['active_bytes'] == 0
    assert value['usage']['retained_deleted_bytes'] > 0
    page = client.get('/storage?household_id=' + hid)
    assert 'Retained deleted original files' in page.text
    assert 'Deleted files continue to count until permanently removed' in page.text


def test_partial_collection_member_browser_never_invokes_aggregate_handler(web, monkeypatch):
    from release_web import storage
    app, owner, hid, cid, fid, _ = web
    member = user(app)
    assert accept(app, member, invite(app, owner, hid, collections=[{'id': cid, 'role': 'contributor'}], frames=[{'id': fid, 'role': 'viewer'}])).status_code == 200
    client, _ = browser(app, member, hid, cid, fid)
    calls = []
    def forbidden(*args, **kwargs):
        calls.append(args)
        raise AssertionError('A member page must not ask for aggregate household usage')
    monkeypatch.setattr(storage, 'api', forbidden)
    assert client.get('/storage?household_id=' + hid).status_code == 403
    assert calls == []
    page = client.get('/account')
    assert 'Storage for Home' not in page.text
    assert client.get('/storage?household_id=foreign').status_code == 404


def test_hosted_inactive_full_storage_is_actionable_without_starting_checkout(web, monkeypatch):
    from flask import jsonify
    from release_web import storage
    app, owner, hid, cid, fid, _ = web
    client, _ = browser(app, owner, hid, cid, fid)
    calls = []
    def summary(path, *args, **kwargs):
        calls.append(path)
        return jsonify(household_id=hid, deployment_mode='hosted', accounting='original_bytes',
                       usage={'active_bytes': 60, 'retained_deleted_bytes': 40, 'total_bytes': 100, 'active_assets': 2, 'frames': 1},
                       entitlement={'active': False, 'status': 'expired', 'plan_id': 'configured', 'valid_until': 1},
                       limits={'storage_bytes': 100, 'asset_limit': 5, 'frame_limit': 2},
                       remaining={'storage_bytes': 0, 'assets': 3, 'frames': 1})
    monkeypatch.setattr(storage, 'api', summary)
    page = client.get('/storage?household_id=' + hid)
    assert page.status_code == 200
    assert 'Uploads paused: the hosted subscription is inactive' in page.text
    assert 'Household storage is full' in page.text
    assert 'Nothing is purchased or upgraded automatically' in page.text
    assert '/trash?household_id=' + hid in page.text
    assert calls == ['/api/v1/households/' + hid + '/storage']
