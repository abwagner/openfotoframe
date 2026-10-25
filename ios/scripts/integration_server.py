"""Isolated real Flask server for simulator integration tests; never uses your data.

Run from the repository root: .venv/bin/python ios/scripts/integration_server.py
Credentials are test fixtures only: admin / ios-integration-password.
"""
import importlib.util
import hashlib
import shutil
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw


def main():
    source = Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(prefix="openfotoframe-ios-test-") as directory:
        target = Path(directory)
        for filename in ("app.py", "render_display.py"):
            shutil.copy2(source / filename, target / filename)
        for folder in ("static", "templates"):
            shutil.copytree(source / folder, target / folder)
        sys.path.insert(0, str(target))
        spec = importlib.util.spec_from_file_location("ios_frame_fixture", target / "app.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.load_users()
        module.change_user_password("admin", "ios-integration-password")
        module.INITIAL_ADMIN_PASSWORD_FILE.unlink(missing_ok=True)
        module.create_user("viewer", "ios-viewer-password", "user")
        module.change_user_password("viewer", "ios-viewer-password")
        module.create_user("firstlogin", "ios-firstlogin-password", "user")
        module.create_user("mfauser", "ios-mfauser-password", "user")
        users = module.load_users()
        users["firstlogin"]["must_change_password"] = True
        users["mfauser"]["mfa"] = {
            "totp_secret": module._encrypt_mfa_secret("JBSWY3DPEHPK3PXP"),
            "passkeys": [],
            "recovery_code_hashes": [hashlib.sha256(b"ios-recovery-code").hexdigest()],
        }
        module.save_users(users)
        settings = module.load_settings()
        settings["mfa_mode"] = "optional"
        module.save_settings(settings)
        for index, color in enumerate(("#0d9488", "#ea9659")):
            name = f"abcdef0{index}_Landscape.jpg"
            image = Image.new("RGB", (1600, 1000), "#dcefea")
            draw = ImageDraw.Draw(image)
            draw.polygon([(0, 1000), (700, 180), (1600, 1000)], fill=color)
            draw.ellipse((1100, 100, 1300, 300), fill="#e6c77c")
            image.save(module.UPLOAD_FOLDER / name)
            module.update_image_metadata(name, title=f"Frame Test Landscape {index + 1}",
                                         enabled=True, uploaded_by="admin", width=1600, height=1000)
            module.generate_thumbnail(module.UPLOAD_FOLDER / name, name)
        # CSRF stays enabled: native mutations must use the real web-session token.
        module.app.run(host="127.0.0.1", port=5059, use_reloader=False)


if __name__ == "__main__":
    main()
