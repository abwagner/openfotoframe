"""Delivery tests use mock SMTP and synthetic local accounts only."""
import hashlib
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
import pytest
from sqlalchemy import insert, select, func
from release_delivery import register, DeliveryError
from release_delivery.offline import issue_recovery
from release_backend import create_app, models as m


def smtp_app(**overrides):
    config = dict(PUBLIC_ORIGIN="https://fixture.example", SMTP_HOST="smtp.fixture.example", SMTP_PORT=587,
        SMTP_USERNAME="fixture-user", SMTP_PASSWORD="fixture-password", SMTP_FROM="frame@fixture.example", SMTP_TLS_MODE="starttls")
    config.update(overrides)
    return SimpleNamespace(config=config)


def test_smtp_tls_precedes_credentials_and_links_use_fragments(monkeypatch):
    import release_delivery
    actions, messages = [], []
    class SMTP:
        def __init__(self, *a, **kw):
            assert kw["timeout"] == 15
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def ehlo(self): actions.append("ehlo")
        def starttls(self, **kw):
            assert kw["context"].check_hostname
            actions.append("tls")
        def login(self, *args): actions.append("login")
        def send_message(self, message): messages.append(message)
    monkeypatch.setattr(release_delivery.smtplib, "SMTP", SMTP)
    app = smtp_app()
    register(app)
    assert not messages
    app.config["VERIFICATION_DELIVERY"]("person@fixture.example", "synthetic-token")
    assert actions == ["ehlo", "tls", "ehlo", "login"]
    text = messages[0].get_content()
    assert "https://fixture.example/verify#token=synthetic-token" in text
    assert "fixture-password" not in text
    app.config["RECOVERY_DELIVERY"]("person@fixture.example", "other-token")
    assert "/recover#token=other-token" in messages[1].get_content()


def test_smtp_partial_plaintext_or_failed_starttls_never_send_credentials(monkeypatch):
    import release_delivery
    for changes in ({"SMTP_PASSWORD": ""}, {"SMTP_TLS_MODE": "none"}, {"SMTP_PORT": "587"}):
        with pytest.raises(ValueError): register(smtp_app(**changes))
    class FailedSMTP:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def ehlo(self): pass
        def starttls(self, **kw): raise RuntimeError("secret fixture provider error")
        def login(self, *args): pytest.fail("Must not login after TLS failure")
    monkeypatch.setattr(release_delivery.smtplib, "SMTP", FailedSMTP)
    app = smtp_app(); register(app)
    with pytest.raises(DeliveryError) as error:
        app.config["RECOVERY_DELIVERY"]("person@fixture.example", "private-token")
    assert "secret" not in str(error.value) and "private-token" not in str(error.value)
    empty = SimpleNamespace(config={}); register(empty)
    assert "RECOVERY_DELIVERY" not in empty.config


def test_smtp_ssl_uses_verified_context_and_no_plaintext_connection(monkeypatch):
    import release_delivery
    calls = []
    class SMTPSSL:
        def __init__(self, host, port, **kwargs):
            assert kwargs["context"].check_hostname and port == 465
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def ehlo(self): pass
        def login(self, *args): calls.append("login")
        def send_message(self, message): calls.append("sent")
    monkeypatch.setattr(release_delivery.smtplib, "SMTP_SSL", SMTPSSL)
    monkeypatch.setattr(release_delivery.smtplib, "SMTP", lambda *a, **kw: pytest.fail("No plaintext connection"))
    app = smtp_app(SMTP_TLS_MODE="ssl", SMTP_PORT=465); register(app)
    app.config["VERIFICATION_DELIVERY"]("person@fixture.example", "synthetic-token")
    assert calls == ["login", "sent"]


@pytest.fixture
def recovery_app(tmp_path):
    app = create_app(dict(TESTING=True, INIT_SCHEMA=True, DATABASE_URL="sqlite:///:memory:",
        MEDIA_ROOT=str(tmp_path / "media"), SECRET_KEY="synthetic-secret-key-with-at-least-32-characters",
        PUBLIC_ORIGIN="https://fixture.example", CLOCK=lambda: 100))
    store = app.extensions["release_store"]
    with store.transaction() as db:
        db.execute(insert(m.users).values(id="user", email="user@fixture.example", username="fixture-user",
            email_verified=True, password_hash="original-audit-hash", mfa={}, disabled=False,
            must_change_password=False, created_at=1))
        db.execute(insert(m.households).values(id="household", name="Fixture", settings={}, created_at=1))
        db.execute(insert(m.memberships).values(id="member", household_id="household", user_id="user", role="owner", active=True))
    yield app, store
    store.engine.dispose()


def test_offline_link_hashed_bounded_private_audited_and_one_use(recovery_app, tmp_path):
    app, store = recovery_app
    private = (tmp_path / "private").resolve(); private.mkdir(mode=0o700)
    output = private / "recovery.txt"
    assert issue_recovery(store, user_id="user", household_id="household", public_origin="https://fixture.example",
        output_path=output, clock=lambda: 100) == {"issued": True, "expires_in": 1800}
    token = parse_qs(urlsplit(output.read_text().strip()).fragment)["token"][0]
    assert output.stat().st_mode & 0o777 == 0o600
    with store.transaction() as db:
        pending = db.execute(select(m.challenges)).mappings().one()
        assert pending["token_hash"] == hashlib.sha256(token.encode()).hexdigest()
        assert pending["expires_at"] == 1900 and not pending["consumed"]
        assert token not in repr(dict(pending))
        assert db.execute(select(m.audit_events.c.action)).scalar_one() == "offline_recovery_issued"
        assert db.execute(select(m.users.c.password_hash)).scalar_one() == "original-audit-hash"
    payload = dict(token=token, password="synthetic-new-password-123")
    assert app.test_client().post("/api/v1/auth/recovery/complete", json=payload).status_code == 200
    assert app.test_client().post("/api/v1/auth/recovery/complete", json=payload).status_code == 401


def test_offline_recovery_does_not_bypass_mfa_or_create_accounts(recovery_app, tmp_path):
    from sqlalchemy import update
    app, store = recovery_app
    private = (tmp_path / "private").resolve(); private.mkdir(mode=0o700)
    with store.transaction() as db:
        db.execute(update(m.users).where(m.users.c.id == "user").values(mfa={"passkeys": [{"id": "fixture-credential"}]}))
    output = private / "mfa.txt"
    issue_recovery(store, user_id="user", household_id="household", public_origin="https://fixture.example",
        output_path=output, clock=lambda: 100)
    token = parse_qs(urlsplit(output.read_text().strip()).fragment)["token"][0]
    assert app.test_client().post("/api/v1/auth/recovery/complete", json={"token": token,
        "password": "synthetic-new-password-123"}).status_code == 401
    with store.transaction() as db:
        assert db.execute(select(m.users.c.mfa)).scalar_one()["passkeys"] == [{"id": "fixture-credential"}]
        assert db.execute(select(m.users.c.password_hash)).scalar_one() == "original-audit-hash"
    with pytest.raises(ValueError):
        issue_recovery(store, user_id="missing", household_id="household", public_origin="https://fixture.example",
            output_path=private / "missing.txt")
    assert not (private / "missing.txt").exists()
    with pytest.raises(ValueError):
        issue_recovery(store, user_id="user", household_id="household", public_origin="https://fixture.example",
            output_path=Path(__file__).resolve().parent / "never-write-recovery.txt")
