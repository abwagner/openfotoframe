"""Provider-neutral subscription boundary. No payment provider is enabled by default."""

from .service import BillingError, BillingService, VerifiedBillingEvent


def register(app):
    from .http import blueprint
    from .configuration import configure
    configure(app)
    if "release_billing" not in app.blueprints:
        app.register_blueprint(blueprint)


__all__ = ["register", "BillingError", "BillingService", "VerifiedBillingEvent"]
