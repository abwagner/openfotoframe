"""The same palette is served to web management and bundled in the iOS app."""
import re


def test_shared_palette_is_available_to_web(client):
    response = client.get('/static/mat-colors.json')
    assert response.status_code == 200
    palette = response.get_json()
    assert len(palette['neutrals']) == 10
    assert len(palette['accents']) == 25
    presets = palette['neutrals'] + palette['accents']
    assert len({preset['color'] for preset in presets}) == 35
    assert all(re.fullmatch(r'#[0-9a-f]{6}', preset['color']) for preset in presets)
    assert next(p for p in presets if p['title'] == 'Cream')['color'] == '#f5f0e6'


def test_management_theme_does_not_replace_display_theme(client):
    login = client.get('/login').get_data(as_text=True)
    assert 'css/theme.css' in login
    display = client.get('/display', base_url='http://localhost').get_data(as_text=True)
    assert 'css/theme.css' not in display
