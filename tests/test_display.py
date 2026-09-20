"""Tests for display enrollment, control, uploads, reorder, settings, 413, and auth edges."""

import json
import hashlib
import time
from unittest.mock import patch

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
import app as photo_app
from werkzeug.middleware.proxy_fix import ProxyFix

from tests.conftest import make_test_image


# ===== Display Page =====

class TestDisplayPage:
    """Tests for /display access control."""

    def test_display_rejects_query_token(self, client, app):
        """The enrollment secret is never accepted from a display URL."""
        resp = client.get(
            f'/display?token={photo_app.DISPLAY_TOKEN}',
            headers={'Host': 'example.com'},
            environ_base={'REMOTE_ADDR': '192.0.2.10'},
        )
        assert resp.status_code == 302
        assert '/display/enroll' in resp.headers['Location']
        assert photo_app.DISPLAY_TOKEN not in resp.headers['Location']

    def test_display_without_token_or_session_redirects(self, client):
        """Display page without token or session from non-localhost redirects to login."""
        # Default test client uses 127.0.0.1 (localhost), so override to simulate external
        resp = client.get('/display', headers={'Host': 'example.com'},
                          environ_base={'REMOTE_ADDR': '192.168.1.100'})
        assert resp.status_code == 302
        assert '/display/enroll' in resp.headers['Location']

    def test_display_with_invalid_token_redirects(self, client):
        """Display page with bad token from non-localhost redirects to login."""
        resp = client.get('/display?token=bad-token', headers={'Host': 'example.com'},
                          environ_base={'REMOTE_ADDR': '192.168.1.100'})
        assert resp.status_code == 302
        assert '/display/enroll' in resp.headers['Location']

    def test_external_source_cannot_spoof_localhost_host(self, client):
        resp = client.get('/display', headers={'Host': 'localhost'},
                          environ_base={'REMOTE_ADDR': '192.0.2.10'})
        assert resp.status_code == 302
        assert '/display/enroll' in resp.headers['Location']

    def test_external_source_cannot_spoof_forwarded_host(self, client):
        resp = client.get('/display', headers={
            'Host': 'example.com',
            'X-Forwarded-Host': 'localhost',
        }, environ_base={'REMOTE_ADDR': '192.0.2.10'})
        assert resp.status_code == 302
        assert '/display/enroll' in resp.headers['Location']

    def test_display_with_session(self, auth_client):
        """Display page with authenticated session returns 200."""
        resp = auth_client.get('/display', environ_base={'REMOTE_ADDR': '192.0.2.10'})
        assert resp.status_code == 200


class TestAutoPortraitPairing:
    def test_pair_normalizes_effective_scales_without_mutating_images(self):
        first = {
            'type': 'single',
            'images': [{'filename': 'first.jpg', 'width': 100, 'height': 200, 'scale': 0.6}],
        }
        second = {
            'type': 'single',
            'images': [{'filename': 'second.jpg', 'width': 100, 'height': 300, 'scale': 1.8}],
        }

        paired = photo_app._auto_pair_portraits([first, second])

        assert len(paired) == 1
        assert paired[0]['type'] == 'group'
        assert [img['scale'] for img in paired[0]['images']] == [1.0, 1.0]
        assert first['images'][0]['scale'] == 0.6
        assert second['images'][0]['scale'] == 1.8

    def test_pair_snapshots_refresh_for_each_active_display_profile(self, app, tmp_path, monkeypatch):
        snapshot_folder = tmp_path / 'display_snapshots'
        monkeypatch.setattr(photo_app, 'SNAPSHOT_FOLDER', snapshot_folder)
        monkeypatch.setattr(photo_app, 'load_settings', lambda: {
            'displays': [
                {'id': 'default', 'active': True, 'width': 1920, 'height': 1080},
                {'id': 'portrait', 'active': True, 'width': 1080, 'height': 1920},
                {'id': 'disabled', 'active': False, 'width': 1920, 'height': 1080},
            ],
        })
        rendered_profiles = []
        slide = {'type': 'group', 'group_id': '__pair_profiles', 'images': []}

        def fake_pair_renderer(slide_data, settings, upload_folder, output_folder):
            rendered_profiles.append(settings['_display_id'])
            output_folder.mkdir(parents=True, exist_ok=True)
            path = output_folder / f"{slide_data['group_id']}.{settings['_display_id']}.display.png"
            path.write_bytes(b'PNG')
            return path

        monkeypatch.setattr(photo_app, '_render_pair_slide_snapshot', fake_pair_renderer)

        photo_app._ensure_auto_pair_snapshots([slide], force=True)

        assert rendered_profiles == ['default', 'portrait']
        assert (snapshot_folder / '__pair_profiles.default.display.png').exists()
        assert (snapshot_folder / '__pair_profiles.portrait.display.png').exists()
        assert not (snapshot_folder / '__pair_profiles.disabled.display.png').exists()


class TestDisplayEnrollment:
    """Tests for POST-only display enrollment and session rotation."""

    @staticmethod
    def _external_enroll(client, secret=None, base_url='https://frame.example'):
        return client.post(
            '/api/display/enroll',
            json={'enrollment_secret': secret or photo_app.DISPLAY_TOKEN},
            base_url=base_url,
            environ_base={'REMOTE_ADDR': '192.0.2.10'},
        )

    def test_enrollment_accepts_post_body_and_sets_protected_cookie(self, client):
        resp = self._external_enroll(client)
        assert resp.status_code == 200
        assert resp.get_json() == {'success': True, 'display_url': '/display'}
        cookie = resp.headers['Set-Cookie']
        assert 'HttpOnly' in cookie
        assert 'SameSite=Lax' in cookie
        assert 'Secure' in cookie
        assert photo_app.DISPLAY_TOKEN not in cookie
        assert resp.headers['Cache-Control'] == 'no-store, private'

    def test_forwarded_host_does_not_authorize_when_proxyfix_enabled(self, app):
        original_wsgi = app.wsgi_app
        app.wsgi_app = ProxyFix(original_wsgi, x_for=1, x_proto=1, x_host=1)
        try:
            proxy_client = app.test_client()
            resp = proxy_client.get('/display', headers={
                'Host': 'frame.example',
                'X-Forwarded-For': '192.0.2.10',
                'X-Forwarded-Host': 'localhost',
                'X-Forwarded-Proto': 'https',
            }, environ_base={'REMOTE_ADDR': '10.0.0.2'})
            assert resp.status_code == 302
            assert '/display/enroll' in resp.headers['Location']
        finally:
            app.wsgi_app = original_wsgi

    def test_enrollment_rejects_query_parameter(self, client):
        resp = client.post(
            f'/api/display/enroll?token={photo_app.DISPLAY_TOKEN}',
            base_url='https://frame.example',
            environ_base={'REMOTE_ADDR': '192.0.2.10'},
        )
        assert resp.status_code == 400

    def test_enrolled_external_display_can_access_page_apis_and_images(self, client, auth_client):
        buf = make_test_image(100, 100, 'red')
        upload = auth_client.post('/api/upload', data={'files': (buf, 'test.png')},
                                  content_type='multipart/form-data')
        filename = upload.get_json()['uploaded'][0]

        self._external_enroll(client)
        request_args = {
            'base_url': 'https://frame.example',
            'environ_base': {'REMOTE_ADDR': '192.0.2.10'},
        }
        assert client.get('/display', **request_args).status_code == 200
        assert client.get('/api/images', **request_args).status_code == 200
        assert client.get('/api/settings', **request_args).status_code == 200
        assert client.get('/api/display/state', **request_args).status_code == 200
        assert client.get(f'/uploads/{filename}', **request_args).status_code == 200

    def test_protected_apis_reject_external_unauthenticated_client(self, client):
        args = {
            'headers': {'Host': 'localhost'},
            'environ_base': {'REMOTE_ADDR': '192.0.2.10'},
        }
        for path in ('/api/images', '/api/settings', '/api/display/state', '/uploads/x.png'):
            assert client.get(path, **args).status_code == 401

    def test_display_html_and_response_never_contain_secret(self, client):
        self._external_enroll(client)
        resp = client.get('/display', base_url='https://frame.example',
                          environ_base={'REMOTE_ADDR': '192.0.2.10'})
        assert photo_app.DISPLAY_TOKEN.encode() not in resp.data
        assert b'?token=' not in resp.data

    def test_rotation_invalidates_existing_display_session(self, app, auth_client):
        display_client = app.test_client()
        self._external_enroll(display_client)
        args = {
            'base_url': 'https://frame.example',
            'environ_base': {'REMOTE_ADDR': '192.0.2.10'},
        }
        assert display_client.get('/api/images', **args).status_code == 200

        old_secret = photo_app.DISPLAY_TOKEN
        rotated = auth_client.post('/api/display/rotate-secret')
        assert rotated.status_code == 200
        data = rotated.get_json()
        assert data['existing_display_sessions_invalidated'] is True
        assert data['enrollment_secret'] != old_secret
        assert old_secret.encode() not in rotated.data
        assert display_client.get('/api/images', **args).status_code == 401

    def test_expired_display_session_is_rejected(self, client, app):
        self._external_enroll(client)
        with client.session_transaction(base_url='https://frame.example') as display_session:
            display_session['display_authenticated_at'] = 0
        resp = client.get('/api/images', base_url='https://frame.example',
                          environ_base={'REMOTE_ADDR': '192.0.2.10'})
        assert resp.status_code == 401

    def test_display_from_localhost(self, client, app):
        """Display page from localhost returns 200."""
        # The test client's REMOTE_ADDR is 127.0.0.1 by default
        resp = client.get('/display')
        # In test environment, remote_addr is 127.0.0.1, so this should work
        assert resp.status_code == 200


# ===== Display Enrollment Secret API =====

class TestDisplayEnrollmentSecret:
    """Tests for the administrator enrollment-secret endpoint."""

    def test_display_secret_requires_admin(self, client):
        resp = client.get('/api/display/enrollment-secret')
        assert resp.status_code == 401

    def test_display_secret_returns_secret_without_caching(self, auth_client):
        resp = auth_client.get('/api/display/enrollment-secret')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['enrollment_secret'] == photo_app.DISPLAY_TOKEN
        assert resp.headers['Cache-Control'] == 'no-store, private'


# ===== Serving Uploads =====

class TestServeUploads:
    """Tests for /uploads/<filename>."""

    def test_serve_existing_upload(self, auth_client):
        """GET /uploads/<fname> returns the uploaded image."""
        buf = make_test_image(100, 100, 'red')
        resp = auth_client.post('/api/upload',
                                data={'files': (buf, 'test.png')},
                                content_type='multipart/form-data')
        fname = resp.get_json()['uploaded'][0]

        resp = auth_client.get(f'/uploads/{fname}')
        assert resp.status_code == 200
        assert resp.content_type.startswith('image/')

    def test_serve_nonexistent_upload(self, client):
        """GET /uploads/<bad_fname> returns 404."""
        resp = client.get('/uploads/nonexistent.png')
        assert resp.status_code == 404


# ===== Reorder API =====

class TestReorderAPI:
    """Tests for POST /api/reorder."""

    def test_reorder_images(self, auth_client):
        """POST /api/reorder persists image order."""
        order = ['b.png', 'a.png', 'c.png']
        resp = auth_client.post('/api/reorder',
                                json={'images': order},
                                content_type='application/json')
        assert resp.status_code == 200
        assert resp.get_json()['success'] is True

        settings = auth_client.get('/api/settings').get_json()
        assert settings['image_order'] == order

    def test_reorder_missing_images_field(self, auth_client):
        """POST /api/reorder without 'images' field returns 400."""
        resp = auth_client.post('/api/reorder',
                                json={},
                                content_type='application/json')
        assert resp.status_code == 400

    def test_reorder_requires_auth(self, client):
        """POST /api/reorder without auth returns 401."""
        resp = client.post('/api/reorder',
                           json={'images': []},
                           content_type='application/json')
        assert resp.status_code == 401


# ===== Settings Edge Cases =====

class TestSettingsEdgeCases:
    """Tests for settings API edge cases."""

    def test_get_settings_unauthenticated(self, client):
        """GET /api/settings works without authentication."""
        resp = client.get('/api/settings')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'mat_color' in data

    def test_post_settings_unauthenticated(self, client):
        """POST /api/settings without auth returns 401."""
        resp = client.post('/api/settings',
                           json={'mat_color': '#000'},
                           content_type='application/json')
        assert resp.status_code == 401

    def test_post_settings_disallowed_field_ignored(self, auth_client):
        """POST /api/settings with disallowed fields ignores them."""
        auth_client.post('/api/settings',
                         json={'not_a_real_field': 'evil'},
                         content_type='application/json')
        settings = auth_client.get('/api/settings').get_json()
        assert 'not_a_real_field' not in settings


# ===== 413 Error Handler =====

class TestErrorHandler413:
    """Test for 413 Payload Too Large handler."""

    def test_413_returns_json(self, app):
        """413 error returns JSON with max size message."""
        with app.test_request_context():
            response, status = photo_app.too_large(None)
            assert status == 413
            data = response.get_json()
            assert 'error' in data
            assert '50MB' in data['error']


# ===== Auth Edge Cases =====

class TestAuthEdgeCases:
    """Tests for logout and legacy hash migration."""

    def test_logout_clears_session(self, client, app):
        """GET /logout clears session and redirects to login."""
        # Log in with a non-default password so forced change doesn't interfere
        photo_app.load_users()
        photo_app.change_user_password('admin', 'new-password-123')
        client.post('/login', data={'username': 'admin', 'password': 'new-password-123'})

        # Verify we're logged in (upload page returns 200)
        resp = client.get('/upload')
        assert resp.status_code == 200

        # Logout
        resp = client.get('/logout')
        assert resp.status_code == 302
        assert '/login' in resp.headers['Location']

        # Verify session is cleared
        resp = client.get('/upload')
        assert resp.status_code == 302
        assert '/login' in resp.headers['Location']

    def test_legacy_sha256_migration(self, app):
        """Login with legacy SHA-256 hash auto-migrates to bcrypt."""
        # Create a user with legacy SHA-256 hash
        password = 'testpass'
        salt = 'somesalt'
        legacy_hash = hashlib.sha256((salt + password).encode()).hexdigest()

        users = {
            'legacyuser': {
                'password_hash': legacy_hash,
                'salt': salt,
                'role': 'admin'
            }
        }
        photo_app.save_users(users)

        # Verify login works with legacy hash
        assert photo_app.verify_user('legacyuser', password) is True

        # Verify hash was migrated to bcrypt
        migrated_users = photo_app.load_users()
        assert migrated_users['legacyuser']['password_hash'].startswith('$2b$')
        assert migrated_users['legacyuser']['salt'] is None

    def test_verify_nonexistent_user(self, app):
        """verify_user returns False for nonexistent user."""
        photo_app.load_users()
        assert photo_app.verify_user('nobody', 'pass') is False


# ===== Display Control API =====

class TestDisplayControl:
    """Tests for /api/display/state and /api/display/control."""

    def _upload_images(self, auth_client, count=3):
        """Upload test images so slides exist."""
        for i in range(count):
            buf = make_test_image(100, 100, ['red', 'blue', 'green'][i % 3])
            auth_client.post('/api/upload',
                             data={'files': (buf, f'img{i}.png')},
                             content_type='multipart/form-data')

    def test_get_state_returns_structure(self, client, app):
        """GET /api/display/state returns index, paused, and total."""
        resp = client.get('/api/display/state')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'index' in data
        assert 'paused' in data
        assert 'total' in data

    def test_get_state_no_auth_required(self, client, app):
        """GET /api/display/state works without auth."""
        resp = client.get('/api/display/state')
        assert resp.status_code == 200

    def test_control_requires_auth(self, client, app):
        """POST /api/display/control without auth from non-localhost returns 401."""
        resp = client.post('/api/display/control',
                           json={'action': 'next'},
                           content_type='application/json',
                           headers={'Host': 'example.com'},
                           environ_base={'REMOTE_ADDR': '192.168.1.100'})
        assert resp.status_code == 401

    def test_control_allowed_from_localhost(self, client, app):
        """POST /api/display/control from localhost works without session auth."""
        resp = client.post('/api/display/control',
                           json={'action': 'pause'},
                           content_type='application/json')
        assert resp.status_code == 200

    def test_control_invalid_action(self, auth_client):
        """POST /api/display/control with bad action returns 400."""
        resp = auth_client.post('/api/display/control',
                                json={'action': 'invalid'},
                                content_type='application/json')
        assert resp.status_code == 400

    def test_control_next(self, auth_client):
        """POST next advances the index."""
        self._upload_images(auth_client)
        # Reset state
        photo_app._display_state['index'] = 0
        photo_app._display_state['paused'] = True
        photo_app._display_state['last_advanced_at'] = time.time()

        resp = auth_client.post('/api/display/control',
                                json={'action': 'next'},
                                content_type='application/json')
        assert resp.status_code == 200
        assert resp.get_json()['index'] == 1

    def test_control_prev(self, auth_client):
        """POST prev decrements the index."""
        self._upload_images(auth_client)
        photo_app._display_state['index'] = 2
        photo_app._display_state['paused'] = True
        photo_app._display_state['last_advanced_at'] = time.time()

        resp = auth_client.post('/api/display/control',
                                json={'action': 'prev'},
                                content_type='application/json')
        assert resp.status_code == 200
        assert resp.get_json()['index'] == 1

    def test_control_pause_play(self, auth_client):
        """POST pause/play toggles paused state."""
        self._upload_images(auth_client)
        photo_app._display_state['index'] = 0
        photo_app._display_state['paused'] = False
        photo_app._display_state['last_advanced_at'] = time.time()

        resp = auth_client.post('/api/display/control',
                                json={'action': 'pause'},
                                content_type='application/json')
        assert resp.get_json()['paused'] is True

        resp = auth_client.post('/api/display/control',
                                json={'action': 'play'},
                                content_type='application/json')
        assert resp.get_json()['paused'] is False

    def test_auto_advance(self, auth_client):
        """After slideshow_interval seconds, index auto-advances."""
        self._upload_images(auth_client)
        photo_app._display_state['index'] = 0
        photo_app._display_state['paused'] = False
        # Set last_advanced_at to 65 seconds ago (interval is 60s default)
        photo_app._display_state['last_advanced_at'] = time.time() - 65

        resp = auth_client.get('/api/display/state')
        data = resp.get_json()
        assert data['index'] == 1  # Should have auto-advanced by 1

    def test_state_with_no_slides(self, client, app):
        """State returns index 0 when no slides exist."""
        resp = client.get('/api/display/state')
        data = resp.get_json()
        assert data['index'] == 0
        assert data['total'] == 0

    def test_state_renders_synthetic_portrait_pair(self, app, tmp_path, monkeypatch):
        """Profile snapshots must use the synthetic pair renderer."""
        snapshot_folder = tmp_path / 'display_snapshots'
        monkeypatch.setattr(photo_app, 'SNAPSHOT_FOLDER', snapshot_folder)
        profile = {'id': 'default', 'width': 1920, 'height': 1080}
        slide = {
            'type': 'group',
            'group_id': '__pair_test',
            'images': [
                {'filename': 'one.jpg'},
                {'filename': 'two.jpg'},
            ],
        }

        def fake_pair_renderer(slide_data, settings, upload_folder, output_folder):
            output_folder.mkdir(parents=True, exist_ok=True)
            path = output_folder / '__pair_test.default.display.png'
            path.write_bytes(b'PNG')
            return path

        monkeypatch.setattr(photo_app, '_render_pair_slide_snapshot', fake_pair_renderer)
        monkeypatch.setattr(photo_app, '_render_group_snapshot', lambda *args: None)

        url = photo_app._profile_snapshot_url(slide, profile)

        assert url.startswith('/snapshots/__pair_test.default.display.png?v=')
