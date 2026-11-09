"""The new service cannot accidentally start with the old trust/config model."""

import pytest
from release_app import environment_config


def config(**changes):
    return {
        "RELEASE_MODE": "self_hosted",
        "SECRET_KEY": "x" * 40,
        "PUBLIC_ORIGIN": "https://frame.example.com",
        **changes,
    }


def test_release_entry_requires_explicit_mode_and_private_secret():
    with pytest.raises(ValueError):
        environment_config({})
    with pytest.raises(ValueError):
        environment_config(config(SECRET_KEY="short"))
    values = environment_config(config())
    assert values["DATABASE_URL"].startswith("sqlite:///")
    assert values["REGISTER_ENABLED"] is False
    assert values["SESSION_COOKIE_SECURE"] is True


@pytest.mark.parametrize(
    "url",
    [
        "https://user:pass@example.com",
        "https://example.com/upload",
        "https://example.com?token=secret",
        "https://example.com#secret",
    ],
)
def test_server_origin_rejects_credentials_and_link_secrets(url):
    with pytest.raises(ValueError):
        environment_config(config(PUBLIC_ORIGIN=url))


def test_hosted_entry_cannot_fall_back_to_sqlite_or_cleartext():
    with pytest.raises(ValueError):
        environment_config(config(RELEASE_MODE="hosted"))
    with pytest.raises(ValueError):
        environment_config(
            config(RELEASE_MODE="hosted", DATABASE_URL="sqlite:///local.db")
        )
    with pytest.raises(ValueError):
        environment_config(
            config(
                RELEASE_MODE="hosted",
                DATABASE_URL="postgresql://db/cloud",
                PUBLIC_ORIGIN="http://cloud.example.com",
            )
        )
    values = environment_config(
        config(RELEASE_MODE="hosted", DATABASE_URL="postgresql://db/cloud")
    )
    assert values["DATABASE_URL"] == "postgresql+psycopg://db/cloud"


def test_provider_configuration_is_explicit_and_does_not_log_bad_secret_input():
    values = environment_config(config())
    assert "APPLE_BUNDLE_ID" not in values and "STRIPE_SECRET_KEY" not in values
    values = environment_config(
        config(
            BILLING_PLANS="[]",
            APPLE_ROOT_CERTIFICATES='["/secrets/apple-root.cer"]',
            APPLE_APP_ID="123",
        )
    )
    assert values["APPLE_ROOT_CERTIFICATES"] == ["/secrets/apple-root.cer"]
    assert values["APPLE_APP_ID"] == 123
    with pytest.raises(ValueError) as failure:
        environment_config(config(BILLING_PLANS="private-value-never-log"))
    assert "private-value-never-log" not in str(failure.value)


@pytest.mark.parametrize(
    "setting", ["REQUIRE_MFA", "REGISTER_ENABLED", "STRIPE_LIVE_MODE"]
)
def test_security_flags_reject_typos_instead_of_silently_disabling(setting):
    with pytest.raises(ValueError):
        environment_config(config(**{setting: "tru"}))


def test_dev_app_callbacks_require_explicit_bounded_configuration():
    values = environment_config(config())
    assert values["APP_LINK_SCHEME"] == "openfotoframe"
    assert "AUTH_REDIRECT_URIS" not in values
    values = environment_config(
        config(
            APP_LINK_SCHEME="openfotoframe-dev",
            AUTH_REDIRECT_URIS='["openfotoframe-dev://auth/callback"]',
        )
    )
    assert values["AUTH_REDIRECT_URIS"] == ["openfotoframe-dev://auth/callback"]
    for raw in [
        "[]",
        '["https://foreign.example/callback"]',
        '["openfotoframe-dev://other/callback"]',
        "[true]",
    ]:
        with pytest.raises(ValueError):
            environment_config(config(AUTH_REDIRECT_URIS=raw))
    with pytest.raises(ValueError):
        environment_config(config(APP_LINK_SCHEME="arbitrary-app"))
