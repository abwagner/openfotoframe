"""Gallery previews reuse display composition without creating saved groups."""
import io
import json
from PIL import Image
import app as photo_app
from tests.conftest import make_test_image


def test_display_gallery_pairs_without_mutating_library(auth_client, monkeypatch, tmp_path):
    snapshots = tmp_path / 'snapshots'
    snapshots.mkdir()
    monkeypatch.setattr(photo_app, 'SNAPSHOT_FOLDER', snapshots)
    names = []
    for index in range(3):
        response = auth_client.post('/api/upload', data={
            'files': (make_test_image(300, 600, 'blue'), f'portrait{index}.png')})
        names.append(response.json['uploaded'][0])
    auth_client.patch('/api/gallery/' + names[2], json={'enabled': False})
    before = photo_app.GALLERY_FILE.read_bytes()
    result = auth_client.get('/api/gallery/display-slides')
    assert result.status_code == 200
    slide, = result.json['slides']
    assert slide['automatic'] and slide['group_id'] is None
    assert set(slide['filenames']) == set(names[:2])
    thumb = auth_client.get(slide['thumbnail_path'])
    assert thumb.status_code == 200
    assert thumb.mimetype == 'image/jpeg'
    with Image.open(io.BytesIO(thumb.data)) as image:
        assert max(image.size) <= 400
        assert image.width > image.height  # composed landscape slide, not original portrait
    assert photo_app.GALLERY_FILE.read_bytes() == before
    assert json.loads(before)['groups'] == {}
    auth_client.patch('/api/gallery/' + names[0], json={'enabled': False})
    assert auth_client.get(slide['thumbnail_path']).status_code == 404


def test_display_gallery_rejects_unknown_profiles_and_slides(auth_client):
    assert auth_client.get('/api/gallery/display-slides/missing').status_code == 404
    assert auth_client.get('/api/gallery/display-thumbnails/default/missing').status_code == 404


def test_display_gallery_requires_user_authentication(client):
    # Simulate a remote request so loopback display access cannot satisfy login.
    assert client.get('/api/gallery/display-slides', environ_base={'REMOTE_ADDR': '203.0.113.4'}).status_code == 401
    assert client.get('/api/gallery/display-thumbnails/default/missing', environ_base={'REMOTE_ADDR': '203.0.113.4'}).status_code == 401
