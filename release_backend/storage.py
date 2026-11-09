"""Private media adapters. Public or client-chosen object URLs are never returned."""

from pathlib import Path
import os


class StorageError(Exception):
    """A private storage operation failed; never expose provider diagnostics."""


class FileStorage:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def path(self, key):
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root) or path == self.root:
            raise ValueError("Invalid storage key")
        return path

    def write(self, key, data):
        path = self.path(key)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Generated opaque keys must not replace an existing resource.
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)

    def read(self, key):
        return self.path(key).read_bytes()

    def delete(self, key):
        self.path(key).unlink(missing_ok=True)


class S3Storage:
    def __init__(self, bucket, prefix="", endpoint_url=None, client=None):
        if not bucket:
            raise ValueError("S3_BUCKET is required")
        if client is None:
            import boto3

            client = boto3.client("s3", endpoint_url=endpoint_url)
        self.client = client
        self.bucket = bucket
        self.prefix = prefix.strip("/")

    def validate_private(self):
        settings = self.client.get_public_access_block(Bucket=self.bucket).get(
            "PublicAccessBlockConfiguration", {}
        )
        if not all(
            settings.get(key) is True
            for key in (
                "BlockPublicAcls",
                "IgnorePublicAcls",
                "BlockPublicPolicy",
                "RestrictPublicBuckets",
            )
        ):
            raise ValueError(
                "Hosted S3 storage requires all bucket Block Public Access settings"
            )

    def key(self, key):
        if key.startswith("/") or any(
            part in ("", "..", ".") for part in key.split("/")
        ):
            raise ValueError("Invalid storage key")
        return "/".join(part for part in (self.prefix, key) if part)

    def sdk(self, operation, **parameters):
        try:
            return getattr(self.client, operation)(**parameters)
        except Exception as error:
            if operation == "get_object" and getattr(error, "response", {}).get(
                "Error", {}
            ).get("Code") in (
                "NoSuchKey",
                "404",
            ):
                raise FileNotFoundError from None
            raise StorageError("Private storage is unavailable") from error

    def write(self, key, data):
        # Bucket configuration must enforce Block Public Access. No ACL or
        # presigned URL makes these objects reachable outside the policy proxy.
        self.sdk(
            "put_object",
            Bucket=self.bucket,
            Key=self.key(key),
            Body=data,
            ContentType="application/octet-stream",
            ServerSideEncryption="AES256",
            IfNoneMatch="*",
        )

    def read(self, key):
        body = self.sdk("get_object", Bucket=self.bucket, Key=self.key(key))["Body"]
        try:
            return body.read()
        except Exception as error:
            raise StorageError("Private storage is unavailable") from error
        finally:
            try:
                body.close()
            except Exception:
                pass

    def delete(self, key):
        self.sdk("delete_object", Bucket=self.bucket, Key=self.key(key))
