"""Tests for forced password change, network info, and maintenance window."""

import json
from datetime import datetime
from unittest.mock import patch

import pytest

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
import app as photo_app


class TestForcedPasswordChange:
    """Tests for the random one-time administrator credential."""

    @staticmethod
    def _bootstrap_password():
        photo_app.load_users()
        return photo_app.INITIAL_ADMIN_PASSWORD_FILE.read_text()

    def test_bootstrap_password_is_random_protected_and_not_fixed(self, app):
        password = self._bootstrap_password()
        assert password != 'password'
        assert len(password) >= 12
        assert photo_app.INITIAL_ADMIN_PASSWORD_FILE.stat().st_mode & 0o777 == 0o600
        assert photo_app.verify_user('admin', password)
        assert photo_app.user_requires_password_change('admin') is True

    def test_login_with_bootstrap_password_redirects(self, client):
        password = self._bootstrap_password()
        resp = client.post('/login', data={
            'username': 'admin',
            'password': password,
        })
        assert resp.status_code == 302
        assert '/change-password' in resp.headers['Location']
        assert 'forced=1' in resp.headers['Location']

    def test_login_after_password_change_goes_to_upload(self, client):
        """After changing password, login should go to /upload."""
        self._bootstrap_password()
        photo_app.change_user_password('admin', 'new-password-123')

        resp = client.post('/login', data={
            'username': 'admin',
            'password': 'new-password-123'
        })
        assert resp.status_code == 302
        assert '/upload' in resp.headers['Location']

    def test_forced_change_blocks_navigation(self, client):
        """With the bootstrap password, navigation is restricted to changing it."""
        password = self._bootstrap_password()
        # Log in (will be redirected, but session is set)
        client.post('/login', data={
            'username': 'admin',
            'password': password,
        })
        # Try to access upload page
        resp = client.get('/upload')
        assert resp.status_code == 302
        assert '/change-password' in resp.headers['Location']

    def test_forced_change_skips_current_password(self, client):
        """Forced mode should not require current password field."""
        password = self._bootstrap_password()
        client.post('/login', data={
            'username': 'admin',
            'password': password,
        })
        # Submit forced password change without current password
        resp = client.post('/change-password?forced=1', data={
            'forced': '1',
            'new_password': 'new-password-123',
            'confirm': 'new-password-123'
        })
        assert resp.status_code == 302
        assert '/upload' in resp.headers['Location']

        # Verify new password works
        assert photo_app.verify_user('admin', 'new-password-123')

    def test_change_password_page_accessible_with_default(self, client):
        """Change password page is accessible with the bootstrap credential."""
        password = self._bootstrap_password()
        client.post('/login', data={
            'username': 'admin',
            'password': password,
        })
        resp = client.get('/change-password?forced=1')
        assert resp.status_code == 200
        assert b'Set New Password' in resp.data
        assert b'Please set a new password' in resp.data

    def test_password_change_marker_clears_after_change(self, app):
        self._bootstrap_password()
        assert photo_app.user_requires_password_change('admin') is True

        photo_app.change_user_password('admin', 'new-password-123')
        assert photo_app.user_requires_password_change('admin') is False

    def test_password_change_marker_nonexistent_user(self, app):
        assert photo_app.user_requires_password_change('nobody') is False

    def test_forced_flag_cannot_bypass_current_password(self, client):
        self._bootstrap_password()
        photo_app.change_user_password('admin', 'current-password-123')
        client.post('/login', data={'username': 'admin', 'password': 'current-password-123'})
        resp = client.post('/change-password?forced=1', data={
            'forced': '1',
            'new_password': 'different-password-123',
            'confirm': 'different-password-123',
        })
        assert resp.status_code == 200
        assert b'Current password is incorrect' in resp.data


class TestAutheliaSSO:
    """Tests for the trusted reverse-proxy Authelia login bridge."""

    @staticmethod
    def _enable(monkeypatch):
        monkeypatch.setattr(photo_app, '_BEHIND_PROXY', True)
        monkeypatch.setattr(photo_app, 'AUTHELIA_URL', 'https://auth.example.test')
        monkeypatch.setattr(photo_app, 'AUTHELIA_USER_MAP', {
            'andrew': 'admin',
            'allison': 'allison',
        })
        monkeypatch.setattr(photo_app, 'AUTHELIA_ADMIN_GROUPS', {'admins'})

    def test_login_redirects_to_authelia(self, client, monkeypatch):
        self._enable(monkeypatch)

        response = client.get('/login?next=/gallery')

        assert response.status_code == 302
        assert response.headers['Location'].startswith('https://auth.example.test/?')
        assert 'rd=http%3A%2F%2Flocalhost%2Fgallery' in response.headers['Location']
        assert 'rm=GET' in response.headers['Location']

    def test_authelia_identity_bridges_to_local_session(self, client, monkeypatch):
        self._enable(monkeypatch)
        photo_app.change_user_password('admin', 'new-password-123')

        response = client.get('/api/gallery', headers={'Remote-User': 'andrew'})

        assert response.status_code == 200
        with client.session_transaction() as flask_session:
            assert flask_session['authenticated'] is True
            assert flask_session['username'] == 'admin'
            assert flask_session['auth_provider'] == 'authelia'

    def test_unmapped_authelia_identity_is_rejected(self, client, monkeypatch):
        self._enable(monkeypatch)

        response = client.get('/api/gallery', headers={'Remote-User': 'unknown'})

        assert response.status_code == 403
        assert 'not mapped' in response.get_json()['error']

    def test_authelia_groups_determine_application_role(self, client, monkeypatch):
        self._enable(monkeypatch)
        photo_app.change_user_password('admin', 'new-password-123')

        admin_response = client.get('/admin/users', headers={
            'Remote-User': 'andrew',
            'Remote-Groups': 'admins',
        })
        assert admin_response.status_code == 200
        with client.session_transaction() as flask_session:
            assert flask_session['auth_role'] == 'admin'
            assert flask_session['authelia_groups'] == ['admins']

        user_response = client.get('/admin/users', headers={
            'Remote-User': 'andrew',
            'Remote-Groups': 'family',
        })
        assert user_response.status_code == 403


class TestPasswordPolicy:
    def test_central_policy_requires_twelve_characters(self, app):
        assert photo_app.validate_password('short')[0] is False
        assert photo_app.validate_password('twelve-chars')[0] is True

    def test_create_and_change_use_same_policy(self, app):
        photo_app.load_users()
        assert photo_app.create_user('person', 'too-short')[0] is False
        assert photo_app.create_user('person', 'long-enough-password')[0] is True
        assert photo_app.change_user_password('person', 'short')[0] is False

    def test_admin_reset_requires_policy_and_forces_change(self, auth_client):
        assert photo_app.create_user('person', 'long-enough-password')[0] is True
        short = auth_client.post('/api/admin/users/person/password', json={'password': 'short'})
        assert short.status_code == 400
        reset = auth_client.post('/api/admin/users/person/password',
                                 json={'password': 'replacement-password'})
        assert reset.status_code == 200
        assert photo_app.user_requires_password_change('person') is True


class TestSecurityEvents:
    @staticmethod
    def _events():
        return [json.loads(line) for line in photo_app.SECURITY_LOG_FILE.read_text().splitlines()]

    def test_successful_and_failed_logins_are_structured_without_passwords(self, client):
        password = TestForcedPasswordChange._bootstrap_password()
        client.post('/login', data={'username': 'admin', 'password': 'wrong-password'})
        client.post('/login', data={'username': 'admin', 'password': password})
        events = [event for event in self._events() if event['event_type'] == 'login']
        assert [event['status'] for event in events] == ['failure', 'success']
        assert all({'timestamp', 'event_type', 'username', 'source_ip', 'status'} <= event.keys()
                   for event in events)
        serialized = photo_app.SECURITY_LOG_FILE.read_text()
        assert password not in serialized
        assert 'wrong-password' not in serialized

    def test_account_and_display_events_are_recorded(self, auth_client):
        auth_client.post('/api/admin/users', json={
            'username': 'person', 'password': 'long-enough-password', 'role': 'user',
        })
        auth_client.delete('/api/admin/users/person')
        auth_client.post('/api/display/rotate-secret')
        event_types = {event['event_type'] for event in self._events()}
        assert {'user_creation', 'user_deletion', 'display_credential_rotation'} <= event_types


class TestNetworkInfo:
    """Tests for the /api/network-info endpoint."""

    def _get_auth_client_with_changed_password(self, client):
        """Get an authenticated client with a non-default password."""
        photo_app.load_users()
        photo_app.change_user_password('admin', 'new-password-123')
        client.post('/login', data={
            'username': 'admin',
            'password': 'new-password-123'
        })
        return client

    def test_network_info_requires_admin(self, client):
        """Non-authenticated users should get 401."""
        resp = client.get('/api/network-info')
        assert resp.status_code == 401

    def test_network_info_returns_data(self, client):
        """Admin should get network info with at least the expected keys."""
        client = self._get_auth_client_with_changed_password(client)
        with patch('app.subprocess.run') as mock_run:
            mock_run.side_effect = FileNotFoundError
            resp = client.get('/api/network-info')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'local_ip' in data
        assert 'tailscale_ip' in data


class TestMaintenanceWindow:
    """Tests for the /api/maintenance-window endpoint."""

    def test_no_schedules_allows_deploy(self, client):
        """No TV schedules → can_deploy=True."""
        # Default settings have no schedules
        photo_app.load_settings()
        resp = client.get('/api/maintenance-window')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['can_deploy'] is True

    def test_during_tv_on_blocks_deploy(self, client, app):
        """During TV on-window → can_deploy=False."""
        # Set up a schedule that covers the current time
        now = datetime.now()
        on_time = f'{now.hour:02d}:00'
        off_time = f'{(now.hour + 1) % 24:02d}:00'
        # Only block if we're not at minute 0 of the off hour
        if now.minute == 0 and now.hour == (now.hour + 1) % 24:
            on_time = f'{now.hour:02d}:00'
            off_time = f'{(now.hour + 2) % 24:02d}:00'

        settings = photo_app.load_settings()
        settings['tv_schedules'] = [{
            'id': 'test1',
            'on_time': on_time,
            'off_time': off_time,
            'days': [now.weekday()],
            'enabled': True
        }]
        photo_app.save_settings(settings)

        resp = client.get('/api/maintenance-window')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['can_deploy'] is False
        assert 'TV is scheduled ON' in data['reason']

    def test_outside_schedule_allows_deploy(self, client, app):
        """Outside TV schedule → can_deploy=True."""
        now = datetime.now()
        # Set schedule for hours that don't include now
        past_hour = (now.hour - 3) % 24
        past_hour_end = (now.hour - 2) % 24

        settings = photo_app.load_settings()
        settings['tv_schedules'] = [{
            'id': 'test2',
            'on_time': f'{past_hour:02d}:00',
            'off_time': f'{past_hour_end:02d}:00',
            'days': [now.weekday()],
            'enabled': True
        }]
        photo_app.save_settings(settings)

        resp = client.get('/api/maintenance-window')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['can_deploy'] is True

    def test_disabled_schedule_allows_deploy(self, client, app):
        """Disabled schedules should not block deploys."""
        now = datetime.now()
        settings = photo_app.load_settings()
        settings['tv_schedules'] = [{
            'id': 'test3',
            'on_time': f'{now.hour:02d}:00',
            'off_time': f'{(now.hour + 1) % 24:02d}:00',
            'days': [now.weekday()],
            'enabled': False
        }]
        photo_app.save_settings(settings)

        resp = client.get('/api/maintenance-window')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['can_deploy'] is True

    def test_wrong_day_allows_deploy(self, client, app):
        """Schedule on a different day should not block deploys."""
        now = datetime.now()
        other_day = (now.weekday() + 1) % 7
        settings = photo_app.load_settings()
        settings['tv_schedules'] = [{
            'id': 'test4',
            'on_time': f'{now.hour:02d}:00',
            'off_time': f'{(now.hour + 1) % 24:02d}:00',
            'days': [other_day],
            'enabled': True
        }]
        photo_app.save_settings(settings)

        resp = client.get('/api/maintenance-window')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['can_deploy'] is True
