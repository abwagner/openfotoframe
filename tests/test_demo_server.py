"""The screenshot demo must stay local and never use the real app's data."""
from pathlib import Path
import subprocess
import sys
import textwrap


def test_demo_is_disposable_and_loopback_only(tmp_path):
    script = Path(__file__).resolve().parents[1] / 'scripts/demo_server.py'
    probe = textwrap.dedent('''
        import importlib.util
        import json
        from pathlib import Path
        import sys
        from PIL import Image

        script, cache = Path(sys.argv[1]), Path(sys.argv[2])
        cache.mkdir()
        photos = json.loads((script.parents[1] / 'docs/demo-photos.json').read_text())
        for photo in photos:
            Image.new('RGB', (160, 100), '#609080').save(cache / (photo['id'] + '.jpg'))
        spec = importlib.util.spec_from_file_location('demo_server', script)
        demo = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(demo)
        with demo.demo_application(cache, offline=True) as module:
            root = Path(module.app.root_path)
            assert root.name.startswith('openfotoframe-demo-')
            assert module.DATA_FOLDER.parent == root
            assert module.UPLOAD_FOLDER.parent == root
            assert Path(sys.modules['artwork_catalog'].__file__).parent == root
            assert Path(sys.modules['render_display'].__file__).parent == root
            assert not module.scheduler.running
            client = module.app.test_client()
            assert client.get('/gallery', follow_redirects=True).status_code == 200
            gallery = client.get('/api/gallery').get_json()
            assert len(gallery['images']) == 6
            assert len(gallery['groups']) == 1
            assert client.get('/display').status_code == 200
            assert client.post('/api/display/control', json={'action': 'next'}).status_code == 400
            assert client.get('/gallery', environ_overrides={'REMOTE_ADDR': '192.0.2.10'}).status_code == 403
        assert not root.exists()
    ''')
    result = subprocess.run([sys.executable, '-c', probe, str(script), str(tmp_path / 'cache')],
                            cwd=tmp_path, capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stderr
