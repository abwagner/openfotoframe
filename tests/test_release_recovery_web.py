"""Email fragments require deliberate CSRF POST; identity and MFA stay in v1 API."""
import re
from sqlalchemy import select
from release_backend import models as m
from release_web import register
from tests.test_release_backend import release, bootstrap, auth, user


def csrf(client, route):
    page = client.get(route)
    assert page.status_code == 200
    assert page.headers['Referrer-Policy'] == 'no-referrer'
    assert 'no-store' in page.headers['Cache-Control']
    return re.search(r'name="csrf_token" value="([^"]+)"', page.get_data(as_text=True))[1]


def setup(release):
    register(release)
    owner = bootstrap(release)
    return release.test_client(), owner


def test_verification_get_never_consumes_and_post_is_identity_bound(release):
    client, owner = setup(release)
    sent = []
    release.config['VERIFICATION_DELIVERY'] = lambda recipient, token: sent.append(token)
    response = client.post('/api/v1/auth/verification/request', headers=auth(owner), json={})
    assert response.status_code == 200
    token = sent[0]
    key = csrf(client, '/verify?token=' + token)
    with release.extensions['release_store'].transaction() as db:
        assert not db.execute(select(m.challenges.c.consumed).where(m.challenges.c.purpose == 'verify_email')).scalar_one()
    assert client.post('/verify', headers=auth(owner), data={'token': token}).status_code == 403
    stranger = user(release)
    assert client.post('/verify', headers=auth(stranger), data={'csrf_token': key, 'token': token}).status_code == 403
    response = client.post('/verify', headers=auth(owner), data={'csrf_token': key, 'token': token})
    assert response.status_code == 200 and b'Your email is verified' in response.data
    assert token.encode() not in response.data
    assert client.post('/verify', headers=auth(owner), data={'csrf_token': key, 'token': token}).status_code == 401


def test_recovery_requests_and_completion_require_csrf_and_explicit_post(release):
    client, owner = setup(release)
    sent = []
    release.config['RECOVERY_DELIVERY'] = lambda recipient, token: sent.append(token)
    key = csrf(client, '/recover')
    assert client.post('/recover', data={'action': 'request', 'email': 'owner@example.test'}).status_code == 403
    response = client.post('/recover', data={'csrf_token': key, 'action': 'request', 'email': 'owner@example.test'})
    assert response.status_code == 200 and len(sent) == 1
    token = sent[0]
    csrf(client, '/recover?token=' + token)
    assert client.post('/recover', data={'csrf_token': key, 'token': token,
        'password': 'new-synthetic-recovery-password'}).status_code == 200
    assert client.post('/recover', data={'csrf_token': key, 'token': token,
        'password': 'new-synthetic-recovery-password'}).status_code == 401
    assert client.post('/api/v1/auth/login', json={'email': 'owner@example.test',
        'password': 'new-synthetic-recovery-password'}).status_code == 200


def test_fragment_script_strips_before_dom_and_never_automatically_posts(release):
    client, owner = setup(release)
    response = client.get('/recovery-static/email-link.js')
    assert response.status_code == 200
    source = response.get_data(as_text=True)
    assert source.index('history.replaceState') < source.index("addEventListener('DOMContentLoaded'")
    assert 'fetch(' not in source and '.submit(' not in source and 'localStorage' not in source
    assert 'sessionStorage' not in source
    html = client.get('/recover').get_data(as_text=True)
    assert 'third-party' not in html
    assert '<button id="complete-link" disabled>' in html


def test_entrypoint_smtp_environment_is_strict_and_optional(tmp_path):
    from release_app import environment_config, create_app
    import pytest
    env = {'RELEASE_MODE': 'self_hosted', 'SECRET_KEY': 'fixture-private-secret-at-least-32-characters',
        'PUBLIC_ORIGIN': 'https://fixture.example', 'DATABASE_URL': 'sqlite:///:memory:',
        'MEDIA_ROOT': str(tmp_path / 'media'), 'SMTP_HOST': 'smtp.fixture.example', 'SMTP_PORT': '587',
        'SMTP_USERNAME': 'fixture', 'SMTP_PASSWORD': 'fixture-private-password',
        'SMTP_FROM': 'frame@fixture.example', 'SMTP_TLS_MODE': 'starttls'}
    config = environment_config(env)
    assert config['SMTP_PORT'] == 587
    config.update(TESTING=True, INIT_SCHEMA=True)
    app = create_app(config)
    assert callable(app.config['VERIFICATION_DELIVERY']) and callable(app.config['RECOVERY_DELIVERY'])
    with pytest.raises(ValueError, match='SMTP_PORT'):
        environment_config({**env, 'SMTP_PORT': 'not-a-port'})
    config.pop('SMTP_PASSWORD')
    with pytest.raises(ValueError, match='Complete SMTP'):
        create_app(config)
