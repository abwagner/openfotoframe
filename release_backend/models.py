"""Shared relational schema. No legacy JSON file is an authorization authority."""

from sqlalchemy import (
    MetaData,
    Table,
    Column,
    String,
    Integer,
    Boolean,
    JSON,
    ForeignKey,
    ForeignKeyConstraint,
    UniqueConstraint,
    CheckConstraint,
)

metadata = MetaData()


def ident(name="id", primary_key=False):
    return Column(name, String(64), primary_key=primary_key)


def tenant(name):
    return [
        ident(primary_key=True),
        Column("household_id", String(64), ForeignKey("households.id"), nullable=False),
        UniqueConstraint("household_id", "id"),
    ]


server_metadata = Table(
    "server_metadata",
    metadata,
    Column("key", String(64), primary_key=True),
    Column("value", JSON, nullable=False),
)
users = Table(
    "users",
    metadata,
    ident(primary_key=True),
    Column("email", String(320), unique=True, nullable=False),
    Column("username", String(320), unique=True),
    Column("email_verified", Boolean, nullable=False, default=False),
    Column("password_hash", String(1024), nullable=False),
    Column("legacy_salt", String(256)),
    Column("mfa", JSON, nullable=False, default=dict),
    Column("must_change_password", Boolean, nullable=False, default=False),
    Column("disabled", Boolean, nullable=False, default=False),
    Column("created_at", Integer, nullable=False),
)
households = Table(
    "households",
    metadata,
    ident(primary_key=True),
    Column("name", String(200), nullable=False),
    Column("settings", JSON, nullable=False, default=dict),
    Column("created_at", Integer, nullable=False),
)
memberships = Table(
    "memberships",
    metadata,
    *tenant("memberships"),
    Column("user_id", String(64), ForeignKey("users.id"), nullable=False),
    Column("role", String(20), nullable=False),
    Column("active", Boolean, nullable=False, default=True),
    UniqueConstraint("household_id", "user_id"),
    CheckConstraint("role IN ('owner','admin','member')"),
)
collections = Table(
    "collections",
    metadata,
    *tenant("collections"),
    Column("name", String(200), nullable=False),
    Column("settings", JSON, nullable=False, default=dict),
)
collection_grants = Table(
    "collection_grants",
    metadata,
    *tenant("collection_grants"),
    Column("membership_id", String(64), nullable=False),
    Column("collection_id", String(64), nullable=False),
    Column("role", String(20), nullable=False),
    ForeignKeyConstraint(
        ["household_id", "membership_id"],
        ["memberships.household_id", "memberships.id"],
    ),
    ForeignKeyConstraint(
        ["household_id", "collection_id"],
        ["collections.household_id", "collections.id"],
    ),
    UniqueConstraint("membership_id", "collection_id"),
    CheckConstraint("role IN ('viewer','contributor','editor')"),
)
assets = Table(
    "assets",
    metadata,
    *tenant("assets"),
    Column("collection_id", String(64), nullable=False),
    Column("filename", String(255), nullable=False),
    Column("storage_key", String(255), unique=True, nullable=False),
    Column("thumbnail_key", String(255)),
    Column("sha256", String(64)),
    Column("byte_size", Integer, nullable=False),
    Column("mime_type", String(80), nullable=False),
    Column("uploaded_by", String(64), ForeignKey("users.id")),
    Column("metadata", JSON, nullable=False, default=dict),
    Column("deleted", Boolean, nullable=False, default=False),
    Column("created_at", Integer, nullable=False),
    UniqueConstraint("household_id", "collection_id", "id"),
    ForeignKeyConstraint(
        ["household_id", "collection_id"],
        ["collections.household_id", "collections.id"],
    ),
)
groups = Table(
    "groups",
    metadata,
    *tenant("groups"),
    Column("collection_id", String(64), nullable=False),
    Column("name", String(200), nullable=False, default=""),
    Column("metadata", JSON, nullable=False, default=dict),
    UniqueConstraint("household_id", "collection_id", "id"),
    ForeignKeyConstraint(
        ["household_id", "collection_id"],
        ["collections.household_id", "collections.id"],
    ),
)
group_assets = Table(
    "group_assets",
    metadata,
    Column("household_id", String(64), nullable=False),
    Column("collection_id", String(64), nullable=False),
    Column("group_id", String(64), nullable=False),
    Column("asset_id", String(64), nullable=False),
    Column("position", Integer, nullable=False),
    ForeignKeyConstraint(
        ["household_id", "collection_id", "group_id"],
        ["groups.household_id", "groups.collection_id", "groups.id"],
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["household_id", "collection_id", "asset_id"],
        ["assets.household_id", "assets.collection_id", "assets.id"],
    ),
    UniqueConstraint("group_id", "asset_id"),
)
frames = Table(
    "frames",
    metadata,
    *tenant("frames"),
    Column("name", String(200), nullable=False),
    Column("settings", JSON, nullable=False, default=dict),
    Column("state", JSON, nullable=False, default=dict),
    Column("schedules", JSON, nullable=False, default=list),
)
frame_grants = Table(
    "frame_grants",
    metadata,
    *tenant("frame_grants"),
    Column("membership_id", String(64), nullable=False),
    Column("frame_id", String(64), nullable=False),
    Column("role", String(20), nullable=False),
    ForeignKeyConstraint(
        ["household_id", "membership_id"],
        ["memberships.household_id", "memberships.id"],
    ),
    ForeignKeyConstraint(
        ["household_id", "frame_id"], ["frames.household_id", "frames.id"]
    ),
    UniqueConstraint("membership_id", "frame_id"),
    CheckConstraint("role IN ('viewer','controller')"),
)
frame_collections = Table(
    "frame_collections",
    metadata,
    Column("household_id", String(64), nullable=False),
    Column("frame_id", String(64), nullable=False),
    Column("collection_id", String(64), nullable=False),
    ForeignKeyConstraint(
        ["household_id", "frame_id"],
        ["frames.household_id", "frames.id"],
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["household_id", "collection_id"],
        ["collections.household_id", "collections.id"],
    ),
    UniqueConstraint("frame_id", "collection_id"),
)
sessions = Table(
    "device_sessions",
    metadata,
    ident(primary_key=True),
    Column("user_id", String(64), ForeignKey("users.id"), nullable=False),
    Column("device_name", String(120), nullable=False),
    Column("access_hash", String(64), unique=True, nullable=False),
    Column("access_expires", Integer, nullable=False),
    Column("refresh_hash", String(64), unique=True, nullable=False),
    Column("refresh_expires", Integer, nullable=False),
    Column("revoked", Boolean, nullable=False, default=False),
    Column("authenticated_at", Integer, nullable=False),
    Column("mfa_verified", Boolean, nullable=False, default=False),
    Column("mfa_method", String(20)),
    Column("created_at", Integer, nullable=False),
)
refresh_history = Table(
    "refresh_history",
    metadata,
    Column("token_hash", String(64), primary_key=True),
    Column("session_id", String(64), ForeignKey("device_sessions.id"), nullable=False),
)
challenges = Table(
    "auth_challenges",
    metadata,
    ident(primary_key=True),
    Column("token_hash", String(64), unique=True, nullable=False),
    Column("user_id", String(64), ForeignKey("users.id"), nullable=False),
    Column("purpose", String(40), nullable=False),
    Column("data", JSON, nullable=False, default=dict),
    Column("expires_at", Integer, nullable=False),
    Column("attempts", Integer, nullable=False, default=0),
    Column("consumed", Boolean, nullable=False, default=False),
)
invitations = Table(
    "invitations",
    metadata,
    *tenant("invitations"),
    Column("token_hash", String(64), unique=True, nullable=False),
    Column("issuer_id", String(64), ForeignKey("users.id"), nullable=False),
    Column("recipient_email", String(320)),
    Column("transferable", Boolean, nullable=False, default=False),
    Column("grants", JSON, nullable=False),
    Column("expires_at", Integer, nullable=False),
    Column("revoked", Boolean, nullable=False, default=False),
    Column("accepted_by", String(64), ForeignKey("users.id")),
)
pairings = Table(
    "pairings",
    metadata,
    ident(primary_key=True),
    Column("user_id", String(64), ForeignKey("users.id"), nullable=False),
    Column(
        "source_session_id",
        String(64),
        ForeignKey("device_sessions.id"),
        nullable=False,
    ),
    Column("code_hash", String(64), nullable=False),
    Column("expires_at", Integer, nullable=False),
    Column("consumed", Boolean, nullable=False, default=False),
)
pairing_challenges = Table(
    "pairing_challenges",
    metadata,
    ident(primary_key=True),
    Column("pairing_id", String(64), ForeignKey("pairings.id"), nullable=False),
    Column("secret_hash", String(64), nullable=False),
    Column("confirmation_hash", String(64), nullable=False),
    Column("confirmation_code", String(6), nullable=False),
    Column("device_name", String(120), nullable=False),
    Column("approved", Boolean, nullable=False, default=False),
    Column("denied", Boolean, nullable=False, default=False),
    Column("attempts", Integer, nullable=False, default=0),
)
web_bridges = Table(
    "web_bridges",
    metadata,
    Column("token_hash", String(64), primary_key=True),
    Column("session_id", String(64), ForeignKey("device_sessions.id"), nullable=False),
    Column("scope", JSON, nullable=False, default=dict),
    Column("expires_at", Integer, nullable=False),
    Column("consumed", Boolean, nullable=False, default=False),
)
web_sessions = Table(
    "web_sessions",
    metadata,
    Column("token_hash", String(64), primary_key=True),
    Column("session_id", String(64), ForeignKey("device_sessions.id"), nullable=False),
    Column("csrf_hash", String(64), nullable=False),
    Column("expires_at", Integer, nullable=False),
)
frame_credentials = Table(
    "frame_credentials",
    metadata,
    *tenant("frame_credentials"),
    Column("frame_id", String(64), nullable=False),
    Column("token_hash", String(64), unique=True, nullable=False),
    Column("revoked", Boolean, nullable=False, default=False),
    Column("csrf_hash", String(64)),
    ForeignKeyConstraint(
        ["household_id", "frame_id"], ["frames.household_id", "frames.id"]
    ),
)
audit_events = Table(
    "audit_events",
    metadata,
    *tenant("audit_events"),
    Column("actor_id", String(64)),
    Column("action", String(80), nullable=False),
    Column("resource_id", String(64)),
    Column("created_at", Integer, nullable=False),
)
rate_limits = Table(
    "rate_limits",
    metadata,
    Column("key_hash", String(64), primary_key=True),
    Column("window", Integer, nullable=False),
    Column("attempts", Integer, nullable=False),
)
migrations = Table(
    "legacy_migrations",
    metadata,
    Column("migration_id", String(128), primary_key=True),
    Column("household_id", String(64), ForeignKey("households.id"), nullable=False),
    Column("manifest", JSON, nullable=False),
    Column("created_at", Integer, nullable=False),
)
TENANT_TABLES = [
    memberships,
    collections,
    collection_grants,
    assets,
    groups,
    group_assets,
    frames,
    frame_grants,
    frame_collections,
    invitations,
    frame_credentials,
    audit_events,
]
jobs = Table(
    "jobs",
    metadata,
    *tenant("jobs"),
    Column("requested_by", String(64), ForeignKey("users.id"), nullable=False),
    Column("collection_id", String(64)),
    Column("kind", String(32), nullable=False),
    Column("state", String(24), nullable=False),
    Column("result_key", String(255)),
    Column("error_code", String(80)),
    Column("created_at", Integer, nullable=False),
    Column("completed_at", Integer),
    Column("dispatch_attempts", Integer, nullable=False, server_default="0"),
    Column("dispatch_token", String(64)),
    Column("dispatch_until", Integer, nullable=False, server_default="0"),
    Column("dispatch_next_at", Integer, nullable=False, server_default="0"),
    ForeignKeyConstraint(
        ["household_id", "collection_id"],
        ["collections.household_id", "collections.id"],
    ),
)
TENANT_TABLES.append(jobs)
authorization_requests = Table(
    "authorization_requests",
    metadata,
    ident(primary_key=True),
    Column("code_challenge", String(128), nullable=False),
    Column("redirect_uri", String(1024), nullable=False),
    Column("state", String(256), nullable=False),
    Column("device_name", String(120), nullable=False),
    Column("expires_at", Integer, nullable=False),
    Column("code_hash", String(64), unique=True),
    Column("user_id", String(64), ForeignKey("users.id")),
    Column("source_session_id", String(64), ForeignKey("device_sessions.id")),
    Column("consumed", Boolean, nullable=False, default=False),
)

# Explicit owner/admin deletion requests. Retain a receipt after the asset row is
# removed; object keys are server-generated and never accepted from the client.
media_purges = Table(
    "media_purges",
    metadata,
    *tenant("media_purges"),
    Column("collection_id", String(64), nullable=False),
    Column("asset_id", String(64), nullable=False),
    Column("requested_by", String(64), ForeignKey("users.id"), nullable=False),
    Column("storage_key", String(255), nullable=False),
    Column("thumbnail_key", String(255)),
    Column("state", String(24), nullable=False),
    Column("error_code", String(80)),
    Column("created_at", Integer, nullable=False),
    Column("completed_at", Integer),
    Column("dispatch_token", String(64)),
    Column("dispatch_until", Integer, nullable=False, server_default="0"),
    Column("dispatch_attempts", Integer, nullable=False, server_default="0"),
    Column("dispatch_next_at", Integer, nullable=False, server_default="0"),
    UniqueConstraint("household_id", "asset_id"),
    ForeignKeyConstraint(
        ["household_id", "collection_id"],
        ["collections.household_id", "collections.id"],
    ),
    CheckConstraint("state IN ('queued','complete','failed')"),
)
TENANT_TABLES.append(media_purges)
