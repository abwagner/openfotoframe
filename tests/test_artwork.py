"""Art profile persistence, color independence, display isolation, and permissions."""
import json
from copy import deepcopy
from unittest.mock import patch

import pytest
import app as photo_app
from artwork_catalog import catalog, validate_content


@pytest.fixture(autouse=True)
def art_state(monkeypatch):
    monkeypatch.setattr(photo_app, '_art_display_states', {})


def save_art(client, settings=None, display='default'):
    return client.patch(f'/api/displays/{display}', json={
        'content': {'mode': 'art', 'art': {'settings': settings or {}}}
    })


def test_legacy_profile_defaults_to_photos(auth_client):
    profile = auth_client.get('/api/displays').get_json()['displays'][0]
    assert profile['content']['mode'] == 'photos'
    assert auth_client.get('/api/display/state').get_json()['content_mode'] == 'photos'


def test_art_works_without_photos_and_never_builds_snapshots(auth_client):
    assert save_art(auth_client).status_code == 200
    with patch.object(photo_app, '_build_slides', side_effect=AssertionError('Art must not build slides')):
        state = auth_client.get('/api/display/state').get_json()
        assert state['content_mode'] == 'art'
        assert state['snapshot_url'] is None
        assert state['art']['settings']['cell_colors']
        assert auth_client.post('/api/display/control', json={'action': 'pause'}).get_json()['paused'] is True
        assert auth_client.post('/api/display/control', json={'action': 'play'}).get_json()['paused'] is False
        assert auth_client.post('/api/display/control', json={'action': 'next'}).status_code == 400


def test_palette_and_grout_are_independent_and_persisted(auth_client):
    assert save_art(auth_client, {'grout_color': '#abcdef'}).status_code == 200
    response = save_art(auth_client, {'palette': 'ocean'})
    settings = response.get_json()['display']['content']['art']['settings']
    assert settings['palette'] == 'ocean'
    assert settings['grout_color'] == '#ABCDEF'
    response = save_art(auth_client, {'grout_color': '#ffffff'})
    assert response.get_json()['display']['content']['art']['settings']['palette'] == 'ocean'
    # Backup copies data/settings.json; verify a file round trip includes all art fields.
    disk_settings = json.loads(photo_app.SETTINGS_FILE.read_text())
    photo_app.save_settings(disk_settings)
    state = auth_client.get('/api/display/state').get_json()['art']['settings']
    assert state['palette'] == 'ocean'
    assert state['grout_color'] == '#FFFFFF'


def test_custom_palette_and_mode_switch_preserve_saved_art(auth_client):
    settings = {'palette': 'custom', 'custom_cell_colors': ['#ff0000', '#00ff00'], 'grout_color': '#dddddd'}
    saved = save_art(auth_client, settings).get_json()['display']['content']['art']
    assert saved['settings']['custom_cell_colors'] == ['#FF0000', '#00FF00']
    assert auth_client.get('/api/display/state').get_json()['art']['settings']['cell_colors'] == ['#FF0000', '#00FF00']
    auth_client.patch('/api/displays/default', json={'content': {'mode': 'photos'}})
    assert photo_app.display_profiles(photo_app.load_settings())[0]['content']['art'] == saved
    auth_client.patch('/api/displays/default', json={'content': {'mode': 'art'}})
    assert auth_client.get('/api/display/state').get_json()['art']['settings']['grout_color'] == '#DDDDDD'
    assert save_art(auth_client, {'palette': 'forest'}).get_json()['display']['content']['art']['settings']['custom_cell_colors'] is None


@pytest.mark.parametrize('settings', [
    {'palette': 'unknown'}, {'palette': []}, {'grout_color': 'red'}, {'grout_color': '#FFFFFF00'},
    {'palette': 'custom', 'custom_cell_colors': ['#ffffff']},
    {'palette': 'custom', 'custom_cell_colors': ['#ffffff', 'red']},
    {'palette': 'custom', 'custom_cell_colors': ['#ffffff'] * 13},
    {'cell_density': True}, {'cell_density': 80.5}, {'cell_density': 301},
    {'idle_speed': -1}, {'interaction_strength': 2}, {'idle_speed': float('nan')},
    {'idle_speed': float('inf')}, {'cell_colors': ['#FFFFFF']},
])
def test_invalid_settings_do_not_change_saved_profile(auth_client, settings):
    assert save_art(auth_client).status_code == 200
    before = photo_app.SETTINGS_FILE.read_bytes()
    assert save_art(auth_client, settings).status_code == 400
    assert photo_app.SETTINGS_FILE.read_bytes() == before


@pytest.mark.parametrize('content', [None, [], {'mode': 'video'}, {'art': {'artwork_id': 'https://evil.test/art.js'}},
                                      {'art': {'seed': True}}, {'art': {'seed': -1}},
                                      {'art': {'settings_version': 2}}, {'art': {'settings': []}}])
def test_invalid_content_is_rejected(auth_client, content):
    assert auth_client.patch('/api/displays/default', json={'content': content}).status_code == 400


def test_art_pause_is_separate_from_other_profiles_and_photos(auth_client, monkeypatch):
    monkeypatch.setattr(photo_app, '_display_state', {'index': 3, 'paused': False, 'last_advanced_at': 0})
    assert save_art(auth_client).status_code == 200
    second = auth_client.post('/api/displays', json={'name': 'Other', 'content': {'mode': 'art'}}).get_json()['display']['id']
    auth_client.post('/api/display/control', json={'action': 'pause', 'display': 'default'})
    assert auth_client.get('/api/display/state?display=default').get_json()['paused'] is True
    assert auth_client.get(f'/api/display/state?display={second}').get_json()['paused'] is False
    assert photo_app._display_state['paused'] is False
    assert photo_app._display_state['index'] == 3


def test_color_change_updates_revision_but_not_seed(auth_client):
    save_art(auth_client)
    before = auth_client.get('/api/display/state').get_json()['art']
    save_art(auth_client, {'grout_color': '#FFFFFF'})
    after = auth_client.get('/api/display/state').get_json()['art']
    assert before['revision'] != after['revision']
    assert before['seed'] == after['seed']


def test_catalog_access_uses_display_auth_and_not_profile_write_access(client):
    args = {'base_url': 'https://frame.example', 'environ_base': {'REMOTE_ADDR': '192.0.2.10'}}
    assert client.get('/api/artworks', **args).status_code == 401
    client.post('/api/display/enroll', json={'enrollment_secret': photo_app.DISPLAY_TOKEN}, **args)
    entries = client.get('/api/artworks', **args).get_json()['artworks']
    assert len(entries[0]['palettes']) == 12
    assert client.patch('/api/displays/default', json={'content': {'mode': 'art'}}, **args).status_code == 401


def test_palette_only_configuration_migrates_and_inputs_are_not_mutated():
    original = {'mode': 'art', 'art': {'settings': {'palette': 'ocean'}}}
    before = deepcopy(original)
    resolved = validate_content(original)
    assert original == before
    assert resolved['art']['settings']['palette'] == 'ocean'
    assert resolved['art']['settings']['grout_color'] == '#171411'
    assert resolved['art']['settings']['custom_cell_colors'] is None
    first = catalog()
    first[0]['palettes'][0]['colors'][0] = '#000000'
    assert catalog()[0]['palettes'][0]['colors'][0] == '#D8C3A5'


def test_independent_photos_resume_after_time_spent_in_art(auth_client, monkeypatch):
    now = photo_app.time.time()
    monkeypatch.setattr(photo_app, '_independent_display_states', {
        'default': {'index': 2, 'paused': False, 'last_advanced_at': now}
    })
    auth_client.patch('/api/displays/default', json={'synchronized': False})
    with patch.object(photo_app, '_build_slides', return_value=([{}] * 10, None, None)), \
            patch.object(photo_app.time, 'time', return_value=now + 30):
        assert save_art(auth_client).status_code == 200
    with patch.object(photo_app.time, 'time', return_value=now + 10000):
        auth_client.patch('/api/displays/default', json={'content': {'mode': 'photos'}})
        profile = photo_app.display_profiles(photo_app.load_settings())[0]
        state = photo_app._profile_state(profile)
        assert photo_app._get_effective_index(10, state, photo_app.load_settings()) == 2


def test_catalog_supports_artworks_with_different_settings(auth_client, monkeypatch):
    from artwork_catalog import ARTWORKS
    monkeypatch.setitem(ARTWORKS, 'test-scene', {
        'id': 'test-scene', 'settings_version': 1,
        'defaults': {'background': '#123456', 'speed': 0.2},
        'schema': [{'key': 'background', 'type': 'color'},
                   {'key': 'speed', 'label': 'Speed', 'type': 'number', 'min': 0, 'max': 1}],
    })
    response = auth_client.patch('/api/displays/default', json={'content': {
        'mode': 'art', 'art': {'artwork_id': 'test-scene', 'settings': {'background': '#abcdef'}}
    }})
    assert response.status_code == 200
    state = auth_client.get('/api/display/state').get_json()['art']
    assert state['artwork_id'] == 'test-scene'
    assert state['settings'] == {'background': '#ABCDEF', 'speed': 0.2}
