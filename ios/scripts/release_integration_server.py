"""Disposable v1 backend fixture for native onboarding checks; no live state is read.

Run from the repository root with requirements-release.txt installed. Binds only
127.0.0.1:5081. Login: native@example.test / Native-test-password-28!.
"""
from io import BytesIO
from pathlib import Path
import sys
import tempfile

from cryptography.fernet import Fernet
from PIL import Image


def main():
    source = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(source))
    from release_backend import create_app
    with tempfile.TemporaryDirectory(prefix="openfotoframe-native-release-") as directory:
        root = Path(directory)
        app = create_app({
            "TESTING": True, "INIT_SCHEMA": True,
            "RELEASE_MODE": "self_hosted",
            "DATABASE_URL": "sqlite:///" + str(root / "fixture.db"),
            "MEDIA_ROOT": str(root / "media"),
            "PUBLIC_ORIGIN": "http://127.0.0.1:5081",
            "SECRET_KEY": "local-fixture-only-do-not-use-in-deployment",
            "BOOTSTRAP_SECRET": "native-fixture-bootstrap",
            "MFA_ENCRYPTION_KEY": Fernet.generate_key().decode(),
        })
        if "release_compat" not in app.blueprints:
            from release_web import register
            register(app)
        client = app.test_client()
        auth = client.post("/api/v1/auth/bootstrap", json={
            "bootstrap_secret": "native-fixture-bootstrap", "email": "native@example.test",
            "password": "Native-test-password-28!", "household_name": "Native Fixture",
            "device_name": "Fixture setup",
        })
        assert auth.status_code == 200, auth.get_json()
        session = auth.get_json()
        hid = session["households"][0]["id"]
        headers = {"Authorization": "Bearer " + session["access_token"]}
        created = client.post(f"/api/v1/households/{hid}/collections", json={"name": "Fixture Photos"}, headers=headers)
        assert created.status_code == 201, created.get_json()
        cid = created.get_json()["id"]
        frame = client.post(f"/api/v1/households/{hid}/frames", json={"name": "Fixture Frame"}, headers=headers)
        assert frame.status_code == 201, frame.get_json()
        fid = frame.get_json()["id"]
        assigned = client.patch(f"/api/v1/households/{hid}/frames/{fid}", json={"collection_ids": [cid]}, headers=headers)
        assert assigned.status_code == 200, assigned.get_json()
        content = BytesIO()
        Image.new("RGB", (1600, 1000), "#4c8f85").save(content, "JPEG")
        uploaded = client.post(f"/api/v1/households/{hid}/collections/{cid}/assets", data={"file": (BytesIO(content.getvalue()), "Native-fixture.jpg")}, headers=headers, content_type="multipart/form-data")
        assert uploaded.status_code == 201, uploaded.get_json()
        app.run(host="127.0.0.1", port=5081, use_reloader=False)


if __name__ == "__main__":
    main()
