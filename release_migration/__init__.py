"""Offline legacy migration; importing this package never opens production data."""

from .plan import MigrationError, MigrationPlan, plan_legacy_import

__all__ = ["MigrationError", "MigrationPlan", "plan_legacy_import"]
