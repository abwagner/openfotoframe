#!/usr/bin/env python3
"""Run a loopback-only, disposable stock-photo demo for screenshots."""
import argparse
from contextlib import contextmanager
from datetime import datetime
import importlib.util
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import sys
import tempfile
import urllib.request


SOURCE = Path(__file__).resolve().parents[1]
CATALOG = SOURCE / 'docs/demo-photos.json'


def stock_photo(photo, cache, offline=False):
    from PIL import Image
    cache.mkdir(parents=True, exist_ok=True)
    destination = cache / f'{photo["id"]}.jpg'
    if not destination.exists():
        if offline:
            raise RuntimeError(f'Missing cached photo {photo["id"]}; run once without --offline')
        url = f'https://picsum.photos/id/{photo["id"]}/1600/1000'
        with urllib.request.urlopen(url, timeout=30) as response:
            contents = response.read(12 * 1024 * 1024 + 1)
        if len(contents) > 12 * 1024 * 1024:
            raise ValueError('Stock image exceeds demo download limit')
        temporary = destination.with_suffix('.download')
        temporary.write_bytes(contents)
        try:
            with Image.open(temporary) as image:
                image.verify()
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
    return destination


@contextmanager
def demo_application(cache, offline=False):
    from PIL import Image
    photos = json.loads(CATALOG.read_text())
    assets = [(photo, stock_photo(photo, cache, offline)) for photo in photos]
    # Set before importing the isolated app: never inherit production SSO/proxy settings.
    os.environ.update(BEHIND_PROXY='0', AUTHELIA_URL='', SECURE_COOKIES='0')
    with tempfile.TemporaryDirectory(prefix='openfotoframe-demo-') as directory:
        target = Path(directory)
        for filename in ('app.py', 'render_display.py', 'artwork_catalog.py'):
            shutil.copy2(SOURCE / filename, target / filename)
        for folder in ('static', 'templates'):
            shutil.copytree(SOURCE / folder, target / folder)
        sys.path.insert(0, str(target))
        try:
            spec = importlib.util.spec_from_file_location('openfotoframe_demo', target / 'app.py')
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            # A demo must not perform backups or hardware scheduling.
            if module.scheduler.running:
                module.scheduler.shutdown(wait=False)
            module.create_user('demo', secrets.token_urlsafe(24), 'admin')
            module.change_user_password('demo', secrets.token_urlsafe(24))
            module.INITIAL_ADMIN_PASSWORD_FILE.unlink(missing_ok=True)
            settings = module.load_settings()
            settings.update(mat_color='#f4f1e9', mat_finish='linen', bevel_width=6,
                            auto_mat_color=False, slideshow_interval=3600, tv_schedules=[])
            module.save_settings(settings)
            names = []
            for index, (photo, cached) in enumerate(assets, 1):
                name = f'd{index:07d}_{photo["title"].replace(" ", "_")}.jpg'
                output = module.UPLOAD_FOLDER / name
                # Write fresh pixels so stock-photo EXIF isn't propagated into the demo.
                with Image.open(cached) as image:
                    rgb = image.convert('RGB')
                    width, height = rgb.size
                    rgb.save(output, quality=92)
                module.update_image_metadata(name, title=photo['title'], enabled=True,
                    uploaded_by='demo', uploaded_at=datetime(2026, 1, index, 12).isoformat(),
                    width=width, height=height, phash=module.compute_phash(output),
                    mat_color='#f4f1e9', mat_finish='linen', bevel_width=6, border_effect='bevel')
                module.generate_thumbnail(output, name)
                names.append(name)
            gallery = module.load_gallery()
            gallery['groups']['group_demo'] = dict(images=names[:2], mat_color='#f4f1e9',
                mat_finish='linen', bevel_width=6, created_at='2026-01-01T12:00:00')
            module.save_gallery(gallery)

            @module.app.before_request
            def local_demo_session():
                # This hook exists only in this disposable copy, never in app.py.
                from flask import abort, request, session
                if request.remote_addr not in ('127.0.0.1', '::1'):
                    abort(403)
                session['authenticated'] = True
                session['username'] = 'demo'

            yield module
        finally:
            sys.path.remove(str(target))
            sys.modules.pop('openfotoframe_demo', None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=5080)
    parser.add_argument('--cache-dir', type=Path, default=Path(
        os.environ.get('XDG_CACHE_HOME', str(Path.home() / '.cache'))) / 'openfotoframe-demo')
    parser.add_argument('--offline', action='store_true', help='Use previously downloaded stock photos only')
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    with demo_application(args.cache_dir, args.offline) as module:
        print(f'Demo gallery: http://127.0.0.1:{args.port}/gallery', flush=True)
        print(f'Demo display: http://127.0.0.1:{args.port}/display', flush=True)
        module.app.run(host='127.0.0.1', port=args.port, debug=False, use_reloader=False)


if __name__ == '__main__':
    main()
