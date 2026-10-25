"""The simulator fixture must work without importing the real server's files."""

import os
from pathlib import Path
import subprocess
import sys
import textwrap


def test_ios_fixture_is_self_contained(tmp_path):
    fixture = Path(__file__).resolve().parents[1] / 'ios/scripts/integration_server.py'
    probe = textwrap.dedent('''
        import re
        import runpy
        import sys
        from pathlib import Path
        from unittest.mock import patch
        from flask import Flask

        def verify(app, *args, **kwargs):
            root = Path(app.root_path)
            assert root.name.startswith('openfotoframe-ios-test-')
            assert Path(sys.modules['artwork_catalog'].__file__).parent == root
            client = app.test_client()
            page = client.get('/login')
            assert page.status_code == 200
            credentials = {'username': 'admin', 'password': 'ios-integration-password'}
            assert client.post('/login', data=credentials).status_code == 400
            credentials['csrf_token'] = re.search(
                r'name="csrf_token" value="([^"]+)"', page.get_data(as_text=True)
            ).group(1)
            response = client.post('/login', data=credentials, follow_redirects=True)
            assert response.status_code == 200
            assert response.request.path == '/upload'
            gallery = client.get('/api/gallery')
            assert gallery.status_code == 200
            assert len(gallery.get_json()['images']) == 2

        with patch.object(Flask, 'run', verify):
            runpy.run_path(sys.argv[1], run_name='__main__')
    ''')
    environment = os.environ.copy()
    environment.update(BEHIND_PROXY='0', AUTHELIA_URL='', SECURE_COOKIES='0')
    result = subprocess.run(
        [sys.executable, '-c', probe, str(fixture)], cwd=tmp_path,
        env=environment, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
