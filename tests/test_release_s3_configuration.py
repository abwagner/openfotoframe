"""Hosted primary storage must support verified permanent object deletion."""

import pytest
from release_backend import create_app
from release_backend.storage import S3Storage, StorageError


class ProviderError(Exception):
    def __init__(self, code):
        super().__init__("private provider diagnostics and credential details")
        self.response = {"Error": {"Code": code}}


class Provider:
    def __init__(
        self,
        *,
        versioning=None,
        lock_error="ObjectLockConfigurationNotFoundError",
        lock_response=None,
        fail=None,
    ):
        self.versioning = {} if versioning is None else versioning
        self.lock_error = lock_error
        self.lock_response = lock_response
        self.fail = fail
        self.calls = []

    def get_public_access_block(self, **arguments):
        self.calls.append(("public", arguments))
        if self.fail == "public":
            raise ProviderError("AccessDenied")
        return {
            "PublicAccessBlockConfiguration": {
                name: True
                for name in (
                    "BlockPublicAcls",
                    "IgnorePublicAcls",
                    "BlockPublicPolicy",
                    "RestrictPublicBuckets",
                )
            }
        }

    def get_bucket_versioning(self, **arguments):
        self.calls.append(("versioning", arguments))
        if self.fail == "versioning":
            raise ProviderError("AccessDenied")
        return self.versioning

    def get_object_lock_configuration(self, **arguments):
        self.calls.append(("lock", arguments))
        if self.lock_error:
            raise ProviderError(self.lock_error)
        return self.lock_response


def test_never_versioned_bucket_with_known_object_lock_absence_is_verified_read_only():
    provider = Provider()
    S3Storage("synthetic-primary", client=provider).validate_private()
    assert [name for name, args in provider.calls] == ["public", "versioning", "lock"]
    assert all(args == {"Bucket": "synthetic-primary"} for name, args in provider.calls)


@pytest.mark.parametrize(
    "status", ["Enabled", "Suspended", "Disabled", "unknown", None]
)
def test_any_reported_versioning_status_fails_closed(status):
    provider = Provider(versioning={"Status": status})
    with pytest.raises(ValueError, match="never-versioned"):
        S3Storage("synthetic-primary", client=provider).validate_private()
    assert [name for name, args in provider.calls] == ["public", "versioning"]


def test_object_lock_enabled_rejects_primary_bucket_even_with_no_default_retention():
    provider = Provider(
        lock_error=None,
        lock_response={"ObjectLockConfiguration": {"ObjectLockEnabled": "Enabled"}},
    )
    with pytest.raises(ValueError, match="Object Lock enabled"):
        S3Storage("synthetic-primary", client=provider).validate_private()


@pytest.mark.parametrize(
    "code",
    [
        "AccessDenied",
        "NoSuchBucket",
        "NotImplemented",
        "InternalError",
        "404",
        "NoSuchObjectLockConfiguration",
    ],
)
def test_only_documented_object_lock_not_configured_error_counts_as_disabled(code):
    with pytest.raises(StorageError, match="could not be verified") as error:
        S3Storage(
            "synthetic-primary", client=Provider(lock_error=code)
        ).validate_private()
    assert "private provider" not in str(error.value)


@pytest.mark.parametrize(
    "response",
    [
        None,
        {},
        {"ObjectLockConfiguration": {}},
        {"ObjectLockConfiguration": {"ObjectLockEnabled": "Disabled"}},
        {"ObjectLockConfiguration": "unknown"},
    ],
)
def test_ambiguous_object_lock_success_response_is_not_treated_as_disabled(response):
    with pytest.raises(StorageError, match="could not be verified"):
        S3Storage(
            "synthetic-primary",
            client=Provider(lock_error=None, lock_response=response),
        ).validate_private()


@pytest.mark.parametrize("stage", ["public", "versioning"])
def test_missing_configuration_iam_permission_fails_closed_without_provider_details(
    stage,
):
    with pytest.raises(StorageError, match="could not be verified") as error:
        S3Storage("synthetic-primary", client=Provider(fail=stage)).validate_private()
    assert "private provider" not in str(error.value)


def test_hosted_startup_rejects_versioned_storage_before_database_or_http_start():
    with pytest.raises(ValueError, match="never-versioned"):
        create_app(
            {
                "RELEASE_MODE": "hosted",
                "PUBLIC_ORIGIN": "https://fixture.example",
                "SECRET_KEY": "synthetic-startup-key-more-than-32-characters",
                "DATABASE_URL": "postgresql+psycopg://not-a-real-database/fixture",
                "MEDIA_STORAGE": S3Storage(
                    "synthetic-primary",
                    client=Provider(versioning={"Status": "Suspended"}),
                ),
            }
        )
