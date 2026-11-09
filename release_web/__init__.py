"""Scoped compatibility UI for the opt-in release server."""


def register(app):
    from pathlib import Path
    from jinja2 import ChoiceLoader, FileSystemLoader

    root = Path(__file__).resolve().parents[1]
    app.static_folder = str(root / "static")
    app.jinja_loader = ChoiceLoader(
        [app.jinja_loader, FileSystemLoader(root / "templates")]
    )
    from .compat import blueprint
    from .pages import pages
    from .display import display_pages
    from .accounts import account_pages
    from .recovery import recovery_pages
    from .backups import backup_pages
    from .frame_settings import frame_settings

    app.register_blueprint(blueprint)
    app.register_blueprint(pages)
    app.register_blueprint(display_pages)
    app.register_blueprint(frame_settings)
    app.register_blueprint(account_pages)
    app.register_blueprint(recovery_pages)
    app.register_blueprint(backup_pages)
