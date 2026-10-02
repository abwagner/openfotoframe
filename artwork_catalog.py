"""Bundled artwork metadata and validation, shared by profiles and the display."""
from copy import deepcopy
import hashlib
import json
import math
import re

PALETTES = [
    ('warm-stone', 'Warm Stone', ['#D8C3A5', '#B89B72', '#8C735B', '#E8DCC8'], '#171411'),
    ('terracotta', 'Terracotta', ['#B85C38', '#D98E64', '#E9BE96', '#8E493A'], '#352820'),
    ('desert', 'Desert', ['#E4C690', '#C59B63', '#D98973', '#8E9B80'], '#514639'),
    ('forest', 'Forest', ['#234D3C', '#52785A', '#8BA67B', '#C2C9A0'], '#101D17'),
    ('ocean', 'Ocean', ['#123B5D', '#236B8E', '#54A6B5', '#B3DAD8'], '#0B1D2B'),
    ('glacier', 'Glacier', ['#DDEBF0', '#AFCBD5', '#779BAE', '#E9E5F2'], '#344853'),
    ('dusk', 'Dusk', ['#5B456B', '#946B8A', '#CD9293', '#E9BFA2'], '#251E30'),
    ('jewel', 'Jewel Tones', ['#176B63', '#324B9B', '#713B89', '#AE365B', '#D6A440'], '#15121D'),
    ('citrus', 'Citrus', ['#F2C94C', '#E89B35', '#A8BA54', '#F3DEA0'], '#383820'),
    ('pastel', 'Soft Pastels', ['#EBC7D2', '#C8D7EC', '#CDE2D0', '#F0E0B9'], '#F5F0E8'),
    ('earth', 'Earth Neutrals', ['#6D6257', '#9B8B78', '#C5B8A6', '#E2D8C9'], '#302B26'),
    ('monochrome', 'Monochrome', ['#262626', '#666666', '#AAAAAA', '#E5E5E5'], '#101010'),
]
DEFAULT_SETTINGS = {'palette': 'warm-stone', 'custom_cell_colors': None,
                    'grout_color': '#171411', 'cell_density': 80,
                    'idle_speed': 0.05, 'interaction_strength': 0.7}
ARTWORKS = {
    'organic-cells': {
        'id': 'organic-cells', 'name': 'Organic Cells',
        'description': 'Slowly shifting stained-glass cells with independent cell and grout colors.',
        'module': '/static/js/artworks/organic-cells.js',
        'preview': '/static/artworks/organic-cells-preview.svg',
        'settings_version': 1, 'defaults': DEFAULT_SETTINGS,
        'capabilities': {'interaction': True, 'renderer': 'canvas'},
        'schema': [
            {'key': 'palette', 'label': 'Cell palette', 'type': 'palette'},
            {'key': 'custom_cell_colors', 'label': 'Custom cell colors', 'type': 'color-list', 'min': 2, 'max': 12},
            {'key': 'grout_color', 'label': 'Grout color', 'type': 'color'},
            {'key': 'cell_density', 'label': 'Cell count', 'type': 'integer', 'min': 20, 'max': 300, 'step': 1},
            {'key': 'idle_speed', 'label': 'Color drift', 'type': 'number', 'min': 0, 'max': 1, 'step': 0.01},
            {'key': 'interaction_strength', 'label': 'Viewer influence', 'type': 'number', 'min': 0, 'max': 1, 'step': 0.01},
        ],
        'palettes': [{'id': key, 'name': name, 'colors': colors, 'grout_color': grout}
                     for key, name, colors, grout in PALETTES],
    }
}


def catalog():
    return deepcopy(list(ARTWORKS.values()))


def _color(value):
    if not isinstance(value, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', value):
        raise ValueError('Colors must use opaque #RRGGBB values')
    return value.upper()


def validate_content(value, previous=None):
    """Validate a partial content update without modifying its input or old values."""
    if not isinstance(value, dict) or set(value) - {'mode', 'art'}:
        raise ValueError('Content must contain only mode and art')
    old = deepcopy(previous or {})
    mode = value.get('mode', old.get('mode', 'photos'))
    if mode not in ('photos', 'art'):
        raise ValueError('Content mode must be photos or art')
    incoming = value.get('art', {})
    if not isinstance(incoming, dict) or set(incoming) - {'artwork_id', 'settings_version', 'seed', 'settings'}:
        raise ValueError('Invalid artwork configuration')
    art = {**old.get('art', {}), **incoming}
    artwork_id = art.get('artwork_id', 'organic-cells')
    if not isinstance(artwork_id, str) or artwork_id not in ARTWORKS:
        raise ValueError('Unknown artwork')
    entry = ARTWORKS[artwork_id]
    version = art.get('settings_version', entry['settings_version'])
    if type(version) is not int or version != entry['settings_version']:
        raise ValueError('Unsupported artwork settings version')
    seed = art.get('seed', 42173)
    if type(seed) is not int or not 0 <= seed <= 2147483647:
        raise ValueError('Seed must be an integer from 0 to 2147483647')
    updates = incoming.get('settings', {})
    if not isinstance(updates, dict) or set(updates) - set(entry['defaults']):
        raise ValueError('Unknown artwork settings')
    prior = old.get('art', {}).get('settings', {}) if old.get('art', {}).get('artwork_id', artwork_id) == artwork_id else {}
    settings = {**deepcopy(entry['defaults']), **prior, **updates}
    if entry.get('palettes'):
        palette = settings['palette']
        if not isinstance(palette, str) or palette not in {p['id'] for p in entry['palettes']} | {'custom'}:
            raise ValueError('Unknown cell palette')
        if palette == 'custom':
            colors = settings['custom_cell_colors']
            if not isinstance(colors, list) or not 2 <= len(colors) <= 12:
                raise ValueError('A custom cell palette needs 2–12 colors')
            settings['custom_cell_colors'] = [_color(c) for c in colors]
        else:
            if updates.get('custom_cell_colors') is not None:
                raise ValueError('Custom colors require the custom palette')
            settings['custom_cell_colors'] = None
    for field in entry['schema']:
        if field['type'] == 'color':
            settings[field['key']] = _color(settings[field['key']])
        if field['type'] not in ('integer', 'number'):
            continue
        number = settings[field['key']]
        valid_type = type(number) is int if field['type'] == 'integer' else type(number) in (int, float)
        if not valid_type or not field['min'] <= number <= field['max'] or not math.isfinite(number):
            raise ValueError(f"{field['label']} must be between {field['min']} and {field['max']}")
    return {'mode': mode, 'art': {'artwork_id': artwork_id, 'settings_version': version,
                                 'seed': seed, 'settings': settings}}


def resolve_art(content):
    art = deepcopy(content['art'])
    entry = ARTWORKS[art['artwork_id']]
    settings = art['settings']
    if entry.get('palettes'):
        settings['cell_colors'] = (settings['custom_cell_colors'] if settings['palette'] == 'custom'
                                  else next(p['colors'] for p in entry['palettes'] if p['id'] == settings['palette']))
    art['revision'] = hashlib.sha256(json.dumps(art, sort_keys=True).encode()).hexdigest()[:16]
    return art
