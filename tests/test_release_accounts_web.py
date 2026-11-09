"""Browser account/admin screens retain the v1 session, CSRF and resource policies."""
import re
import pyotp
from sqlalchemy import select
from tests.test_release_web import web
from tests.test_release_backend import release, auth, user, invite, accept
from release_backend import models as m


def browser(app, owner, hid, cid, fid):
    client = app.test_client()
    issued = client.post('/api/v1/auth/web-bridge', headers=auth(owner), json={
        'household_id': hid, 'collection_id': cid, 'frame_id': fid, 'path': '/upload'})
    assert issued.status_code == 200
    assert client.post('/api/v1/auth/web-bridge/redeem', json={'bridge_token': issued.json['bridge_token']}).status_code == 303
    page = client.get('/account')
    assert page.status_code == 200
    token = re.search(r'name="csrf-token" content="([^"]+)"', page.text).group(1)
    return client, token


def test_advanced_account_links_and_csrf_password_revocation(web):
    app, owner, hid, cid, fid, _ = web
    client, token = browser(app, owner, hid, cid, fid)
    for route in ['/account', '/change-password', '/mfa', '/admin?household_id=' + hid]:
        assert client.get(route).status_code == 200
    assert client.post('/change-password', data={'current_password': 'fixture-owner-password', 'password': 'changed-secure-password'}).status_code == 403
    response = client.post('/change-password', data={'csrf_token': token, 'current_password': 'fixture-owner-password', 'password': 'changed-secure-password'})
    assert response.status_code == 303 and response.location == '/login'
    assert app.test_client().get('/api/v1/auth/me', headers=auth(owner)).status_code == 401


def test_admin_invitation_only_grants_selected_resources_and_rejects_foreign_households(web):
    app, owner, hid, cid, fid, _ = web
    client, token = browser(app, owner, hid, cid, fid)
    form = {'csrf_token': token, 'household_id': hid, 'action': 'invite', 'recipient_email': 'guest@example.test',
            'collections_' + cid: 'viewer', 'frames_' + fid: 'controller'}
    assert client.post('/admin', data=form | {'csrf_token': 'forged'}).status_code == 403
    response = client.post('/admin', data=form)
    assert response.status_code == 200 and '/connect#v=1' in response.text
    assert 'token=' not in (response.location or '')
    with app.app_context(), app.extensions['release_store'].engine.connect() as db:
        grants = db.execute(select(m.invitations.c.grants).where(m.invitations.c.household_id == hid)).scalar_one()
        assert grants == {'collections': [{'id': cid, 'role': 'viewer'}], 'frames': [{'id': fid, 'role': 'controller'}]}
        iid = db.execute(select(m.invitations.c.id).where(m.invitations.c.household_id == hid)).scalar_one()
    assert client.post('/admin', data={'csrf_token': token, 'household_id': hid, 'action': 'revoke_invitation', 'invitation_id': iid}).status_code == 200
    with app.app_context(), app.extensions['release_store'].engine.connect() as db:
        assert db.execute(select(m.invitations.c.revoked).where(m.invitations.c.id == iid)).scalar_one() is True
    assert client.get('/admin?household_id=foreign').status_code == 404
    assert client.post('/admin', data=form | {'collections_' + cid: 'owner'}).status_code == 400


def test_member_cannot_open_admin_and_grant_replacement_requires_explicit_confirmation(web):
    app, owner, hid, cid, fid, _ = web
    member = user(app)
    invitation = invite(app, owner, hid, collections=[{'id': cid, 'role': 'viewer'}], frames=[{'id': fid, 'role': 'viewer'}])
    assert accept(app, member, invitation).status_code == 200
    guest, _ = browser(app, member, hid, cid, fid)
    assert guest.get('/admin?household_id=' + hid).status_code == 404
    client, token = browser(app, owner, hid, cid, fid)
    memberships = app.test_client().get('/api/v1/households/' + hid + '/memberships', headers=auth(owner)).json['memberships']
    mid = next(item['id'] for item in memberships if item['user_id'] == member['user']['id'])
    form = {'csrf_token': token, 'household_id': hid, 'membership_id': mid, 'action': 'grants'}
    assert client.post('/admin', data=form).status_code == 400
    assert client.post('/admin', data=form | {'confirm_replace': 'yes', 'collections_' + cid: 'contributor'}).status_code == 200
    assert guest.get('/api/v1/households/' + hid + '/frames').json['frames'] == []


def test_authenticator_enrollment_never_renders_bearer_credentials(web):
    app, owner, hid, cid, fid, _ = web
    client, token = browser(app, owner, hid, cid, fid)
    response = client.post('/mfa', data={'csrf_token': token, 'action': 'start'})
    assert response.status_code == 200
    secret = re.search(r'<pre>([A-Z2-7]+)</pre>', response.text).group(1)
    response = client.post('/mfa', data={'csrf_token': token, 'action': 'complete', 'code': pyotp.TOTP(secret).now()})
    assert response.status_code == 200 and 'Save your recovery codes' in response.text
    assert 'access_token' not in response.text and 'refresh_token' not in response.text
    assert client.get('/account').status_code == 200
    assert app.test_client().get('/api/v1/auth/me', headers=auth(owner)).status_code == 401


def test_hosted_registration_never_grants_subscription_or_household(web):
    app, _, _, _, _, _ = web
    app.config.update(RELEASE_MODE='hosted', REGISTER_ENABLED=True)
    client = app.test_client()
    page = client.get('/register')
    token = re.search(r'name="csrf-token" content="([^"]+)"', page.text).group(1)
    assert client.post('/register', data={'email': 'signup@example.test', 'password': 'new-signup-password'}).status_code == 403
    response = client.post('/register', data={'csrf_token': token, 'email': 'signup@example.test', 'password': 'new-signup-password'})
    assert response.status_code == 303
    identity = client.get('/api/v1/auth/me').json
    assert identity['households'] == [] and identity['user']['email_verified'] is False
    page = client.get('/subscribe')
    assert page.status_code == 200 and 'Website purchases are not configured' in page.text
    token = re.search(r'name="csrf-token" content="([^"]+)"', page.text).group(1)
    assert client.post('/subscribe', data={'csrf_token': token, 'action': 'create', 'name': 'Unverified'}).status_code == 403


from tests.test_release_billing import billing_app


def test_website_checkout_preserves_owner_csrf_and_retry_identity(billing_app):
    from release_web import register
    from tests.test_release_billing import PLANS
    app = billing_app
    register(app)
    class Provider:
        calls = []
        def create_checkout(self, **values):
            self.calls.append(values)
            return 'https://checkout.example.test/session'
    provider = Provider()
    app.config.update(BILLING_PLANS=[dict(PLANS[0], provider='stripe', stripe_price_id='test-configured-price')],
                      BILLING_PROVIDERS={'stripe': provider}, BILLING_CHECKOUT_HOSTS=['checkout.example.test'])
    client = app.test_client()
    issued = client.post('/api/v1/auth/web-bridge', headers={'Authorization': 'Bearer owner-token'}, json={'path': '/workspace'}).json
    assert client.post('/api/v1/auth/web-bridge/redeem', json={'bridge_token': issued['bridge_token']}).status_code == 303
    page = client.get('/subscribe?household_id=a')
    assert page.status_code == 200 and 'Test plan' in page.text
    token = re.search(r'name="csrf-token" content="([^"]+)"', page.text).group(1)
    form = {'household_id': 'a', 'plan_id': 'fixture'}
    assert client.post('/subscribe', data=form).status_code == 403
    first = client.post('/subscribe', data=form | {'csrf_token': token})
    second = client.post('/subscribe', data=form | {'csrf_token': token})
    assert first.status_code == second.status_code == 200
    assert 'https://checkout.example.test/session' in first.text
    assert len(provider.calls) == 1
    assert client.post('/subscribe', data=form | {'csrf_token': token, 'household_id': 'foreign'}).status_code == 404


def test_account_device_revoke_is_session_scoped_and_expired_browser_returns_to_login(web):
    app, owner, hid, cid, fid, _ = web
    client, token = browser(app, owner, hid, cid, fid)
    other = user(app)
    assert client.post('/account', data={'csrf_token': token, 'action': 'revoke', 'session_id': other['session_id']}).status_code == 404
    assert app.test_client().get('/api/v1/auth/me', headers=auth(other)).status_code == 200
    assert client.post('/account', data={'csrf_token': token, 'action': 'revoke', 'session_id': owner['session_id']}).status_code == 303
    assert client.get('/account').status_code == 303
    assert app.test_client().get('/account').location == '/login'
