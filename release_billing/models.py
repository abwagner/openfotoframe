"""Billing rows never create membership or collection/frame grants."""

from sqlalchemy import Table, Column, String, Integer, BigInteger, JSON, ForeignKey, UniqueConstraint
from release_backend.models import metadata

checkout_requests = Table("billing_checkout_requests", metadata,
    Column("id", String(64), primary_key=True),
    Column("household_id", String(64), ForeignKey("households.id"), nullable=False),
    Column("provider", String(40), nullable=False), Column("plan_id", String(120), nullable=False),
    Column("request_key", String(120), nullable=False), Column("checkout_url", String(2048)),
    Column("created_at", Integer, nullable=False),
    UniqueConstraint("household_id", "request_key"))

subscriptions = Table("billing_subscriptions", metadata,
    Column("household_id", String(64), ForeignKey("households.id"), primary_key=True),
    Column("provider", String(40), nullable=False), Column("subscription_id", String(255), nullable=False),
    Column("plan_id", String(120), nullable=False), Column("status", String(30), nullable=False),
    Column("valid_until", BigInteger, nullable=False), Column("revision", BigInteger, nullable=False),
    Column("updated_at", Integer, nullable=False),
    UniqueConstraint("provider", "subscription_id"))

subscription_bindings = Table("billing_subscription_bindings", metadata,
    Column("provider", String(40), primary_key=True), Column("subscription_id", String(255), primary_key=True),
    Column("household_id", String(64), ForeignKey("households.id"), nullable=False),
    Column("checkout_reference", String(64), ForeignKey("billing_checkout_requests.id"), nullable=False),
    Column("last_revision", BigInteger, nullable=False, default=-1),
    Column("last_status", String(30)), Column("last_plan_id", String(120)),
    Column("last_valid_until", BigInteger),
    Column("created_at", Integer, nullable=False))

events = Table("billing_events", metadata,
    Column("provider", String(40), primary_key=True), Column("event_id", String(255), primary_key=True),
    Column("household_id", String(64), ForeignKey("households.id"), nullable=False),
    Column("payload_hash", String(64), nullable=False), Column("result", JSON, nullable=False),
    Column("created_at", Integer, nullable=False))

apple_accounts = Table("billing_apple_accounts", metadata,
    Column("household_id", String(64), ForeignKey("households.id"), primary_key=True),
    Column("app_account_token", String(36), unique=True, nullable=False),
    Column("created_at", Integer, nullable=False))

TENANT_TABLES = [checkout_requests, subscriptions, subscription_bindings, events, apple_accounts]
