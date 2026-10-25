#!/usr/bin/env python3
"""
OpenFotoFrame - A web-based photo display system for Raspberry Pi
Upload photos via web interface, display on TV with customizable mat colors
"""

import os
import json
import uuid
import secrets
import hashlib
import fcntl
import re
import random
import time
import subprocess
import threading
import logging
import base64
import warnings
from ipaddress import ip_address
from urllib.parse import urlparse, urlencode
from datetime import datetime
from pathlib import Path
from functools import wraps
from flask import Flask, render_template, request, jsonify, send_from_directory, redirect, url_for, session, has_request_context

import bcrypt
from flask_wtf.csrf import CSRFProtect
from flask.sessions import SecureCookieSessionInterface
from werkzeug.utils import secure_filename
import tempfile

import io
import colorsys
import qrcode
import qrcode.image.svg
from artwork_catalog import catalog as artwork_catalog, validate_content, resolve_art

from PIL import Image, ImageOps, UnidentifiedImageError
from apscheduler.schedulers.background import BackgroundScheduler
import imagehash
import pyotp
from cryptography.fernet import Fernet, InvalidToken
from webauthn import (
    generate_registration_options, verify_registration_response,
    generate_authentication_options, verify_authentication_response, options_to_json,
)
from webauthn.helpers.structs import (
    PublicKeyCredentialDescriptor, UserVerificationRequirement,
)

from render_display import (
    render_snapshot as _render_snapshot,
    render_group_snapshot as _render_group_snapshot,
    render_pair_slide_snapshot as _render_pair_slide_snapshot,
    delete_snapshot as _delete_snapshot,
    delete_group_snapshot as _delete_group_snapshot,
    get_groups_containing,
    backfill_snapshots as _backfill_snapshots,
    regenerate_all_snapshots as _regenerate_all_snapshots,
)

app = Flask(__name__)
csrf = CSRFProtect(app)


class RequestAwareSecureCookieSessionInterface(SecureCookieSessionInterface):
    """Set Secure on session cookies whenever Flask sees an HTTPS request."""

    def get_cookie_secure(self, app):
        return super().get_cookie_secure(app) or request.is_secure


app.session_interface = RequestAwareSecureCookieSessionInterface()

# Configuration
UPLOAD_FOLDER = Path(__file__).parent / 'uploads'
DATA_FOLDER = Path(__file__).parent / 'data'
SETTINGS_FILE = DATA_FOLDER / 'settings.json'
USERS_FILE = DATA_FOLDER / 'users.json'
INITIAL_ADMIN_PASSWORD_FILE = DATA_FOLDER / '.initial_admin_password'
SECURITY_LOG_FILE = DATA_FOLDER / 'security-events.jsonl'
MFA_ENCRYPTION_KEY_FILE = DATA_FOLDER / '.mfa_key'
GALLERY_FILE = DATA_FOLDER / 'gallery.json'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp'}
THUMBNAIL_FOLDER = UPLOAD_FOLDER / 'thumbnails'
THUMBNAIL_MAX_SIZE = (400, 400)
MAX_IMAGE_PIXELS = int(os.environ.get('MAX_IMAGE_PIXELS', 80_000_000))
MAX_IMAGE_DIMENSION = int(os.environ.get('MAX_IMAGE_DIMENSION', 20_000))
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
RCLONE_CONFIG_DIR = DATA_FOLDER / 'rclone'
RCLONE_CONFIG_FILE = RCLONE_CONFIG_DIR / 'rclone.conf'
BACKUP_LOG_FILE = DATA_FOLDER / 'backup_log.json'
BACKUP_LOCK_FILE = DATA_FOLDER / '.backup.lock'
SNAPSHOT_FOLDER = DATA_FOLDER / 'display_snapshots'

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB max upload

# Ensure folders exist
UPLOAD_FOLDER.mkdir(exist_ok=True)
THUMBNAIL_FOLDER.mkdir(exist_ok=True)
DATA_FOLDER.mkdir(exist_ok=True)
SNAPSHOT_FOLDER.mkdir(exist_ok=True)

# Generate a secret key for sessions (persisted so sessions survive restarts)
SECRET_KEY_FILE = DATA_FOLDER / '.secret_key'
if SECRET_KEY_FILE.exists():
    app.secret_key = SECRET_KEY_FILE.read_text().strip()
else:
    app.secret_key = secrets.token_hex(32)
    SECRET_KEY_FILE.write_text(app.secret_key)
    os.chmod(SECRET_KEY_FILE, 0o600)

# Display enrollment secret (legacy filename retained for migration compatibility)
DISPLAY_TOKEN_FILE = DATA_FOLDER / '.display_token'
if DISPLAY_TOKEN_FILE.exists():
    DISPLAY_TOKEN = DISPLAY_TOKEN_FILE.read_text().strip()
else:
    DISPLAY_TOKEN = secrets.token_urlsafe(32)
    DISPLAY_TOKEN_FILE.write_text(DISPLAY_TOKEN)
try:
    os.chmod(DISPLAY_TOKEN_FILE, 0o600)
except OSError:
    pass

DISPLAY_SESSION_GENERATION_FILE = DATA_FOLDER / '.display_session_generation'
if DISPLAY_SESSION_GENERATION_FILE.exists():
    try:
        DISPLAY_SESSION_GENERATION = int(DISPLAY_SESSION_GENERATION_FILE.read_text().strip())
    except (OSError, ValueError):
        DISPLAY_SESSION_GENERATION = 1
else:
    DISPLAY_SESSION_GENERATION = 1
    DISPLAY_SESSION_GENERATION_FILE.write_text(str(DISPLAY_SESSION_GENERATION))
try:
    os.chmod(DISPLAY_SESSION_GENERATION_FILE, 0o600)
except OSError:
    pass

# Separate machine credential for the display-side CEC scheduling agent.
CEC_AGENT_TOKEN_FILE = DATA_FOLDER / '.cec_agent_token'
CEC_AGENT_TOKEN = os.environ.get('CEC_AGENT_TOKEN', '').strip()
if not CEC_AGENT_TOKEN:
    if CEC_AGENT_TOKEN_FILE.exists():
        CEC_AGENT_TOKEN = CEC_AGENT_TOKEN_FILE.read_text().strip()
    else:
        CEC_AGENT_TOKEN = secrets.token_urlsafe(32)
        CEC_AGENT_TOKEN_FILE.write_text(CEC_AGENT_TOKEN)
try:
    if CEC_AGENT_TOKEN_FILE.exists():
        os.chmod(CEC_AGENT_TOKEN_FILE, 0o600)
except OSError:
    pass

# In-memory device code store for QR enrollment flow
_device_codes = {}  # code -> {expires_at, approved, approved_by}
_DEVICE_CODE_TTL = 300  # 5 minutes
_BEHIND_PROXY = os.environ.get('BEHIND_PROXY', '').lower() in ('1', 'true')


def _parse_authelia_user_map(value):
    """Parse ``external_user=local_user`` pairs from the environment."""
    mapping = {}
    for pair in (value or '').split(','):
        if '=' not in pair:
            continue
        external, local = (part.strip().lower() for part in pair.split('=', 1))
        if external and local:
            mapping[external] = local
    return mapping


def _parse_authelia_groups(value):
    """Parse a comma-separated Authelia group list."""
    return {group.strip().lower() for group in (value or '').split(',') if group.strip()}


# Authelia is intentionally opt-in. It is safe to trust these headers only when
# Flask is reachable exclusively through the configured trusted reverse proxy.
AUTHELIA_URL = os.environ.get('AUTHELIA_URL', '').strip().rstrip('/')
AUTHELIA_USER_MAP = _parse_authelia_user_map(os.environ.get('AUTHELIA_USER_MAP', ''))
AUTHELIA_ADMIN_GROUPS = _parse_authelia_groups(
    os.environ.get('AUTHELIA_ADMIN_GROUPS', 'admins')
)

def _cleanup_device_codes():
    now = time.time()
    expired = [k for k, v in list(_device_codes.items()) if v['expires_at'] < now]
    for k in expired:
        del _device_codes[k]


# Session cookie security
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
# Enable Secure flag when behind HTTPS (set env var SECURE_COOKIES=1)
if os.environ.get('SECURE_COOKIES', '').lower() in ('1', 'true'):
    app.config['SESSION_COOKIE_SECURE'] = True
app.config['DISPLAY_SESSION_LIFETIME'] = int(
    os.environ.get('DISPLAY_SESSION_LIFETIME_SECONDS', 30 * 24 * 60 * 60)
)
app.config['HSTS_ENABLED'] = os.environ.get('ENABLE_HSTS', '').lower() in ('1', 'true')
app.config['TRUSTED_HTTPS_HOSTNAME'] = os.environ.get('TRUSTED_HTTPS_HOSTNAME', '').lower()

# Reverse proxy support — enable with BEHIND_PROXY=1
if os.environ.get('BEHIND_PROXY', '').lower() in ('1', 'true'):
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)


# ============ User Management ============

MIN_PASSWORD_LENGTH = 12


def log_security_event(event_type, success, username=None, **details):
    """Append a structured security event without accepting secret fields."""
    forbidden = {'password', 'secret', 'token', 'cookie', 'csrf', 'recovery_code'}
    safe_details = {
        key: value for key, value in details.items()
        if not any(word in key.lower() for word in forbidden)
    }
    event = {
        'timestamp': datetime.now().astimezone().isoformat(),
        'event_type': event_type,
        'username': username,
        'source_ip': request.remote_addr if has_request_context() else None,
        'status': 'success' if success else 'failure',
        **safe_details,
    }
    SECURITY_LOG_FILE.parent.mkdir(exist_ok=True)
    with open(SECURITY_LOG_FILE, 'a') as log_file:
        fcntl.flock(log_file.fileno(), fcntl.LOCK_EX)
        log_file.write(json.dumps(event, separators=(',', ':')) + '\n')
        log_file.flush()
        fcntl.flock(log_file.fileno(), fcntl.LOCK_UN)
    os.chmod(SECURITY_LOG_FILE, 0o600)


def validate_password(password):
    """Apply the single password policy used by every password-changing path."""
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH:
        return False, f'Password must be at least {MIN_PASSWORD_LENGTH} characters'
    return True, None


def _mfa_cipher():
    if not MFA_ENCRYPTION_KEY_FILE.exists():
        MFA_ENCRYPTION_KEY_FILE.write_bytes(Fernet.generate_key())
        os.chmod(MFA_ENCRYPTION_KEY_FILE, 0o600)
    return Fernet(MFA_ENCRYPTION_KEY_FILE.read_bytes().strip())


def _encrypt_mfa_secret(secret):
    return _mfa_cipher().encrypt(secret.encode()).decode()


def _decrypt_mfa_secret(encrypted):
    try:
        return _mfa_cipher().decrypt(encrypted.encode()).decode()
    except (InvalidToken, ValueError, TypeError):
        return None


def _b64url(value):
    return base64.urlsafe_b64encode(value).rstrip(b'=').decode()


def _b64url_bytes(value):
    return base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))


def _user_mfa(user):
    return user.setdefault('mfa', {'passkeys': [], 'recovery_code_hashes': []})


def user_mfa_methods(username):
    user = load_users().get(username, {})
    mfa = user.get('mfa', {})
    methods = []
    if mfa.get('totp_secret'):
        methods.append('totp')
    if mfa.get('passkeys'):
        methods.append('passkey')
    return methods


def mfa_required_for(username):
    settings = load_settings()
    mode = settings.get('mfa_mode', 'disabled')
    role = get_user_role(username)
    return mode == 'required_all' or (mode == 'required_admins' and role == 'admin')


def mfa_challenge_required(username):
    settings = load_settings()
    if settings.get('mfa_mode', 'disabled') == 'disabled':
        return False
    allowed = settings.get('mfa_methods', 'either')
    methods = user_mfa_methods(username)
    return any(method in methods for method in (
        ['totp'] if allowed == 'totp' else ['passkey'] if allowed == 'passkey' else ['totp', 'passkey']
    ))


def passkeys_available():
    settings = load_settings()
    origin = settings.get('webauthn_origin', '').strip()
    rp_id = settings.get('webauthn_rp_id', '').strip()
    parsed = urlparse(origin)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.hostname != rp_id:
        return False
    try:
        return not ip_address(rp_id).is_unspecified
    except ValueError:
        return rp_id != 'localhost' and '.' in rp_id

def hash_password(password: str, salt: str = None) -> tuple:
    """Hash a password using bcrypt.

    The salt parameter is accepted for backwards compatibility with legacy
    SHA-256 hashes but is ignored for new bcrypt hashes.
    """
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    return hashed, None


def _verify_legacy_sha256(password: str, stored_hash: str, salt: str) -> bool:
    """Verify a password against a legacy salted SHA-256 hash."""
    candidate = hashlib.sha256((salt + password).encode()).hexdigest()
    return secrets.compare_digest(candidate, stored_hash)


def _is_bcrypt_hash(stored_hash: str) -> bool:
    """Check if a stored hash is in bcrypt format."""
    return stored_hash.startswith('$2b$') or stored_hash.startswith('$2a$')


def load_users():
    """Load users, generating a random one-time administrator credential if needed."""
    if USERS_FILE.exists():
        with open(USERS_FILE, 'r') as f:
            users = json.load(f)
        # Safely migrate installations that still have the former fixed credential.
        admin = users.get('admin')
        if admin and 'must_change_password' not in admin:
            if (_is_bcrypt_hash(admin['password_hash'])
                    and bcrypt.checkpw(b'password', admin['password_hash'].encode())):
                admin['must_change_password'] = True
                save_users(users)
        return users

    initial_password = secrets.token_urlsafe(18)
    hashed, salt = hash_password(initial_password)
    users = {
        'admin': {
            'password_hash': hashed,
            'salt': salt,
            'role': 'admin',
            'created': datetime.now().isoformat(),
            'must_change_password': True,
        }
    }
    save_users(users)
    INITIAL_ADMIN_PASSWORD_FILE.write_text(initial_password)
    os.chmod(INITIAL_ADMIN_PASSWORD_FILE, 0o600)
    return users


def save_users(users):
    """Save users to JSON file"""
    with open(USERS_FILE, 'w') as f:
        json.dump(users, f, indent=2)
    os.chmod(USERS_FILE, 0o600)


def verify_user(username: str, password: str) -> bool:
    """Verify username and password, auto-migrating legacy SHA-256 hashes to bcrypt."""
    users = load_users()
    if username not in users:
        return False
    user = users[username]

    if _is_bcrypt_hash(user['password_hash']):
        if not bcrypt.checkpw(password.encode(), user['password_hash'].encode()):
            return False
    else:
        # Legacy salted SHA-256
        if not _verify_legacy_sha256(password, user['password_hash'], user.get('salt', '')):
            return False
        # Auto-migrate to bcrypt on successful login
        new_hash, _ = hash_password(password)
        user['password_hash'] = new_hash
        user['salt'] = None
        save_users(users)

    return True


def user_requires_password_change(username):
    """Check the explicit one-time/reset password-change marker."""
    users = load_users()
    if username not in users:
        return False
    return users[username].get('must_change_password', False)


# Backwards-compatible helper name for extensions; authorization uses the marker.
has_default_password = user_requires_password_change


def get_user_role(username: str) -> str:
    """Get user's role"""
    if (has_request_context()
            and session.get('auth_provider') == 'authelia'
            and session.get('username') == username):
        return session.get('auth_role', 'user')
    users = load_users()
    if username in users:
        return users[username].get('role', 'user')
    return None


def create_user(username: str, password: str, role: str = 'user') -> tuple:
    """Create a new user. Returns (success, message)"""
    users = load_users()

    if username in users:
        return False, 'Username already exists'

    if len(username) < 3:
        return False, 'Username must be at least 3 characters'

    valid, message = validate_password(password)
    if not valid:
        return False, message

    if role not in ['admin', 'user']:
        return False, 'Invalid role'

    hashed, salt = hash_password(password)
    users[username] = {
        'password_hash': hashed,
        'salt': salt,
        'role': role,
        'created': datetime.now().isoformat(),
        'must_change_password': False,
    }
    save_users(users)
    return True, 'User created successfully'


def delete_user(username: str) -> tuple:
    """Delete a user. Returns (success, message)"""
    users = load_users()

    if username not in users:
        return False, 'User not found'

    if username == 'admin':
        return False, 'Cannot delete the admin user'

    del users[username]
    save_users(users)
    return True, 'User deleted successfully'


def change_user_password(username: str, new_password: str, require_change=False) -> tuple:
    """Change a user's password. Returns (success, message)"""
    users = load_users()

    if username not in users:
        return False, 'User not found'

    valid, message = validate_password(new_password)
    if not valid:
        return False, message

    hashed, salt = hash_password(new_password)
    users[username]['password_hash'] = hashed
    users[username]['salt'] = salt
    users[username]['must_change_password'] = require_change
    save_users(users)
    return True, 'Password changed successfully'


# Ensure gunicorn/import-based startup creates the one-time bootstrap credential.
load_users()


# ============ Authentication Decorators ============

def is_authenticated():
    """Check if current session is authenticated"""
    if _BEHIND_PROXY and AUTHELIA_URL and session.get('auth_provider') != 'authelia':
        return False
    return session.get('authenticated', False)


def request_is_loopback():
    """Return whether the actual request source is a loopback IP address."""
    try:
        return ip_address(request.remote_addr).is_loopback
    except (ValueError, TypeError):
        return False


def has_valid_display_session():
    """Validate the signed display session, including expiry and rotation."""
    if not session.get('display_authenticated'):
        return False
    if session.get('display_session_generation') != DISPLAY_SESSION_GENERATION:
        return False
    authenticated_at = session.get('display_authenticated_at')
    if not isinstance(authenticated_at, (int, float)):
        return False
    lifetime = app.config['DISPLAY_SESSION_LIFETIME']
    return 0 <= time.time() - authenticated_at <= lifetime


def display_access_ok():
    """Authorize display reads without trusting hostnames or URL credentials."""
    return request_is_loopback() or has_valid_display_session() or is_authenticated()


def authelia_enabled():
    """Return whether the application should use the proxy's Authelia identity."""
    return _BEHIND_PROXY and bool(AUTHELIA_URL)


def authelia_identity():
    """Return the mapped local identity supplied by a trusted Authelia proxy."""
    if not authelia_enabled():
        return None

    external_username = request.headers.get('Remote-User', '').strip().lower()
    if not external_username:
        return None

    local_username = AUTHELIA_USER_MAP.get(external_username, external_username)
    if local_username not in load_users():
        return None
    return external_username, local_username


def authelia_request_groups():
    """Return the groups asserted by the trusted Authelia forward-auth response."""
    return _parse_authelia_groups(request.headers.get('Remote-Groups', ''))


def authelia_redirect_url(path='/upload'):
    """Build an Authelia portal URL with a safe same-host return destination."""
    if not isinstance(path, str) or not path.startswith('/') or path.startswith('//'):
        path = '/upload'
    return f'{AUTHELIA_URL}/?{urlencode({"rd": request.host_url.rstrip("/") + path, "rm": "GET"})}'


@app.before_request
def establish_authelia_session():
    """Bridge a successful Authelia check into the app's signed session cookie."""
    if not authelia_enabled():
        return None

    external_identity = request.headers.get('Remote-User', '').strip().lower()
    if not external_identity:
        return None

    identity = authelia_identity()
    if identity is None:
        # An authenticated proxy identity without a local account must not fall
        # through to the local login form or create an unintended account.
        return jsonify({'error': 'Authelia identity is not mapped to an application user'}), 403

    external_username, local_username = identity
    groups = authelia_request_groups()
    auth_role = 'admin' if groups.intersection(AUTHELIA_ADMIN_GROUPS) else 'user'
    if (session.get('auth_provider') == 'authelia'
            and session.get('username') == local_username):
        session['authelia_groups'] = sorted(groups)
        session['auth_role'] = auth_role
        return None

    session.clear()
    session.permanent = True
    session['authenticated'] = True
    session['username'] = local_username
    session['auth_provider'] = 'authelia'
    session['authelia_groups'] = sorted(groups)
    session['auth_role'] = auth_role
    log_security_event('authelia_login', True, username=local_username,
                       external_username=external_username)
    return None


def display_api_required(f):
    """Require a user/display session or an intentionally supported loopback source."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not display_access_ok():
            return jsonify({'error': 'Authentication required'}), 401
        return f(*args, **kwargs)
    return decorated_function


def cec_agent_authenticated():
    """Authenticate the CEC machine agent exclusively with a Bearer token."""
    authorization = request.headers.get('Authorization', '')
    scheme, separator, credential = authorization.partition(' ')
    if separator != ' ' or scheme != 'Bearer' or not credential or ' ' in credential:
        return False
    return secrets.compare_digest(credential, CEC_AGENT_TOKEN)


def cec_agent_required(f):
    """Return 401 unless the request has the dedicated CEC bearer credential."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not cec_agent_authenticated():
            log_security_event('cec_authentication', False)
            return jsonify({'error': 'Authentication required'}), 401
        return f(*args, **kwargs)
    return decorated_function


def is_admin():
    """Check if current user is admin"""
    if not is_authenticated():
        return False
    username = session.get('username')
    return get_user_role(username) == 'admin'


def login_required(f):
    """Decorator to require authentication"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not is_authenticated():
            return redirect(url_for('login', next=request.path))
        if (not (authelia_enabled() and session.get('auth_provider') == 'authelia')
                and has_default_password(session.get('username'))
                and request.path != '/change-password'):
            return redirect(url_for('change_password', forced=1))
        if session.get('mfa_enrollment_required') and not request.path.startswith('/mfa'):
            return redirect(url_for('mfa_page'))
        return f(*args, **kwargs)
    return decorated_function


def admin_required(f):
    """Decorator to require admin role"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not is_authenticated():
            return redirect(url_for('login', next=request.path))
        if not is_admin():
            return render_template('error.html', message='Admin access required'), 403
        return f(*args, **kwargs)
    return decorated_function


def api_login_required(f):
    """Decorator for API endpoints - returns 401 instead of redirect"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not is_authenticated():
            return jsonify({'error': 'Authentication required'}), 401
        return f(*args, **kwargs)
    return decorated_function


def api_admin_required(f):
    """Decorator for admin API endpoints"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not is_authenticated():
            return jsonify({'error': 'Authentication required'}), 401
        if not is_admin():
            return jsonify({'error': 'Admin access required'}), 403
        return f(*args, **kwargs)
    return decorated_function


# ============ Gallery Management ============

def load_gallery():
    """Load gallery metadata"""
    if GALLERY_FILE.exists():
        with open(GALLERY_FILE, 'r') as f:
            data = json.load(f)
            if 'groups' not in data:
                data['groups'] = {}
            return data
    return {'images': {}, 'groups': {}}


def save_gallery(gallery):
    """Save gallery metadata"""
    with open(GALLERY_FILE, 'w') as f:
        json.dump(gallery, f, indent=2)


def get_image_metadata(filename):
    """Get metadata for a single image"""
    gallery = load_gallery()
    return gallery['images'].get(filename, {
        'enabled': True,
        'title': '',
        'uploaded_at': None,
        'uploaded_by': None,
        'width': None,
        'height': None,
        'mat_color': None,
        'phash': None,
        'scale': 1.0,
        'mat_finish': None,
        'bevel_width': None,
        'border_effect': None,
        'crop': None
    })


def update_image_metadata(filename, **kwargs):
    """Update metadata for an image"""
    gallery = load_gallery()
    if filename not in gallery['images']:
        gallery['images'][filename] = {
            'enabled': True,
            'title': '',
            'uploaded_at': None,
            'uploaded_by': None,
            'width': None,
            'height': None,
            'mat_color': None,
            'phash': None,
            'scale': 1.0,
            'mat_finish': None,
            'bevel_width': None,
            'border_effect': None,
            'crop': None
        }
    gallery['images'][filename].update(kwargs)
    save_gallery(gallery)


def remove_image_metadata(filename):
    """Remove metadata for an image"""
    gallery = load_gallery()
    if filename in gallery['images']:
        del gallery['images'][filename]
        save_gallery(gallery)


def get_grouped_filenames():
    """Get set of all filenames that belong to a group"""
    gallery = load_gallery()
    grouped = set()
    for group in gallery['groups'].values():
        grouped.update(group.get('images', []))
    return grouped


def remove_filename_from_groups(filename):
    """Remove a filename from any groups it belongs to, delete empty groups"""
    gallery = load_gallery()
    groups_to_delete = []
    for group_id, group in gallery['groups'].items():
        if filename in group.get('images', []):
            group['images'].remove(filename)
            if len(group['images']) < 2:
                groups_to_delete.append(group_id)
    for gid in groups_to_delete:
        del gallery['groups'][gid]
    if groups_to_delete or any(filename in g.get('images', []) for g in gallery['groups'].values()):
        save_gallery(gallery)


def allowed_file(filename):
    """Check if file extension is allowed"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def validate_image(filepath):
    """Fully decode an image before hashing or any other transformation."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(filepath) as image:
                image.verify()
            with Image.open(filepath) as image:
                image.load()
                width, height = ImageOps.exif_transpose(image).size
        if (width > MAX_IMAGE_DIMENSION or height > MAX_IMAGE_DIMENSION
                or width * height > MAX_IMAGE_PIXELS):
            raise ValueError(
                f'Image dimensions exceed the {MAX_IMAGE_DIMENSION}px / '
                f'{MAX_IMAGE_PIXELS:,}-pixel safety limit'
            )
        return width, height
    except (Image.DecompressionBombWarning, Image.DecompressionBombError):
        raise ValueError('Image is too large to decode safely') from None
    except (UnidentifiedImageError, OSError, SyntaxError):
        raise ValueError('File is not a valid supported image') from None


def compute_phash(filepath):
    """Compute perceptual hash of an image file. Returns hex string or None."""
    try:
        with Image.open(filepath) as img:
            oriented = ImageOps.exif_transpose(img)
            return str(imagehash.phash(oriented))
    except Exception:
        return None


def generate_thumbnail(filepath, filename):
    """Generate a thumbnail for an image. Returns True on success."""
    thumb_path = THUMBNAIL_FOLDER / filename
    if thumb_path.exists():
        return True
    try:
        with Image.open(filepath) as img:
            oriented = ImageOps.exif_transpose(img)
            oriented.thumbnail(THUMBNAIL_MAX_SIZE, Image.Resampling.LANCZOS)
            # Save as JPEG for smaller file size (unless PNG with transparency)
            if oriented.mode in ('RGBA', 'LA', 'P'):
                # PNG doesn't accept quality param; use optimize for smaller size
                oriented.save(thumb_path, 'PNG', optimize=True)
            else:
                thumb_path = thumb_path.with_suffix('.jpg')
                oriented = oriented.convert('RGB')
                oriented.save(thumb_path, 'JPEG', quality=85)
        return True
    except Exception:
        return False


def suggest_mat_color(filepath):
    """Return a hex mat color derived from the image (brightness-aware + edge-neutral hybrid)."""
    try:
        with Image.open(filepath) as img:
            img = ImageOps.exif_transpose(img)
            img = img.convert('RGB')
            img.thumbnail((150, 150), Image.Resampling.NEAREST)
            w, h = img.size

            # Overall brightness
            gray_pixels = list(img.convert('L').getdata())
            brightness = sum(gray_pixels) / len(gray_pixels)  # 0-255

            # Sample outer edge strip (~15% on each side) for dominant hue/warmth
            edge = max(1, min(15, w // 7, h // 7))
            all_pixels = list(img.getdata())
            edge_pixels = [
                all_pixels[y * w + x]
                for y in range(h)
                for x in range(w)
                if x < edge or x >= w - edge or y < edge or y >= h - edge
            ]
            if not edge_pixels:
                edge_pixels = all_pixels

            avg_r = sum(p[0] for p in edge_pixels) / len(edge_pixels)
            avg_g = sum(p[1] for p in edge_pixels) / len(edge_pixels)
            avg_b = sum(p[2] for p in edge_pixels) / len(edge_pixels)

            h_hls, l_hls, s_hls = colorsys.rgb_to_hls(avg_r / 255, avg_g / 255, avg_b / 255)

            # Map image brightness to mat lightness (inverted: bright image → dark mat)
            if brightness > 165:
                target_l = 0.20
            elif brightness > 110:
                target_l = 0.55
            else:
                target_l = 0.82

            # Heavily desaturate, preserving a subtle warmth/cool cast from edge colors
            target_s = min(s_hls * 0.25, 0.10)

            r, g, b = colorsys.hls_to_rgb(h_hls, target_l, target_s)
            return '#{:02x}{:02x}{:02x}'.format(int(r * 255), int(g * 255), int(b * 255))
    except Exception:
        return None


def backfill_thumbnails():
    """Generate thumbnails for any uploaded images missing them."""
    if not UPLOAD_FOLDER.exists():
        return 0
    count = 0
    for f in UPLOAD_FOLDER.iterdir():
        if f.is_file() and allowed_file(f.name):
            # Check both original extension and .jpg fallback
            thumb_path = THUMBNAIL_FOLDER / f.name
            thumb_jpg = thumb_path.with_suffix('.jpg')
            if not thumb_path.exists() and not thumb_jpg.exists():
                if generate_thumbnail(f, f.name):
                    count += 1
    return count


def backfill_mat_colors():
    """Suggest and store mat_color for any images that don't have one yet."""
    if not UPLOAD_FOLDER.exists():
        return 0
    if not load_settings().get('auto_mat_color', True):
        return 0
    gallery = load_gallery()
    count = 0
    for filename, meta in gallery.get('images', {}).items():
        if meta.get('mat_color') is None:
            filepath = UPLOAD_FOLDER / filename
            if filepath.exists():
                color = suggest_mat_color(filepath)
                if color:
                    update_image_metadata(filename, mat_color=color)
                    count += 1
    return count


def generate_display_snapshot(filename):
    """Generate a display snapshot for a single image."""
    try:
        gallery = load_gallery()
        settings = load_settings()
        return _render_snapshot(filename, gallery, settings, UPLOAD_FOLDER, SNAPSHOT_FOLDER)
    except Exception as e:
        logging.warning('Snapshot render failed for %s: %s', filename, e)
        return None


def generate_group_display_snapshot(group_id):
    """Generate a display snapshot for a group."""
    try:
        gallery = load_gallery()
        settings = load_settings()
        return _render_group_snapshot(group_id, gallery, settings, UPLOAD_FOLDER, SNAPSHOT_FOLDER)
    except Exception as e:
        logging.warning('Group snapshot render failed for %s: %s', group_id, e)
        return None


def regenerate_snapshots_for_groups_containing(filename):
    """Re-render snapshots for all groups that contain the given image."""
    gallery = load_gallery()
    for group_id in get_groups_containing(filename, gallery):
        generate_group_display_snapshot(group_id)


def regenerate_profile_snapshots_for_image(filename):
    """Refresh every active display profile after an image presentation change."""
    gallery = load_gallery()
    settings = load_settings()
    for profile in display_profiles(settings, active_only=True):
        profile_settings = display_settings(settings, profile)
        _render_snapshot(filename, gallery, profile_settings, UPLOAD_FOLDER, SNAPSHOT_FOLDER)
        for group_id in get_groups_containing(filename, gallery):
            _render_group_snapshot(group_id, gallery, profile_settings, UPLOAD_FOLDER, SNAPSHOT_FOLDER)


def _get_slide_snapshot_url(slide):
    """Return the /snapshots/ URL for a slide if its snapshot file exists, else None."""
    if slide is None:
        return None
    if slide['type'] == 'group':
        group_id = slide.get('group_id')
        if not group_id:
            return None
        snap_path = SNAPSHOT_FOLDER / f'{group_id}.display.png'
    else:
        filename = slide['images'][0]['filename']
        snap_path = SNAPSHOT_FOLDER / f'{filename}.display.png'
    if not snap_path.exists():
        return None
    # Change the URL whenever a snapshot is regenerated so displays reload it.
    return f'/snapshots/{snap_path.name}?v={snap_path.stat().st_mtime_ns}'


def get_uploaded_images():
    """Get list of all uploaded images with metadata"""
    if not UPLOAD_FOLDER.exists():
        return []

    gallery = load_gallery()
    images = []

    for f in sorted(UPLOAD_FOLDER.iterdir()):
        if f.is_file() and allowed_file(f.name):
            meta = gallery['images'].get(f.name, {
                'enabled': True,
                'title': '',
                'uploaded_at': None,
                'uploaded_by': None,
                'width': None,
                'height': None,
                'mat_color': None,
                'phash': None,
                'scale': 1.0,
                'mat_finish': None,
                'bevel_width': None,
                'border_effect': None,
                'crop': None
            })
            images.append({
                'filename': f.name,
                'size': f.stat().st_size,
                'modified': datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
                **meta
            })

    return images


def get_enabled_images():
    """Get list of enabled images only (for display)"""
    return [img for img in get_uploaded_images() if img.get('enabled', True)]


# ============ Settings ============

DEFAULT_SETTINGS = {
    'mat_color': '#ffffff',
    'mat_finish': 'eggshell',
    'bevel_width': 4,
    'border_effect': 'bevel',
    'bevel_lit_intensity': 50,
    'bevel_lit_v': 50,
    'bevel_lit_h': 50,
    'slideshow_interval': 60,
    'transition_duration': 1,
    'fit_mode': 'contain',
    'auto_mat_color': True,
    'shuffle': False,
    'image_order': [],
    'target_aspect_ratio': '16:9',
    'tv_schedules': [],
    'max_backup_history': 30,
    'mfa_mode': 'disabled',
    'mfa_methods': 'either',
    'webauthn_rp_id': '',
    'webauthn_origin': '',
    'displays': [],
}


def _legacy_display(settings):
    """Translate the former single global target into a display profile."""
    aspect = settings.get("target_aspect_ratio", "16:9")
    width, height = {"16:9": (1920, 1080), "21:9": (2560, 1080), "4:3": (1600, 1200), "1:1": (1200, 1200)}.get(aspect, (1920, 1080))
    return {"id": "default", "name": "Main display", "active": True, "width": width, "height": height, "synchronized": True}

def display_profiles(settings, active_only=False):
    profiles = settings.get("displays") or [_legacy_display(settings)]
    profiles = [{**p, "content": validate_content(p.get("content", {}))} for p in profiles]
    return [p for p in profiles if p.get("active", True)] if active_only else profiles

def get_display_profile(settings, display_id=None):
    profiles = display_profiles(settings)
    if display_id:
        profile = next((p for p in profiles if p.get("id") == display_id), None)
        if profile:
            return profile
    return next((p for p in profiles if p.get("active", True)), profiles[0])

def display_settings(settings, profile):
    """Return settings for one render target without mutating global settings."""
    result = dict(settings)
    result.update(profile.get("settings", {}))
    result["screen_width"] = int(profile.get("width", 1920))
    result["screen_height"] = int(profile.get("height", 1080))
    result["_display_id"] = profile["id"]
    return result

def load_settings():
    """Load settings, exposing a migrated default display for legacy installs."""
    settings = {}
    if SETTINGS_FILE.exists():
        with open(SETTINGS_FILE, "r") as f:
            settings = json.load(f)
    settings = {**DEFAULT_SETTINGS, **settings}
    settings.setdefault("displays", [_legacy_display(settings)])
    return settings

def save_settings(settings):
    """Save settings to JSON file"""
    with open(SETTINGS_FILE, 'w') as f:
        json.dump(settings, f, indent=2)


# ============ Display State (server-controlled slideshow) ============

_display_state = {
    'index': 0,
    'paused': False,
    'last_advanced_at': time.time(),
}


def _get_effective_index(total_slides, state=None, settings=None):
    """Compute a profile slideshow index with lazy auto-advance."""
    if total_slides == 0:
        return 0
    state = state or _display_state
    if state["paused"] or total_slides <= 1:
        return state["index"] % total_slides
    settings = settings or load_settings()
    interval = settings.get("slideshow_interval", 10)
    elapsed = time.time() - state["last_advanced_at"]
    advances = int(elapsed / interval)
    return (state["index"] + advances) % total_slides


_independent_display_states = {}
_art_display_states = {}


def _art_state_response(profile):
    state = _art_display_states.setdefault(profile['id'], {'paused': False})
    return {"display": profile, "content_mode": "art", "paused": state['paused'],
            "index": 0, "total": 0, "snapshot_url": None,
            "art": resolve_art(profile['content'])}

def _profile_state(profile):
    if profile.get("synchronized", True):
        return _display_state
    return _independent_display_states.setdefault(profile["id"], {"index": 0, "paused": False, "last_advanced_at": time.time()})

def _profile_snapshot_url(slide, profile):
    if slide is None: return None
    base = slide.get("group_id") if slide.get("type") == "group" else slide["images"][0]["filename"]
    path = SNAPSHOT_FOLDER / f"{base}.{profile["id"]}.display.png"
    if not path.exists():
        settings = display_settings(load_settings(), profile)
        if slide.get("type") == "group":
            if base.startswith("__pair_"):
                # Auto-paired portrait slides are synthetic and have no entry in
                # gallery["groups"]. Render from the slide data itself.
                _render_pair_slide_snapshot(slide, settings, UPLOAD_FOLDER, SNAPSHOT_FOLDER)
            else:
                _render_group_snapshot(base, load_gallery(), settings, UPLOAD_FOLDER, SNAPSHOT_FOLDER)
        else:
            _render_snapshot(base, load_gallery(), settings, UPLOAD_FOLDER, SNAPSHOT_FOLDER)
    return f"/snapshots/{path.name}?v={path.stat().st_mtime_ns}" if path.exists() else None

# ============ Backup Management ============

backup_lock = threading.Lock()
backup_in_progress = False


def load_backup_log():
    """Load backup log from JSON file"""
    if BACKUP_LOG_FILE.exists():
        with open(BACKUP_LOG_FILE, 'r') as f:
            return json.load(f)
    return {'last_backup': None, 'last_result': None, 'last_error': None, 'history': []}


def save_backup_log(log_data):
    """Save backup log to JSON file"""
    max_history = load_settings().get('max_backup_history', 30)
    if len(log_data.get('history', [])) > max_history:
        log_data['history'] = log_data['history'][-max_history:]
    with open(BACKUP_LOG_FILE, 'w') as f:
        json.dump(log_data, f, indent=2)


def _files_changed_since(since_ts, *folders, exclude_names=None):
    """Return True if any tracked file under folders was modified after since_ts."""
    exclude_names = set(exclude_names or [])
    for folder in folders:
        folder = Path(folder)
        if not folder.exists():
            continue
        for path in folder.rglob('*'):
            if path.is_file() and path.name not in exclude_names:
                try:
                    if path.stat().st_mtime > since_ts:
                        return True
                except OSError:
                    pass
    return False


def is_backup_configured():
    """Check if rclone is configured for Dropbox"""
    if not RCLONE_CONFIG_FILE.exists():
        return False
    content = RCLONE_CONFIG_FILE.read_text()
    return '[dropbox]' in content


def generate_rclone_config(token):
    """Generate rclone.conf for Dropbox with the given token"""
    RCLONE_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    config_content = f"""[dropbox]
type = dropbox
token = {token}
"""
    RCLONE_CONFIG_FILE.write_text(config_content)
    os.chmod(RCLONE_CONFIG_FILE, 0o600)


def get_backup_settings():
    """Get backup-specific settings"""
    settings = load_settings()
    return {
        'backup_time': settings.get('backup_time', os.environ.get('BACKUP_TIME', '03:00')),
        'backup_path': settings.get('backup_path', 'PhotoFrameBackup'),
        'max_backup_history': settings.get('max_backup_history', 30),
    }


def run_backup():
    """Run rclone backup to Dropbox"""
    global backup_in_progress

    if not is_backup_configured():
        return {'success': False, 'error': 'Backup not configured'}

    # File-based lock to prevent concurrent runs across workers
    try:
        lock_fd = open(BACKUP_LOCK_FILE, 'w')
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (IOError, OSError):
        return {'success': False, 'error': 'Backup already in progress'}

    backup_in_progress = True
    start_time = datetime.now()
    log = load_backup_log()

    try:
        # Skip if nothing has changed since the last successful backup
        _data_excludes = {'.backup.lock', '.secret_key', '.display_token', 'users.json'}
        last_backup_str = log.get('last_backup')
        if log.get('last_result') == 'success' and last_backup_str:
            try:
                last_ts = datetime.fromisoformat(last_backup_str).timestamp()
                if not _files_changed_since(last_ts, UPLOAD_FOLDER) and \
                   not _files_changed_since(last_ts, DATA_FOLDER, exclude_names=_data_excludes):
                    log['history'].append({
                        'timestamp': start_time.isoformat(),
                        'result': 'no_changes',
                        'duration_seconds': 0
                    })
                    save_backup_log(log)
                    return {'success': True, 'no_changes': True}
            except (ValueError, OSError):
                pass  # Can't determine — proceed with backup

        backup_settings = get_backup_settings()
        remote_path = backup_settings['backup_path']
        config = str(RCLONE_CONFIG_FILE)

        # Sync uploads
        result1 = subprocess.run(
            ['rclone', 'sync', str(UPLOAD_FOLDER), f'dropbox:{remote_path}/uploads',
             '--config', config],
            capture_output=True, text=True, timeout=3600
        )

        # Sync data (excluding secrets, credentials, and lock files)
        result2 = subprocess.run(
            ['rclone', 'sync', str(DATA_FOLDER), f'dropbox:{remote_path}/data',
             '--config', config,
             '--exclude', 'rclone/**',
             '--exclude', '.backup.lock',
             '--exclude', '.secret_key',
             '--exclude', '.display_token',
             '--exclude', 'users.json'],
            capture_output=True, text=True, timeout=3600
        )

        duration = (datetime.now() - start_time).total_seconds()

        if result1.returncode != 0 or result2.returncode != 0:
            error_msg = (result1.stderr or '') + (result2.stderr or '')
            error_msg = error_msg.strip()[:500]
            log['last_backup'] = start_time.isoformat()
            log['last_result'] = 'error'
            log['last_error'] = error_msg
            log['history'].append({
                'timestamp': start_time.isoformat(),
                'result': 'error',
                'error': error_msg,
                'duration_seconds': round(duration, 1)
            })
            save_backup_log(log)
            return {'success': False, 'error': error_msg}

        log['last_backup'] = start_time.isoformat()
        log['last_result'] = 'success'
        log['last_error'] = None
        log['history'].append({
            'timestamp': start_time.isoformat(),
            'result': 'success',
            'duration_seconds': round(duration, 1)
        })
        save_backup_log(log)
        return {'success': True, 'duration_seconds': round(duration, 1)}

    except subprocess.TimeoutExpired:
        log['last_backup'] = start_time.isoformat()
        log['last_result'] = 'error'
        log['last_error'] = 'Backup timed out after 1 hour'
        log['history'].append({
            'timestamp': start_time.isoformat(),
            'result': 'error',
            'error': 'Backup timed out after 1 hour'
        })
        save_backup_log(log)
        return {'success': False, 'error': 'Backup timed out'}

    except Exception as e:
        error_msg = str(e)[:500]
        log['last_backup'] = start_time.isoformat()
        log['last_result'] = 'error'
        log['last_error'] = error_msg
        log['history'].append({
            'timestamp': start_time.isoformat(),
            'result': 'error',
            'error': error_msg
        })
        save_backup_log(log)
        return {'success': False, 'error': error_msg}

    finally:
        backup_in_progress = False
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_fd.close()
        except Exception:
            pass


def run_backup_async():
    """Run backup in a background thread"""
    thread = threading.Thread(target=run_backup, daemon=True)
    thread.start()


restore_in_progress = False


def run_restore():
    """Restore photos and data from Dropbox backup"""
    global restore_in_progress

    if not is_backup_configured():
        return {'success': False, 'error': 'Backup not configured'}

    # Reuse the same file lock to prevent backup and restore from running concurrently
    try:
        lock_fd = open(BACKUP_LOCK_FILE, 'w')
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (IOError, OSError):
        return {'success': False, 'error': 'A backup or restore is already in progress'}

    restore_in_progress = True
    start_time = datetime.now()

    try:
        backup_settings = get_backup_settings()
        remote_path = backup_settings['backup_path']
        config = str(RCLONE_CONFIG_FILE)

        # Restore uploads (use copy so we don't delete local files missing from remote)
        result1 = subprocess.run(
            ['rclone', 'copy', f'dropbox:{remote_path}/uploads', str(UPLOAD_FOLDER),
             '--config', config],
            capture_output=True, text=True, timeout=3600
        )

        # Restore data (excluding rclone config, lock file, secret key, and users)
        result2 = subprocess.run(
            ['rclone', 'copy', f'dropbox:{remote_path}/data', str(DATA_FOLDER),
             '--config', config,
             '--exclude', 'rclone/**',
             '--exclude', '.backup.lock',
             '--exclude', '.secret_key',
             '--exclude', 'users.json'],
            capture_output=True, text=True, timeout=3600
        )

        duration = (datetime.now() - start_time).total_seconds()

        if result1.returncode != 0 or result2.returncode != 0:
            error_msg = (result1.stderr or '') + (result2.stderr or '')
            return {'success': False, 'error': error_msg.strip()[:500]}

        return {'success': True, 'duration_seconds': round(duration, 1)}

    except subprocess.TimeoutExpired:
        return {'success': False, 'error': 'Restore timed out after 1 hour'}

    except Exception as e:
        return {'success': False, 'error': str(e)[:500]}

    finally:
        restore_in_progress = False
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_fd.close()
        except Exception:
            pass


def run_restore_async():
    """Run restore in a background thread"""
    thread = threading.Thread(target=run_restore, daemon=True)
    thread.start()


# ============ Backup Scheduler ============

scheduler = BackgroundScheduler(daemon=True)


def init_scheduler():
    """Initialize the daily backup scheduler"""
    backup_settings = get_backup_settings()
    backup_time = backup_settings['backup_time']
    try:
        hour, minute = map(int, backup_time.split(':'))
    except ValueError:
        hour, minute = 3, 0

    scheduler.add_job(
        run_backup,
        'cron',
        hour=hour,
        minute=minute,
        id='daily_backup',
        replace_existing=True,
        misfire_grace_time=3600
    )
    if not scheduler.running:
        scheduler.start()


def reschedule_backup(time_str):
    """Reschedule the daily backup to a new time"""
    try:
        hour, minute = map(int, time_str.split(':'))
    except ValueError:
        return

    if scheduler.get_job('daily_backup'):
        scheduler.reschedule_job(
            'daily_backup',
            trigger='cron',
            hour=hour,
            minute=minute
        )


# Start scheduler on module load
init_scheduler()


# ============ CEC TV Control ============

# In split backend/display mode, cec-ctl is not available on the server.
# Commands are queued here and picked up by the cec-agent running on the Pi.
_cec_queue = []           # pending commands waiting for a remote display to execute
_cec_display_has_cec = False  # True once a CEC-capable display has registered


def is_cec_available():
    """Check if CEC control is available — either locally or via a registered remote display."""
    if _cec_display_has_cec:
        return True
    try:
        result = subprocess.run(
            ['cec-ctl', '-d0', '--phys-addr'],
            capture_output=True, text=True, timeout=5
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def cec_send_command(command):
    """Send a CEC command. Runs locally if cec-ctl is available, otherwise queues
    for a remote display running the cec-agent (split backend/display mode)."""
    cec_args = {
        'on': ['cec-ctl', '-d0', '--playback', '--image-view-on'],
        'standby': ['cec-ctl', '-d0', '--playback', '--standby'],
    }
    if command not in cec_args:
        return {'success': False, 'error': f'Unknown command: {command}'}
    try:
        result = subprocess.run(
            cec_args[command], capture_output=True, text=True, timeout=10
        )
        return {'success': result.returncode == 0,
                'error': result.stderr.strip()[:200] if result.returncode != 0 else None}
    except FileNotFoundError:
        pass  # no local cec-ctl — fall through to queue
    except subprocess.TimeoutExpired:
        return {'success': False, 'error': 'CEC command timed out'}

    # No local cec-ctl available — queue for remote display agent
    _cec_queue.append(command)
    return {'success': True, 'queued': True}


def schedule_cec_jobs():
    """(Re-)schedule all CEC on/off jobs from settings."""
    for job in scheduler.get_jobs():
        if job.id.startswith('cec_'):
            scheduler.remove_job(job.id)

    settings = load_settings()
    for sched in settings.get('tv_schedules', []):
        if not sched.get('enabled', True):
            continue

        sched_id = sched.get('id', '')
        days = sched.get('days', [])
        if not days:
            continue

        day_of_week = ','.join(str(d) for d in sorted(days))

        on_h, on_m = map(int, sched['on_time'].split(':'))
        scheduler.add_job(
            cec_send_command, 'cron',
            args=['on'],
            day_of_week=day_of_week,
            hour=on_h, minute=on_m,
            id=f'cec_{sched_id}_on',
            replace_existing=True,
            misfire_grace_time=300
        )

        off_h, off_m = map(int, sched['off_time'].split(':'))
        scheduler.add_job(
            cec_send_command, 'cron',
            args=['standby'],
            day_of_week=day_of_week,
            hour=off_h, minute=off_m,
            id=f'cec_{sched_id}_off',
            replace_existing=True,
            misfire_grace_time=300
        )


schedule_cec_jobs()


# ============ Routes ============

# --- Authentication Routes ---

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Login page"""
    if is_authenticated():
        return redirect(url_for('upload_page'))

    if authelia_enabled():
        next_path = request.args.get('next', '') or request.form.get('next', '')
        return redirect(authelia_redirect_url(next_path))

    if request.method == 'POST':
        username = request.form.get('username', '').strip().lower()
        password = request.form.get('password', '')

        if verify_user(username, password):
            session.permanent = True
            if user_requires_password_change(username):
                session['authenticated'] = True
                session['username'] = username
                log_security_event('login', True, username=username)
                return redirect(url_for('change_password', forced=1))
            if mfa_challenge_required(username):
                session.clear()
                session['mfa_pending_username'] = username
                session.permanent = True
                return redirect(url_for('mfa_challenge_page'))
            session['authenticated'] = True
            session['username'] = username
            log_security_event('login', True, username=username)
            if mfa_required_for(username):
                session['mfa_enrollment_required'] = True
                return redirect(url_for('mfa_page'))
            next_url = request.form.get('next', '').strip()
            if next_url and next_url.startswith('/') and '//' not in next_url:
                return redirect(next_url)
            return redirect(url_for('upload_page'))

        log_security_event('login', False, username=username or None)
        return render_template('login.html', error='Invalid username or password')

    return render_template('login.html')


@app.route('/logout')
def logout():
    """Logout and clear session"""
    session.clear()
    if authelia_enabled():
        return redirect(f'{AUTHELIA_URL}/logout?{urlencode({"rd": request.host_url.rstrip("/") + "/login"})}')
    return redirect(url_for('login'))


@app.route('/change-password', methods=['GET', 'POST'])
@login_required
def change_password():
    """Change own password"""
    username = session.get('username')
    forced = user_requires_password_change(username)

    if request.method == 'POST':
        forced = user_requires_password_change(username)
        new_password = request.form.get('new_password', '')
        confirm = request.form.get('confirm', '')

        if not forced:
            current = request.form.get('current', '')
            if not verify_user(username, current):
                log_security_event('password_change', False, username=username)
                return render_template('change_password.html', error='Current password is incorrect',
                                       is_admin=is_admin(), username=username, forced=forced)

        if new_password != confirm:
            log_security_event('password_change', False, username=username)
            return render_template('change_password.html', error='New passwords do not match',
                                   is_admin=is_admin(), username=username, forced=forced)

        success, message = change_user_password(username, new_password)
        if success:
            log_security_event('password_change', True, username=username)
            if forced:
                if mfa_required_for(username) and not user_mfa_methods(username):
                    session['mfa_enrollment_required'] = True
                    return redirect(url_for('mfa_page'))
                return redirect(url_for('upload_page'))
            return render_template('change_password.html', success=message,
                                   is_admin=is_admin(), username=username, forced=False)
        log_security_event('password_change', False, username=username)
        return render_template('change_password.html', error=message,
                               is_admin=is_admin(), username=username, forced=forced)

    return render_template('change_password.html', is_admin=is_admin(),
                           username=username, forced=forced)


# --- Multi-factor authentication ---

def _generate_recovery_codes(user):
    codes = [f'{secrets.token_hex(3)}-{secrets.token_hex(3)}' for _ in range(10)]
    _user_mfa(user)['recovery_code_hashes'] = [hashlib.sha256(c.encode()).hexdigest() for c in codes]
    return codes


def _verify_totp_or_recovery(username, code):
    users = load_users()
    user = users.get(username)
    if not user:
        return False, None
    mfa = _user_mfa(user)
    code_hash = hashlib.sha256(code.strip().lower().encode()).hexdigest()
    for stored in list(mfa.get('recovery_code_hashes', [])):
        if secrets.compare_digest(code_hash, stored):
            mfa['recovery_code_hashes'].remove(stored)
            save_users(users)
            return True, 'recovery'
    secret = _decrypt_mfa_secret(mfa.get('totp_secret', ''))
    if not secret:
        return False, None
    totp = pyotp.TOTP(secret)
    current = totp.timecode(datetime.now())
    last = mfa.get('last_totp_counter', -1)
    for counter in range(current - 1, current + 2):
        if counter > last and secrets.compare_digest(totp.at(counter * totp.interval), code.strip()):
            mfa['last_totp_counter'] = counter
            save_users(users)
            return True, 'totp'
    return False, None


def _complete_mfa_login(username, method):
    session.clear()
    session['authenticated'] = True
    session['username'] = username
    session.permanent = True
    log_security_event('mfa_challenge', True, username=username, method=method)
    log_security_event('login', True, username=username, mfa_method=method)


@app.get('/mfa')
@login_required
def mfa_page():
    username = session.get('username')
    response = app.make_response(render_template(
        'mfa.html', methods=user_mfa_methods(username), required=mfa_required_for(username),
        passkeys_available=passkeys_available(), settings=load_settings(),
        is_admin=is_admin(), username=username,
    ))
    response.headers['Cache-Control'] = 'no-store, private'
    return response


@app.get('/mfa/challenge')
def mfa_challenge_page():
    username = session.get('mfa_pending_username')
    if not username:
        return redirect(url_for('login'))
    response = app.make_response(render_template(
        'mfa_challenge.html', methods=user_mfa_methods(username),
        passkeys_available=passkeys_available(),
    ))
    response.headers['Cache-Control'] = 'no-store, private'
    return response


@app.post('/api/mfa/challenge/totp')
def api_mfa_challenge_totp():
    username = session.get('mfa_pending_username')
    if not username:
        return jsonify({'error': 'MFA login is not pending'}), 401
    ok, method = _verify_totp_or_recovery(username, (request.json or {}).get('code', ''))
    if not ok:
        log_security_event('mfa_challenge', False, username=username, method='totp_or_recovery')
        return jsonify({'error': 'Invalid or previously used code'}), 401
    _complete_mfa_login(username, method)
    return jsonify({'success': True, 'redirect': url_for('upload_page')})


@app.post('/api/mfa/totp/start')
@api_login_required
def api_mfa_totp_start():
    if load_settings().get('mfa_methods') == 'passkey':
        return jsonify({'error': 'TOTP is disabled by policy'}), 403
    users = load_users()
    username = session['username']
    secret = pyotp.random_base32()
    _user_mfa(users[username])['pending_totp_secret'] = _encrypt_mfa_secret(secret)
    save_users(users)
    uri = pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name='OpenFotoFrame')
    response = jsonify({'secret': secret, 'provisioning_uri': uri})
    response.headers['Cache-Control'] = 'no-store, private'
    return response


@app.post('/api/mfa/totp/verify')
@api_login_required
def api_mfa_totp_verify():
    users = load_users()
    username = session['username']
    mfa = _user_mfa(users[username])
    secret = _decrypt_mfa_secret(mfa.get('pending_totp_secret', ''))
    code = (request.json or {}).get('code', '')
    if not secret or not pyotp.TOTP(secret).verify(code, valid_window=1):
        log_security_event('mfa_enrollment', False, username=username, method='totp')
        return jsonify({'error': 'Invalid verification code'}), 400
    mfa['totp_secret'] = _encrypt_mfa_secret(secret)
    mfa['last_totp_counter'] = pyotp.TOTP(secret).timecode(datetime.now())
    mfa.pop('pending_totp_secret', None)
    codes = _generate_recovery_codes(users[username])
    save_users(users)
    session.pop('mfa_enrollment_required', None)
    log_security_event('mfa_enrollment', True, username=username, method='totp')
    response = jsonify({'success': True, 'recovery_codes': codes})
    response.headers['Cache-Control'] = 'no-store, private'
    return response


@app.delete('/api/mfa/totp')
@api_login_required
def api_mfa_totp_remove():
    users = load_users()
    username = session['username']
    mfa = _user_mfa(users[username])
    if not mfa.get('totp_secret'):
        return jsonify({'error': 'TOTP is not enrolled'}), 404
    mfa.pop('totp_secret', None)
    mfa.pop('last_totp_counter', None)
    if not mfa.get('passkeys'):
        mfa['recovery_code_hashes'] = []
        if mfa_required_for(username):
            session['mfa_enrollment_required'] = True
    save_users(users)
    log_security_event('mfa_removal', True, username=username, method='totp')
    return jsonify({'success': True})


def _webauthn_settings():
    settings = load_settings()
    if not passkeys_available():
        return None
    return settings['webauthn_rp_id'], settings['webauthn_origin']


@app.post('/api/mfa/passkeys/register/options')
@api_login_required
def api_passkey_register_options():
    config = _webauthn_settings()
    if not config or load_settings().get('mfa_methods') == 'totp':
        return jsonify({'error': 'Passkey enrollment requires a configured stable HTTPS origin'}), 400
    username = session['username']
    user = load_users()[username]
    credentials = [PublicKeyCredentialDescriptor(id=_b64url_bytes(p['credential_id']))
                   for p in _user_mfa(user).get('passkeys', [])]
    options = generate_registration_options(
        rp_id=config[0], rp_name='OpenFotoFrame',
        user_id=hashlib.sha256(username.encode()).digest(), user_name=username,
        exclude_credentials=credentials,
    )
    session['webauthn_registration_challenge'] = _b64url(options.challenge)
    return jsonify(json.loads(options_to_json(options)))


@app.post('/api/mfa/passkeys/register/verify')
@api_login_required
def api_passkey_register_verify():
    config = _webauthn_settings()
    challenge = session.pop('webauthn_registration_challenge', None)
    if not config or not challenge:
        return jsonify({'error': 'Passkey registration is not pending'}), 400
    data = request.json or {}
    try:
        verified = verify_registration_response(
            credential=data.get('credential'), expected_challenge=_b64url_bytes(challenge),
            expected_rp_id=config[0], expected_origin=config[1], require_user_verification=True,
        )
    except Exception:
        log_security_event('mfa_enrollment', False, username=session['username'], method='passkey')
        return jsonify({'error': 'Passkey verification failed'}), 400
    users = load_users()
    user = users[session['username']]
    mfa = _user_mfa(user)
    mfa.setdefault('passkeys', []).append({
        'credential_id': _b64url(verified.credential_id),
        'public_key': _b64url(verified.credential_public_key),
        'sign_count': verified.sign_count,
        'name': (data.get('name') or 'Passkey')[:80],
        'created_at': datetime.now().astimezone().isoformat(),
        'last_used': None,
    })
    codes = _generate_recovery_codes(user) if not mfa.get('recovery_code_hashes') else None
    save_users(users)
    session.pop('mfa_enrollment_required', None)
    log_security_event('mfa_enrollment', True, username=session['username'], method='passkey')
    response = jsonify({'success': True, 'recovery_codes': codes})
    response.headers['Cache-Control'] = 'no-store, private'
    return response


@app.post('/api/mfa/passkeys/authenticate/options')
def api_passkey_authenticate_options():
    username = session.get('mfa_pending_username')
    config = _webauthn_settings()
    if not username or not config:
        return jsonify({'error': 'Passkey login is unavailable'}), 400
    passkeys = _user_mfa(load_users()[username]).get('passkeys', [])
    options = generate_authentication_options(
        rp_id=config[0],
        allow_credentials=[PublicKeyCredentialDescriptor(id=_b64url_bytes(p['credential_id'])) for p in passkeys],
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    session['webauthn_authentication_challenge'] = _b64url(options.challenge)
    return jsonify(json.loads(options_to_json(options)))


@app.post('/api/mfa/passkeys/authenticate/verify')
def api_passkey_authenticate_verify():
    username = session.get('mfa_pending_username')
    challenge = session.pop('webauthn_authentication_challenge', None)
    config = _webauthn_settings()
    data = request.json or {}
    users = load_users()
    passkey = next((p for p in _user_mfa(users.get(username, {})).get('passkeys', [])
                    if p['credential_id'] == data.get('credential', {}).get('id')), None)
    if not username or not challenge or not config or not passkey:
        return jsonify({'error': 'Passkey authentication is not pending'}), 400
    try:
        verified = verify_authentication_response(
            credential=data['credential'], expected_challenge=_b64url_bytes(challenge),
            expected_rp_id=config[0], expected_origin=config[1],
            credential_public_key=_b64url_bytes(passkey['public_key']),
            credential_current_sign_count=passkey['sign_count'], require_user_verification=True,
        )
    except Exception:
        log_security_event('mfa_challenge', False, username=username, method='passkey')
        return jsonify({'error': 'Passkey verification failed'}), 401
    passkey['sign_count'] = verified.new_sign_count
    passkey['last_used'] = datetime.now().astimezone().isoformat()
    save_users(users)
    _complete_mfa_login(username, 'passkey')
    return jsonify({'success': True, 'redirect': url_for('upload_page')})


@app.delete('/api/mfa/passkeys/<credential_id>')
@api_login_required
def api_remove_passkey(credential_id):
    users = load_users()
    username = session['username']
    passkeys = _user_mfa(users[username]).get('passkeys', [])
    remaining = [p for p in passkeys if p['credential_id'] != credential_id]
    if len(remaining) == len(passkeys):
        return jsonify({'error': 'Passkey not found'}), 404
    users[username]['mfa']['passkeys'] = remaining
    save_users(users)
    log_security_event('mfa_removal', True, username=username, method='passkey')
    return jsonify({'success': True})


@app.delete('/api/admin/users/<username>/mfa')
@api_admin_required
def api_admin_reset_mfa(username):
    users = load_users()
    if username not in users:
        return jsonify({'error': 'User not found'}), 404
    users[username].pop('mfa', None)
    save_users(users)
    log_security_event('mfa_admin_reset', True, username=session['username'], target_username=username)
    return jsonify({'success': True})


@app.route('/api/admin/security-settings', methods=['GET', 'POST'])
@api_admin_required
def api_security_settings():
    settings = load_settings()
    fields = ('mfa_mode', 'mfa_methods', 'webauthn_rp_id', 'webauthn_origin')
    if request.method == 'GET':
        return jsonify({field: settings.get(field) for field in fields} |
                       {'passkeys_available': passkeys_available()})
    data = request.json or {}
    if data.get('mfa_mode', settings['mfa_mode']) not in ('disabled', 'optional', 'required_admins', 'required_all'):
        return jsonify({'error': 'Invalid MFA mode'}), 400
    if data.get('mfa_methods', settings['mfa_methods']) not in ('totp', 'passkey', 'either'):
        return jsonify({'error': 'Invalid MFA method policy'}), 400
    for field in fields:
        if field in data:
            settings[field] = data[field].strip() if isinstance(data[field], str) else data[field]
    save_settings(settings)
    log_security_event('security_settings_change', True, username=session['username'])
    return jsonify({field: settings.get(field) for field in fields} |
                   {'passkeys_available': passkeys_available()})


# --- Admin Routes ---

@app.route('/admin')
@admin_required
def admin_page():
    users = load_users()
    user_list = [
        {'username': uname, 'role': udata.get('role', 'user'), 'created': udata.get('created_at', 'Unknown')}
        for uname, udata in users.items()
    ]
    settings = load_settings()
    return render_template('admin.html',
                           users=user_list,
                           username=session.get('username'),
                           settings=settings)


@app.route('/admin/users')
@admin_required
def admin_users():
    """User management page"""
    users = load_users()
    user_list = [
        {'username': u, 'role': d['role'], 'created': d.get('created', 'Unknown')}
        for u, d in users.items()
    ]
    return render_template('admin_users.html', users=user_list, is_admin=True, username=session.get('username'))


@app.route('/api/admin/users', methods=['POST'])
@api_admin_required
def api_create_user():
    """Create a new user"""
    data = request.json
    username = data.get('username', '').strip().lower()
    password = data.get('password', '')
    role = data.get('role', 'user')

    success, message = create_user(username, password, role)
    log_security_event('user_creation', success, username=session.get('username'),
                       target_username=username, target_role=role)
    if success:
        return jsonify({'success': True, 'message': message})
    return jsonify({'error': message}), 400


@app.route('/api/admin/users/<username>', methods=['DELETE'])
@api_admin_required
def api_delete_user(username):
    """Delete a user"""
    success, message = delete_user(username)
    log_security_event('user_deletion', success, username=session.get('username'),
                       target_username=username)
    if success:
        return jsonify({'success': True, 'message': message})
    return jsonify({'error': message}), 400


@app.route('/api/admin/users/<username>/password', methods=['POST'])
@api_admin_required
def api_reset_password(username):
    """Reset a user's password (admin only)"""
    data = request.json
    new_password = data.get('password', '')

    success, message = change_user_password(username, new_password, require_change=True)
    log_security_event('password_reset', success, username=session.get('username'),
                       target_username=username)
    if success:
        return jsonify({'success': True, 'message': message})
    return jsonify({'error': message}), 400


# --- Main Routes ---

@app.route('/')
def index():
    """Redirect to upload page"""
    return redirect(url_for('upload_page'))


@app.route('/upload')
@login_required
def upload_page():
    """Render the upload interface"""
    settings = load_settings()
    return render_template('upload.html',
                          settings=settings,
                          is_admin=is_admin(),
                          username=session.get('username'),
                          password_changed=not has_default_password(session.get('username')))


@app.route('/gallery')
@login_required
def gallery_page():
    """Redirect to combined upload/gallery page"""
    return redirect(url_for('upload_page'))


@app.route('/display')
def display_page():
    """Render the TV display page"""
    if display_access_ok():
        settings = load_settings()
        return render_template('display.html', settings=settings)

    return redirect(url_for('display_enroll_page'))


@app.get('/display/enroll')
def display_enroll_page():
    """Render the one-time display enrollment form without exposing a secret."""
    if display_access_ok():
        return redirect(url_for('display_page'))
    response = app.make_response(render_template('display_enroll.html'))
    response.headers['Cache-Control'] = 'no-store, private'
    return response


@app.post('/api/display/enroll')
@csrf.exempt
def api_display_enroll():
    """Exchange an enrollment secret from a POST body for a display session."""
    if request.args.get('token') is not None or request.args.get('secret') is not None:
        response = jsonify({'error': 'Credentials are not accepted in query parameters'})
        response.status_code = 400
        response.headers['Cache-Control'] = 'no-store, private'
        return response

    data = request.get_json(silent=True) if request.is_json else request.form
    submitted_secret = (data or {}).get('enrollment_secret', '')
    if not submitted_secret or not secrets.compare_digest(submitted_secret, DISPLAY_TOKEN):
        log_security_event('display_enrollment', False)
        response = jsonify({'error': 'Invalid enrollment secret'})
        response.status_code = 401
        response.headers['Cache-Control'] = 'no-store, private'
        return response

    session['display_authenticated'] = True
    session['display_authenticated_at'] = int(time.time())
    session['display_session_generation'] = DISPLAY_SESSION_GENERATION
    session.permanent = True
    log_security_event('display_enrollment', True)

    if request.is_json:
        response = jsonify({'success': True, 'display_url': url_for('display_page')})
    else:
        response = redirect(url_for('display_page'))
    response.headers['Cache-Control'] = 'no-store, private'
    return response


@app.get('/api/display/device-code')
@csrf.exempt
def api_display_device_code():
    """Generate a device code and return its QR data URL for the enrollment page."""
    _cleanup_device_codes()
    code = secrets.token_urlsafe(16)
    _device_codes[code] = {
        'expires_at': time.time() + _DEVICE_CODE_TTL,
        'approved': False,
        'approved_by': None,
    }
    approve_url = url_for('display_approve_page', code=code, _external=True)
    factory = qrcode.image.svg.SvgPathImage
    img = qrcode.make(approve_url, image_factory=factory)
    svg_io = io.BytesIO()
    img.save(svg_io)
    qr_data_url = 'data:image/svg+xml;base64,' + base64.b64encode(svg_io.getvalue()).decode('ascii')
    return jsonify({
        'code': code,
        'approve_url': approve_url,
        'qr_data_url': qr_data_url,
        'expires_in': _DEVICE_CODE_TTL,
    })


@app.get('/api/display/device-code/status/<code>')
@csrf.exempt
def api_display_device_code_status(code):
    """Pi polls here. Sets display session and returns redirect URL when approved."""
    if code not in _device_codes:
        return jsonify({'status': 'invalid'}), 404
    entry = _device_codes[code]
    if entry['expires_at'] < time.time():
        del _device_codes[code]
        return jsonify({'status': 'expired'}), 410
    if entry['approved']:
        del _device_codes[code]
        session['display_authenticated'] = True
        session['display_authenticated_at'] = int(time.time())
        session['display_session_generation'] = DISPLAY_SESSION_GENERATION
        session.permanent = True
        log_security_event('display_enrollment', True)
        return jsonify({'status': 'approved', 'redirect': url_for('display_page')})
    return jsonify({'status': 'pending', 'expires_in': int(entry['expires_at'] - time.time())})


@app.get('/display/approve/<code>')
@login_required
def display_approve_page(code):
    """Authenticated user sees the approval prompt for a pending device code."""
    if code not in _device_codes or _device_codes[code]['expires_at'] < time.time():
        _device_codes.pop(code, None)
        return render_template('display_approve.html', error='This code has expired. The display will generate a new one automatically.')
    remaining = int(_device_codes[code]['expires_at'] - time.time())
    return render_template('display_approve.html', code=code, remaining=remaining,
                           approver=session.get('username'))


@app.post('/api/display/approve/<code>')
@login_required
def api_display_approve(code):
    """Authenticated user approves a pending device code."""
    if code not in _device_codes:
        return jsonify({'error': 'Invalid or expired code'}), 404
    if _device_codes[code]['expires_at'] < time.time():
        del _device_codes[code]
        return jsonify({'error': 'Code expired'}), 410
    if _device_codes[code]['approved']:
        return jsonify({'already': True}), 200
    _device_codes[code]['approved'] = True
    _device_codes[code]['approved_by'] = session.get('username', 'unknown')
    log_security_event('display_enrollment_approved', True, username=session.get('username'))
    return jsonify({'success': True})


# --- API Routes ---

@app.route('/api/upload', methods=['POST'])
@api_login_required
def api_upload():
    """Handle image uploads"""
    if 'files' not in request.files:
        return jsonify({'error': 'No files provided'}), 400

    files = request.files.getlist('files')
    uploaded = []
    errors = []

    username = session.get('username', 'unknown')

    for file in files:
        if file.filename == '':
            continue

        if file and allowed_file(file.filename):
            unique_name = f"{uuid.uuid4().hex[:8]}_{secure_filename(file.filename)}"
            filepath = UPLOAD_FOLDER / unique_name
            with tempfile.NamedTemporaryFile(delete=False, dir=UPLOAD_FOLDER, suffix='.upload') as tmp:
                file.save(tmp.name)
                tmp_path = Path(tmp.name)
            try:
                width, height = validate_image(tmp_path)
                os.replace(tmp_path, filepath)
            except ValueError as error:
                tmp_path.unlink(missing_ok=True)
                errors.append(f'{file.filename}: {error}')
                continue
            generate_thumbnail(filepath, unique_name)
            uploaded.append(unique_name)

            # Compute perceptual hash for duplicate detection
            phash = compute_phash(filepath)
            settings_for_upload = load_settings()
            mat_color = suggest_mat_color(filepath) if settings_for_upload.get('auto_mat_color', True) else None

            # Add metadata
            update_image_metadata(unique_name,
                enabled=True,
                title='',
                uploaded_at=datetime.now().isoformat(),
                uploaded_by=username,
                width=width,
                height=height,
                phash=phash,
                mat_color=mat_color
            )

            # Generate display snapshot with default settings
            generate_display_snapshot(unique_name)
        else:
            errors.append(f"Invalid file type: {file.filename}")

    response = jsonify({
        'uploaded': uploaded,
        'errors': errors,
        'total_images': len(get_uploaded_images())
    })
    if errors and not uploaded:
        response.status_code = 400
    return response


@app.route('/api/check-duplicates', methods=['POST'])
@api_login_required
def api_check_duplicates():
    """Check uploaded files for perceptual duplicates against existing gallery.

    Returns duplicate matches and dimension info per file so the client
    can present warnings before the actual upload.
    """
    if 'files' not in request.files:
        return jsonify({'error': 'No files provided'}), 400

    threshold = int(request.args.get('threshold', 10))
    files = request.files.getlist('files')
    gallery = load_gallery()

    results = {}
    validation_errors = []

    for file in files:
        if file.filename == '' or not allowed_file(file.filename):
            continue

        # Save to temp file to compute hash and dimensions
        with tempfile.NamedTemporaryFile(delete=False, suffix='.tmp') as tmp:
            file.save(tmp.name)
            tmp_path = tmp.name

        try:
            try:
                width, height = validate_image(tmp_path)
            except ValueError as error:
                validation_errors.append(f'{file.filename}: {error}')
                continue
            new_hash_str = compute_phash(tmp_path)

            matches = []
            if new_hash_str:
                new_hash = imagehash.hex_to_hash(new_hash_str)
                for existing_fname, meta in gallery.get('images', {}).items():
                    existing_hash = meta.get('phash')
                    if not existing_hash:
                        continue
                    distance = int(new_hash - imagehash.hex_to_hash(existing_hash))
                    if distance <= threshold:
                        matches.append({
                            'filename': existing_fname,
                            'distance': distance,
                            'uploaded_by': meta.get('uploaded_by', '')
                        })
                matches.sort(key=lambda m: m['distance'])

            results[file.filename] = {
                'phash': new_hash_str,
                'width': width,
                'height': height,
                'matches': matches
            }
        finally:
            os.unlink(tmp_path)

    response = jsonify({'results': results, 'errors': validation_errors})
    if validation_errors:
        response.status_code = 400
    return response


@app.route('/api/gallery/backfill-hashes', methods=['POST'])
@api_admin_required
def api_backfill_hashes():
    """Compute and store perceptual hashes for images that don't have one."""
    gallery = load_gallery()
    updated = 0

    for filename, meta in gallery['images'].items():
        if meta.get('phash'):
            continue
        filepath = UPLOAD_FOLDER / filename
        if not filepath.exists():
            continue
        phash = compute_phash(filepath)
        if phash:
            meta['phash'] = phash
            updated += 1

    save_gallery(gallery)
    return jsonify({'success': True, 'updated': updated})


@app.route('/api/gallery/backfill-snapshots', methods=['POST'])
@api_admin_required
def api_backfill_snapshots():
    """Generate display snapshots for images and groups that don't have one."""
    gallery = load_gallery()
    settings = load_settings()
    count = _backfill_snapshots(gallery, settings, UPLOAD_FOLDER, SNAPSHOT_FOLDER)
    return jsonify({'success': True, 'generated': count})


def _is_portrait(img_data, threshold=1.15):
    """Return True if an image is portrait-oriented (height/width > threshold)."""
    w = img_data.get('width') or 0
    h = img_data.get('height') or 0
    return h > 0 and w > 0 and (h / w) > threshold


def _auto_pair_portraits(slides):
    """Pair all ungrouped portrait singles into synthetic side-by-side group slides.

    Collects every portrait single (regardless of adjacency), pairs them in order
    of their position in the shuffled list, and replaces the first portrait's slot
    with the pair. The second portrait's original slot is removed. Auto-pairs use
    a normalized effective scale so their rendered heights match; the original
    per-image scale values remain unchanged in the gallery.
    """
    portrait_indices = [
        idx for idx, slide in enumerate(slides)
        if slide['type'] == 'single' and _is_portrait(slide['images'][0])
    ]

    if len(portrait_indices) < 2:
        return slides

    paired_indices = set()
    pair_insertions = {}  # index of first portrait -> pair slide
    for k in range(0, len(portrait_indices) - 1, 2):
        i, j = portrait_indices[k], portrait_indices[k + 1]
        img1 = slides[i]['images'][0]
        img2 = slides[j]['images'][0]
        # Group-row rendering uses scale as a relative height multiplier. Reset
        # only the synthetic pair's effective scales so both photos share the
        # same height; their aspect ratios still determine their widths.
        pair_images = []
        for image in (img1, img2):
            pair_image = dict(image)
            pair_image['scale'] = 1.0
            pair_images.append(pair_image)
        pair_key = img1['filename'] + '|' + img2['filename']
        synthetic_id = '__pair_' + hashlib.sha1(pair_key.encode()).hexdigest()[:16]
        paired_indices.update([i, j])
        pair_insertions[i] = {
            'type': 'group',
            'group_id': synthetic_id,
            'images': pair_images,
            'mat_color': img1.get('mat_color') or img2.get('mat_color'),
        }

    result = []
    for idx, slide in enumerate(slides):
        if idx in pair_insertions:
            result.append(pair_insertions[idx])
        elif idx not in paired_indices:
            result.append(slide)
    return result


def _ensure_auto_pair_snapshots(slides, force=False):
    """Generate missing synthetic portrait-pair snapshots, or refresh all when forced.

    Runs in a background thread; safe to call on every state poll since it's a no-op
    when the profile-specific snapshot files already exist unless ``force`` is
    requested.
    """
    settings = load_settings()
    pair_slides = [s for s in slides
                   if s['type'] == 'group'
                   and s.get('group_id', '').startswith('__pair_')]
    for profile in display_profiles(settings, active_only=True):
        profile_settings = display_settings(settings, profile)
        for slide in pair_slides:
            path = SNAPSHOT_FOLDER / f"{slide['group_id']}.{profile['id']}.display.png"
            if not force and path.exists():
                continue
            try:
                _render_pair_slide_snapshot(slide, profile_settings,
                                            UPLOAD_FOLDER, SNAPSHOT_FOLDER)
            except Exception as e:
                logging.warning('Auto-pair snapshot failed for %s (%s): %s',
                                slide.get('group_id'), profile['id'], e)


def _build_slides():
    """Build the ordered slides list from gallery data and settings."""
    settings = load_settings()
    gallery = load_gallery()
    all_images = get_enabled_images()
    groups = gallery.get('groups', {})

    # Build lookup of image metadata by filename
    img_lookup = {}
    for img in all_images:
        img_lookup[img['filename']] = {
            'filename': img['filename'],
            'width': img.get('width'),
            'height': img.get('height'),
            'mat_color': img.get('mat_color'),
            'mat_finish': img.get('mat_finish'),
            'bevel_width': img.get('bevel_width'),
            'border_effect': img.get('border_effect'),
            'scale': img.get('scale', 1.0),
            'crop': img.get('crop'),
            'no_mat': img.get('no_mat'),
        }

    # Find which filenames are in groups
    grouped_filenames = set()
    for group in groups.values():
        grouped_filenames.update(group.get('images', []))

    # Build slides: groups first as encountered, then ungrouped singles
    slides = []

    # Add group slides (only if all images are enabled)
    for group_id, group in groups.items():
        group_images = []
        all_enabled = True
        scales = group.get('scales', {})
        for fname in group.get('images', []):
            if fname in img_lookup:
                img_entry = dict(img_lookup[fname])
                img_entry['scale'] = scales.get(fname, 1.0)
                group_images.append(img_entry)
            else:
                all_enabled = False
        if all_enabled and len(group_images) >= 2:
            slides.append({
                'type': 'group',
                'group_id': group_id,
                'images': group_images,
                'mat_color': group.get('mat_color'),
                'bevel_width': group.get('bevel_width'),
                'border_effect': group.get('border_effect'),
            })

    # Add ungrouped singles
    for img in all_images:
        if img['filename'] not in grouped_filenames:
            slides.append({
                'type': 'single',
                'images': [img_lookup[img['filename']]],
                'mat_color': img.get('mat_color')
            })

    # Server-side shuffle with daily seed so all display clients see the same order
    if settings.get('shuffle'):
        daily_seed = datetime.now().strftime('%Y-%m-%d')
        random.Random(daily_seed).shuffle(slides)

    # Auto-pair consecutive ungrouped portrait singles side-by-side
    slides = _auto_pair_portraits(slides)

    return slides, all_images, settings


@app.route('/api/images', methods=['GET'])
@display_api_required
def api_get_images():
    """Get slides for display (singles + groups)"""
    slides, all_images, settings = _build_slides()
    filenames = [img['filename'] for img in all_images]

    return jsonify({
        'images': filenames,
        'slides': slides,
        'settings': settings
    })


@app.route('/api/gallery', methods=['GET'])
@api_login_required
def api_get_gallery():
    """Get all images with full metadata for gallery management"""
    images = get_uploaded_images()
    gallery = load_gallery()
    return jsonify({'images': images, 'groups': gallery.get('groups', {})})


@app.route('/api/gallery/<filename>', methods=['PATCH'])
@api_login_required
def api_update_image(filename):
    """Update image metadata"""
    filepath = UPLOAD_FOLDER / secure_filename(filename)
    if not filepath.exists():
        return jsonify({'error': 'Image not found'}), 404

    data = request.json
    allowed_fields = ['enabled', 'title', 'mat_color', 'scale', 'mat_finish', 'bevel_width', 'border_effect', 'crop', 'no_mat']
    updates = {k: v for k, v in data.items() if k in allowed_fields}

    update_image_metadata(filename, **updates)

    # Re-render display snapshot if any display-affecting field changed
    display_fields = {'mat_color', 'scale', 'mat_finish', 'bevel_width', 'border_effect', 'crop', 'no_mat'}
    if display_fields & updates.keys():
        generate_display_snapshot(filename)
        regenerate_snapshots_for_groups_containing(filename)
        regenerate_profile_snapshots_for_image(filename)

    return jsonify({'success': True})


@app.route('/api/gallery/<filename>', methods=['DELETE'])
@api_login_required
def api_delete_image(filename):
    """Delete an image"""
    filepath = UPLOAD_FOLDER / secure_filename(filename)

    if filepath.exists():
        # Re-render group snapshots before removing from groups
        gallery = load_gallery()
        affected_groups = get_groups_containing(filename, gallery)

        filepath.unlink()
        # Clean up thumbnail (try both original extension and .jpg)
        thumb = THUMBNAIL_FOLDER / secure_filename(filename)
        thumb.unlink(missing_ok=True)
        thumb.with_suffix('.jpg').unlink(missing_ok=True)
        # Clean up display snapshot
        _delete_snapshot(filename, SNAPSHOT_FOLDER)
        remove_image_metadata(filename)
        remove_filename_from_groups(filename)

        # Re-render or delete affected group snapshots
        for group_id in affected_groups:
            gallery = load_gallery()
            group = gallery.get('groups', {}).get(group_id)
            if group and len(group.get('images', [])) >= 2:
                generate_group_display_snapshot(group_id)
            else:
                _delete_group_snapshot(group_id, SNAPSHOT_FOLDER)

        return jsonify({'success': True})

    return jsonify({'error': 'File not found'}), 404


@app.route('/api/gallery/bulk', methods=['POST'])
@api_login_required
def api_bulk_action():
    """Perform bulk actions on images"""
    data = request.json
    action = data.get('action')
    filenames = data.get('filenames', [])

    if not filenames:
        return jsonify({'error': 'No images selected'}), 400

    if action == 'enable':
        for f in filenames:
            update_image_metadata(f, enabled=True)
        return jsonify({'success': True, 'message': f'Enabled {len(filenames)} images'})

    elif action == 'disable':
        for f in filenames:
            update_image_metadata(f, enabled=False)
        return jsonify({'success': True, 'message': f'Disabled {len(filenames)} images'})

    elif action == 'delete':
        deleted = 0
        for f in filenames:
            filepath = UPLOAD_FOLDER / secure_filename(f)
            if filepath.exists():
                filepath.unlink()
                thumb = THUMBNAIL_FOLDER / secure_filename(f)
                thumb.unlink(missing_ok=True)
                thumb.with_suffix('.jpg').unlink(missing_ok=True)
                _delete_snapshot(f, SNAPSHOT_FOLDER)
                remove_image_metadata(f)
                remove_filename_from_groups(f)
                deleted += 1
        return jsonify({'success': True, 'message': f'Deleted {deleted} images'})

    return jsonify({'error': 'Invalid action'}), 400


# --- Group API Routes ---

@app.route('/api/groups', methods=['GET'])
@api_login_required
def api_get_groups():
    """Get all groups"""
    gallery = load_gallery()
    return jsonify({'groups': gallery.get('groups', {})})


@app.route('/api/groups', methods=['POST'])
@api_login_required
def api_create_group():
    """Create a new group from selected images"""
    data = request.json
    filenames = data.get('images', [])
    mat_color = data.get('mat_color')

    if len(filenames) < 2:
        return jsonify({'error': 'A group needs at least 2 images'}), 400

    gallery = load_gallery()
    group_id = f"group_{uuid.uuid4().hex[:8]}"
    gallery['groups'][group_id] = {
        'images': filenames,
        'mat_color': mat_color,
        'created_at': datetime.now().isoformat()
    }
    save_gallery(gallery)
    generate_group_display_snapshot(group_id)

    return jsonify({'success': True, 'group_id': group_id})


@app.route('/api/groups/<group_id>', methods=['PATCH'])
@api_login_required
def api_update_group(group_id):
    """Update a group (mat_color, images)"""
    gallery = load_gallery()
    if group_id not in gallery.get('groups', {}):
        return jsonify({'error': 'Group not found'}), 404

    data = request.json
    if 'mat_color' in data:
        gallery['groups'][group_id]['mat_color'] = data['mat_color']
    if 'mat_finish' in data:
        gallery['groups'][group_id]['mat_finish'] = data['mat_finish']
    if 'bevel_width' in data:
        gallery['groups'][group_id]['bevel_width'] = data['bevel_width']
    if 'border_effect' in data:
        gallery['groups'][group_id]['border_effect'] = data['border_effect']
    if 'images' in data:
        if len(data['images']) < 2:
            return jsonify({'error': 'A group needs at least 2 images'}), 400
        gallery['groups'][group_id]['images'] = data['images']
    if 'scales' in data:
        gallery['groups'][group_id]['scales'] = data['scales']

    save_gallery(gallery)
    generate_group_display_snapshot(group_id)
    return jsonify({'success': True})


@app.route('/api/groups/<group_id>', methods=['DELETE'])
@api_login_required
def api_delete_group(group_id):
    """Delete a group (ungroup images, they become individual)"""
    gallery = load_gallery()
    if group_id not in gallery.get('groups', {}):
        return jsonify({'error': 'Group not found'}), 404

    del gallery['groups'][group_id]
    save_gallery(gallery)
    _delete_group_snapshot(group_id, SNAPSHOT_FOLDER)
    return jsonify({'success': True})


@app.route('/api/settings', methods=['GET', 'POST'])
def api_settings():
    """Get or update settings"""
    if request.method == 'GET':
        if not display_access_ok():
            return jsonify({'error': 'Authentication required'}), 401
        return jsonify(load_settings())

    if not is_authenticated():
        return jsonify({'error': 'Authentication required'}), 401

    settings = load_settings()
    data = request.json
    prev_auto_mat = settings.get('auto_mat_color', True)

    allowed_fields = ['mat_color', 'mat_finish', 'bevel_width', 'border_effect',
                      'slideshow_interval', 'transition_duration',
                      'fit_mode', 'shuffle', 'image_order',
                      'target_aspect_ratio', 'auto_mat_color',
                      'bevel_lit_intensity', 'bevel_lit_v', 'bevel_lit_h']
    for field in allowed_fields:
        if field in data:
            settings[field] = data[field]

    save_settings(settings)

    # If auto-mat was just enabled, backfill images that don't have a mat color yet
    if data.get('auto_mat_color') is True and not prev_auto_mat:
        threading.Thread(target=backfill_mat_colors, daemon=True).start()

    # If display-affecting global settings changed, re-render all snapshots in background
    display_settings = {'mat_color', 'mat_finish', 'bevel_width', 'border_effect',
                        'fit_mode', 'target_aspect_ratio',
                        'bevel_lit_intensity', 'bevel_lit_v', 'bevel_lit_h'}
    if display_settings & data.keys():
        def _rerender_all():
            gallery = load_gallery()
            new_settings = load_settings()
            _regenerate_all_snapshots(gallery, new_settings, UPLOAD_FOLDER, SNAPSHOT_FOLDER)
            slides, _, _ = _build_slides()
            _ensure_auto_pair_snapshots(slides, force=True)
        thread = threading.Thread(target=_rerender_all, daemon=True)
        thread.start()

    return jsonify(settings)


@app.route('/api/reset-mat-colors', methods=['POST'])
@api_login_required
def api_reset_mat_colors():
    """Clear all per-image mat colors so they fall back to the global default."""
    gallery = load_gallery()
    count = 0
    for filename, meta in gallery.get('images', {}).items():
        if meta.get('mat_color') is not None:
            meta['mat_color'] = None
            count += 1
    if count:
        save_gallery(gallery)
        # Re-render snapshots in background
        def _rerender():
            g = load_gallery()
            s = load_settings()
            _regenerate_all_snapshots(g, s, UPLOAD_FOLDER, SNAPSHOT_FOLDER)
        threading.Thread(target=_rerender, daemon=True).start()
    return jsonify({'reset': count})


@app.route('/api/reorder', methods=['POST'])
@api_login_required
def api_reorder():
    """Reorder images"""
    data = request.json
    if 'images' not in data:
        return jsonify({'error': 'No image order provided'}), 400

    settings = load_settings()
    settings['image_order'] = data['images']
    save_settings(settings)

    return jsonify({'success': True})


@app.route('/uploads/<filename>')
@display_api_required
def serve_upload(filename):
    """Serve uploaded images (only files tracked in gallery metadata)"""
    gallery = load_gallery()
    if filename not in gallery.get('images', {}):
        return jsonify({'error': 'Not found'}), 404
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


@app.route('/thumbnails/<filename>')
@display_api_required
def serve_thumbnail(filename):
    """Serve image thumbnails (falls back to full image if no thumbnail)."""
    # Try exact filename first, then .jpg variant
    thumb_path = THUMBNAIL_FOLDER / filename
    if thumb_path.exists():
        return send_from_directory(THUMBNAIL_FOLDER, filename)
    jpg_name = Path(filename).with_suffix('.jpg').name
    jpg_path = THUMBNAIL_FOLDER / jpg_name
    if jpg_path.exists():
        return send_from_directory(THUMBNAIL_FOLDER, jpg_name)
    # Fall back to full image
    gallery = load_gallery()
    if filename not in gallery.get('images', {}):
        return jsonify({'error': 'Not found'}), 404
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


@app.route('/snapshots/<filename>')
@display_api_required
def serve_snapshot(filename):
    """Serve pre-rendered display snapshots (PNG files baked by render_display.py)."""
    if not filename.endswith('.display.png'):
        return jsonify({'error': 'Not found'}), 404
    snap_path = SNAPSHOT_FOLDER / filename
    if not snap_path.exists():
        return jsonify({'error': 'Not found'}), 404
    response = send_from_directory(SNAPSHOT_FOLDER, filename)
    response.headers['Cache-Control'] = 'public, max-age=300'
    return response


@app.get('/api/display/enrollment-secret')
@api_admin_required
def api_display_enrollment_secret():
    """Return the enrollment secret to an administrator for device setup."""
    response = jsonify({'enrollment_secret': DISPLAY_TOKEN})
    response.headers['Cache-Control'] = 'no-store, private'
    return response


@app.post('/api/display/rotate-secret')
@api_admin_required
def api_rotate_display_secret():
    """Rotate enrollment access and immediately invalidate display sessions."""
    global DISPLAY_TOKEN, DISPLAY_SESSION_GENERATION
    DISPLAY_TOKEN = secrets.token_urlsafe(32)
    DISPLAY_TOKEN_FILE.write_text(DISPLAY_TOKEN)
    os.chmod(DISPLAY_TOKEN_FILE, 0o600)
    DISPLAY_SESSION_GENERATION += 1
    DISPLAY_SESSION_GENERATION_FILE.write_text(str(DISPLAY_SESSION_GENERATION))
    os.chmod(DISPLAY_SESSION_GENERATION_FILE, 0o600)
    log_security_event('display_credential_rotation', True, username=session.get('username'))
    response = jsonify({
        'success': True,
        'enrollment_secret': DISPLAY_TOKEN,
        'existing_display_sessions_invalidated': True,
    })
    response.headers['Cache-Control'] = 'no-store, private'
    return response


@app.route('/api/artworks')
@display_api_required
def api_artworks():
    return jsonify({'artworks': artwork_catalog()})


@app.route("/api/displays", methods=["GET", "POST"])
@api_login_required
def api_displays():
    settings = load_settings(); profiles = display_profiles(settings)
    if request.method == "GET": return jsonify({"displays": profiles})
    data = request.json or {}
    try: width, height = int(data.get("width", 1920)), int(data.get("height", 1080))
    except (TypeError, ValueError): return jsonify({"error": "Width and height must be numbers"}), 400
    if not 100 <= width <= 10000 or not 100 <= height <= 10000: return jsonify({"error": "Invalid display dimensions"}), 400
    profile = {"id": uuid.uuid4().hex[:12], "name": str(data.get("name") or "New display")[:80], "active": bool(data.get("active", True)), "width": width, "height": height, "synchronized": bool(data.get("synchronized", True))}
    try:
        profile['content'] = validate_content(data.get('content', {}))
    except ValueError as error:
        return jsonify({'error': str(error)}), 400
    settings["displays"] = profiles + [profile]; save_settings(settings)
    return jsonify({"display": profile}), 201

@app.route("/api/displays/<display_id>", methods=["PATCH", "DELETE"])
@api_login_required
def api_display_profile(display_id):
    settings = load_settings(); profiles = display_profiles(settings)
    profile = next((p for p in profiles if p["id"] == display_id), None)
    if not profile: return jsonify({"error": "Display not found"}), 404
    previous_mode = profile['content']['mode']
    if request.method == "DELETE":
        if len(profiles) == 1: return jsonify({"error": "At least one display is required"}), 400
        settings["displays"] = [p for p in profiles if p["id"] != display_id]; save_settings(settings)
        _art_display_states.pop(display_id, None)
        _independent_display_states.pop(display_id, None)
        return jsonify({"success": True})
    data = request.json or {}
    if 'content' in data:
        try:
            profile['content'] = validate_content(data['content'], profile.get('content'))
        except ValueError as error:
            return jsonify({'error': str(error)}), 400
    for key in ("name", "active", "synchronized"):
        if key in data: profile[key] = str(data[key])[:80] if key == "name" else bool(data[key])
    for key in ("width", "height"):
        if key in data:
            try: profile[key] = int(data[key])
            except (TypeError, ValueError): return jsonify({"error": f"{key} must be a number"}), 400
            if not 100 <= profile[key] <= 10000:
                return jsonify({'error': 'Invalid display dimensions'}), 400
    if not any(p.get("active", True) for p in profiles): return jsonify({"error": "At least one display must remain active"}), 400
    if previous_mode != profile['content']['mode'] and not profile.get('synchronized', True):
        photo_state = _profile_state(profile)
        if profile['content']['mode'] == 'art':
            slides, _, _ = _build_slides()
            photo_state['index'] = _get_effective_index(len(slides), photo_state, display_settings(settings, profile))
        photo_state['last_advanced_at'] = time.time()
    settings["displays"] = profiles; save_settings(settings)
    return jsonify({"display": profile})

@app.route("/api/display/state")
@display_api_required
def api_display_state():
    """Return the current render for this display profile."""
    settings = load_settings()
    profile = get_display_profile(settings, request.args.get("display"))
    if profile['content']['mode'] == 'art':
        return jsonify(_art_state_response(profile))
    profile_settings = display_settings(settings, profile)
    state = _profile_state(profile)
    slides, _, _ = _build_slides()
    total = len(slides)
    index = _get_effective_index(total, state, profile_settings)
    current_slide = slides[index] if total else None
    return jsonify({
        "display": profile, "content_mode": "photos", "index": index, "paused": state["paused"], "total": total,
        "snapshot_url": _profile_snapshot_url(current_slide, profile),
        "mat_color": (current_slide.get("mat_color") if current_slide else None) or profile_settings.get("mat_color", "#ffffff"),
        "transition_duration": profile_settings.get("transition_duration", 1),
    })

@app.route("/api/display/control", methods=["POST"])
@display_api_required
def api_display_control():
    data = request.json or {}
    action = data.get("action")
    if action not in ("next", "prev", "pause", "play"):
        return jsonify({"error": "Invalid action. Use next, prev, pause, or play."}), 400
    settings = load_settings()
    profile = get_display_profile(settings, data.get("display") or request.args.get("display"))
    if profile['content']['mode'] == 'art':
        if action in ('next', 'prev'):
            return jsonify({'error': 'Artwork navigation is unavailable for a single artwork'}), 400
        state = _art_display_states.setdefault(profile['id'], {'paused': False})
        state['paused'] = action == 'pause'
        return jsonify(_art_state_response(profile))
    profile_settings = display_settings(settings, profile)
    state = _profile_state(profile)
    slides, _, _ = _build_slides()
    total = len(slides)
    if not total:
        return jsonify({"index": 0, "paused": state["paused"]})
    now, current = time.time(), _get_effective_index(total, state, profile_settings)
    if action == "next":
        state["index"] = (current + 1) % total
        state["last_advanced_at"] = now
    elif action == "prev":
        state["index"] = (current - 1) % total
        state["last_advanced_at"] = now
    elif action == "pause":
        state["index"], state["paused"] = current, True
    else:
        state["paused"], state["last_advanced_at"] = False, now
    index = _get_effective_index(total, state, profile_settings)
    return jsonify({"index": index, "paused": state["paused"], "snapshot_url": _profile_snapshot_url(slides[index], profile)})


# --- Backup Routes ---

@app.route('/backup')
@admin_required
def backup_page():
    from flask import redirect
    return redirect('/admin#backup')


@app.route('/api/backup/status', methods=['GET'])
@api_admin_required
def api_backup_status():
    """Get backup status"""
    configured = is_backup_configured()
    log = load_backup_log()
    backup_settings = get_backup_settings()

    # Get next scheduled run time
    next_run = None
    job = scheduler.get_job('daily_backup')
    if job and job.next_run_time:
        next_run = job.next_run_time.isoformat()

    return jsonify({
        'configured': configured,
        'in_progress': backup_in_progress,
        'restore_in_progress': restore_in_progress,
        'last_backup': log.get('last_backup'),
        'last_result': log.get('last_result'),
        'last_error': log.get('last_error'),
        'next_scheduled': next_run,
        'backup_time': backup_settings['backup_time'],
        'backup_path': backup_settings['backup_path'],
        'max_backup_history': backup_settings['max_backup_history'],
    })


@app.route('/api/backup/run', methods=['POST'])
@api_admin_required
def api_backup_run():
    """Trigger a manual backup"""
    if backup_in_progress:
        return jsonify({'error': 'Backup already in progress'}), 409
    if not is_backup_configured():
        return jsonify({'error': 'Backup not configured'}), 400
    run_backup_async()
    return jsonify({'success': True, 'message': 'Backup started'})


@app.route('/api/backup/restore', methods=['POST'])
@api_admin_required
def api_backup_restore():
    """Restore photos and data from Dropbox backup"""
    if backup_in_progress or restore_in_progress:
        return jsonify({'error': 'A backup or restore is already in progress'}), 409
    if not is_backup_configured():
        return jsonify({'error': 'Backup not configured'}), 400
    run_restore_async()
    return jsonify({'success': True, 'message': 'Restore started'})


@app.route('/api/backup/configure', methods=['POST'])
@api_admin_required
def api_backup_configure():
    """Configure Dropbox backup with an rclone token"""
    data = request.json
    token = data.get('token', '').strip()

    if not token:
        return jsonify({'error': 'No token provided'}), 400

    # Validate that the token looks like JSON
    try:
        json.loads(token)
    except json.JSONDecodeError:
        return jsonify({'error': 'Invalid token format. Must be a JSON string from rclone authorize.'}), 400

    # Write rclone config
    generate_rclone_config(token)

    # Test the connection
    try:
        result = subprocess.run(
            ['rclone', 'lsd', 'dropbox:', '--config', str(RCLONE_CONFIG_FILE)],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            # Clean up on failure
            RCLONE_CONFIG_FILE.unlink(missing_ok=True)
            error = result.stderr.strip()[:300]
            return jsonify({'error': f'Connection test failed: {error}'}), 400
    except subprocess.TimeoutExpired:
        RCLONE_CONFIG_FILE.unlink(missing_ok=True)
        return jsonify({'error': 'Connection test timed out'}), 400
    except FileNotFoundError:
        RCLONE_CONFIG_FILE.unlink(missing_ok=True)
        return jsonify({'error': 'rclone is not installed'}), 500

    return jsonify({'success': True, 'message': 'Dropbox connected successfully'})


@app.route('/api/backup/configure', methods=['DELETE'])
@api_admin_required
def api_backup_disconnect():
    """Disconnect Dropbox backup"""
    RCLONE_CONFIG_FILE.unlink(missing_ok=True)
    return jsonify({'success': True, 'message': 'Dropbox disconnected'})


@app.route('/api/backup/history', methods=['GET'])
@api_admin_required
def api_backup_history():
    """Get backup history"""
    log = load_backup_log()
    return jsonify({'history': log.get('history', [])})


@app.route('/api/backup/settings', methods=['POST'])
@api_admin_required
def api_backup_settings():
    """Update backup settings (time, path)"""
    data = request.json
    settings = load_settings()

    if 'backup_time' in data:
        time_str = data['backup_time']
        # Validate HH:MM format
        try:
            h, m = map(int, time_str.split(':'))
            if not (0 <= h <= 23 and 0 <= m <= 59):
                raise ValueError
            settings['backup_time'] = time_str
            reschedule_backup(time_str)
        except (ValueError, AttributeError):
            return jsonify({'error': 'Invalid time format. Use HH:MM'}), 400

    if 'backup_path' in data:
        path = data['backup_path'].strip()
        if path:
            settings['backup_path'] = path

    if 'max_backup_history' in data:
        try:
            val = int(data['max_backup_history'])
            if 1 <= val <= 200:
                settings['max_backup_history'] = val
        except (ValueError, TypeError):
            return jsonify({'error': 'max_backup_history must be an integer between 1 and 200'}), 400

    save_settings(settings)
    return jsonify({'success': True})


# --- CEC TV Control Routes ---

@app.route('/api/cec/status', methods=['GET'])
@api_login_required
def api_cec_status():
    """Check if CEC control is available."""
    return jsonify({'available': is_cec_available()})


@app.route('/api/cec/test', methods=['POST'])
@api_admin_required
def api_cec_test():
    """Test CEC by sending a command to the TV."""
    data = request.json or {}
    command = data.get('command', 'on')
    if command not in ('on', 'standby'):
        return jsonify({'error': 'Invalid command. Use "on" or "standby".'}), 400
    result = cec_send_command(command)
    if result['success']:
        return jsonify({'success': True})
    return jsonify({'error': result.get('error', 'Unknown error')}), 500


@app.route('/api/tv-schedules', methods=['GET'])
@api_login_required
def api_get_tv_schedules():
    """Get all TV schedules."""
    settings = load_settings()
    return jsonify({
        'schedules': settings.get('tv_schedules', []),
        'cec_available': is_cec_available()
    })


@app.route('/api/cec/register', methods=['POST'])
@csrf.exempt
@cec_agent_required
def api_cec_register():
    """Allow the authenticated display-side agent to register availability."""
    global _cec_display_has_cec
    _cec_display_has_cec = True
    return jsonify({'success': True})


@app.route('/api/cec/pending', methods=['GET'])
@cec_agent_required
def api_cec_pending():
    """Return and dequeue the oldest pending CEC command for a remote display agent.
    Returns {command: 'on'|'standby'} or {command: null} if nothing is queued."""
    command = _cec_queue.pop(0) if _cec_queue else None
    if command not in (None, 'on', 'standby'):
        command = None
    return jsonify({'command': command})


@app.get('/api/cec/agent-token')
@api_admin_required
def api_cec_agent_token():
    """Return the separate CEC token to an administrator for agent installation."""
    response = jsonify({'cec_agent_token': CEC_AGENT_TOKEN})
    response.headers['Cache-Control'] = 'no-store, private'
    return response


@app.route('/api/tv-schedules', methods=['POST'])
@api_admin_required
def api_save_tv_schedules():
    """Save all TV schedules."""
    data = request.json
    schedules = data.get('schedules', [])

    for sched in schedules:
        if 'id' not in sched:
            sched['id'] = f'sched_{uuid.uuid4().hex[:8]}'
        for field in ('on_time', 'off_time'):
            try:
                h, m = map(int, sched[field].split(':'))
                if not (0 <= h <= 23 and 0 <= m <= 59):
                    raise ValueError
            except (ValueError, KeyError, AttributeError):
                return jsonify({'error': f'Invalid {field} format. Use HH:MM.'}), 400
        days = sched.get('days', [])
        if not isinstance(days, list) or not all(isinstance(d, int) and 0 <= d <= 6 for d in days):
            return jsonify({'error': 'Invalid days. Must be array of integers 0-6.'}), 400
        sched.setdefault('enabled', True)

    settings = load_settings()
    settings['tv_schedules'] = schedules
    save_settings(settings)
    schedule_cec_jobs()

    return jsonify({'success': True, 'schedules': schedules})


# ============ Network Info ============

def get_network_info():
    """Get network addresses for display."""
    import socket
    info = {'local_ip': None, 'tailscale_ip': None}
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        info['local_ip'] = s.getsockname()[0]
        s.close()
    except Exception:
        pass
    try:
        result = subprocess.run(['tailscale', 'ip', '-4'],
                                capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            info['tailscale_ip'] = result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return info


@app.route('/api/network-info', methods=['GET'])
@api_admin_required
def api_network_info():
    """Get network info (local IP, Tailscale IP) for admin display."""
    return jsonify(get_network_info())


# ============ Maintenance Window ============

@app.route('/api/maintenance-window', methods=['GET'])
def api_maintenance_window():
    """Check if current time is within a maintenance window (TV is off)."""
    settings = load_settings()
    schedules = settings.get('tv_schedules', [])

    if not schedules:
        return jsonify({'can_deploy': True, 'reason': 'No TV schedules configured'})

    now = datetime.now()
    current_day = now.weekday()
    current_minutes = now.hour * 60 + now.minute

    for sched in schedules:
        if not sched.get('enabled', True):
            continue
        days = sched.get('days', [])
        if current_day not in days:
            continue

        on_h, on_m = map(int, sched['on_time'].split(':'))
        off_h, off_m = map(int, sched['off_time'].split(':'))
        on_minutes = on_h * 60 + on_m
        off_minutes = off_h * 60 + off_m

        if on_minutes <= current_minutes < off_minutes:
            return jsonify({
                'can_deploy': False,
                'reason': f'TV is scheduled ON until {sched["off_time"]}'
            })

    return jsonify({'can_deploy': True, 'reason': 'Outside TV schedule'})


# ============ Error Handlers ============

@app.errorhandler(413)
def too_large(e):
    return jsonify({'error': 'File too large. Maximum size is 50MB.'}), 413


@app.after_request
def apply_browser_security_headers(response):
    """Apply browser protections even when no external proxy is present."""
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'same-origin'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; "
        "script-src 'self'; script-src-attr 'none'; "
        # Dynamic photo layout uses element.style for dimensions/crops. Keep the
        # exception narrowly scoped to style attributes; style elements remain self-only.
        "style-src 'self'; style-src-attr 'unsafe-inline'; "
        "img-src 'self' data: blob:; connect-src 'self'; font-src 'self'; "
        "object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
    )
    sensitive_paths = (
        '/login', '/change-password', '/mfa', '/admin/users',
        '/api/display/enroll', '/api/display/enrollment-secret', '/api/display/rotate-secret',
        '/api/display/state', '/api/settings', '/api/admin/', '/api/mfa/', '/api/cec/agent-token',
        '/display/approve/', '/api/display/approve/',
    )
    if any(request.path == path or (path.endswith('/') and request.path.startswith(path))
           for path in sensitive_paths):
        response.headers['Cache-Control'] = 'no-store, private'
    hostname = (request.host.split(':', 1)[0] or '').lower()
    if (app.config['HSTS_ENABLED'] and request.is_secure
            and hostname == app.config['TRUSTED_HTTPS_HOSTNAME']
            and hostname not in ('localhost', '127.0.0.1', '::1')):
        try:
            ip_address(hostname)
        except ValueError:
            response.headers['Strict-Transport-Security'] = 'max-age=31536000'
    return response


# ============ Startup ============

# Backfill thumbnails for existing images on startup
_thumb_count = backfill_thumbnails()
if _thumb_count > 0:
    print(f"Generated {_thumb_count} missing thumbnail(s)")

# Backfill mat colors for existing images on startup (runs in background to avoid slowing boot)
def _backfill_mat_colors_bg():
    count = backfill_mat_colors()
    if count > 0:
        print(f"Suggested mat colors for {count} existing image(s)")

threading.Thread(target=_backfill_mat_colors_bg, daemon=True).start()


# ============ Main ============

if __name__ == '__main__':
    # Ensure the random one-time administrator exists.
    load_users()

    print("\n" + "="*50)
    print("OpenFotoFrame Server")
    print("="*50)
    print(f"Upload & Gallery: http://localhost:5000/upload")
    print(f"TV Display:       http://localhost:5000/display")
    print("\nAdministrator bootstrap credentials are available only from the protected data volume.")
    print("="*50 + "\n")

    app.run(host='0.0.0.0', port=5000, debug=os.environ.get('FLASK_DEBUG', '').lower() in ('1', 'true'))
