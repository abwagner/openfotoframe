import { ArtworkHost } from './display-art.js';

function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text != null) node.textContent = text;
    if (className) node.className = className;
    return node;
}
function select(options, value) {
    const node = element('select');
    for (const [id, name] of options) {
        const option = element('option', name);
        option.value = id;
        node.append(option);
    }
    node.value = value;
    return node;
}
function button(text, action) {
    const node = element('button', text, 'btn btn-edit');
    node.type = 'button';
    node.addEventListener('click', action);
    return node;
}
function field(parent, label, input) {
    if (input.matches('input, select')) input.setAttribute('aria-label', label);
    const node = element('label', null, 'art-field');
    node.append(element('span', label), input);
    parent.append(node);
}
const clone = value => JSON.parse(JSON.stringify(value));
const validColor = value => /^#[\da-f]{6}$/i.test(value);

export function createArtEditor(container, profile, catalog, { onSaved, onClose }) {
    container.replaceChildren();
    container.hidden = false;
    const content = clone(profile.content);
    let entry, host, revision = 0, disposed = false;
    const heading = element('div', null, 'art-editor-heading');
    heading.append(element('h3', `${profile.name} — Content`), button('Close', onClose));
    container.append(heading);
    const mode = select([['photos', 'Photos'], ['art', 'Art']], content.mode);
    field(container, 'Content', mode);
    const artSection = element('div');
    container.append(artSection);
    const artworkSelect = select(catalog.artworks.map(a => [a.id, a.name]), content.art.artwork_id);
    field(artSection, 'Artwork', artworkSelect);
    const description = element('p', null, 'art-help');
    artSection.append(description);
    const preview = element('div', null, 'art-preview');
    preview.style.aspectRatio = `${profile.width} / ${profile.height}`;
    artSection.append(preview);
    artSection.append(element('p', 'Move across the preview to simulate a viewer. Changes stay here until saved.', 'art-help'));
    let previewPaused = false;
    const pause = button('Pause preview', () => {
        previewPaused = !previewPaused;
        host.setPaused(previewPaused);
        pause.textContent = previewPaused ? 'Play preview' : 'Pause preview';
    });
    artSection.append(pause);
    const form = element('div', null, 'art-settings');
    artSection.append(form);
    const message = element('p', null, 'art-help');
    message.setAttribute('role', 'status');
    const save = button('Save content', async () => {
        save.disabled = true;
        message.textContent = 'Saving…';
        try {
            const response = await fetch(`/api/displays/${encodeURIComponent(profile.id)}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': document.querySelector('meta[name="csrf-token"]').content },
                body: JSON.stringify({ content })
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || 'Could not save content');
            if (!disposed) { message.textContent = 'Saved'; onSaved(data.display); }
        } catch (error) { if (!disposed) message.textContent = error.message; }
        finally { if (!disposed) save.disabled = false; }
    });
    container.append(save, message);
    host = new ArtworkHost(preview, { simulate: true, onError: () => { message.textContent = 'Preview could not render. Try another artwork or reopen this editor.'; } });
    let colorwayStatus, swatchButtons = [];

    function refresh() {
        if (disposed) return;
        artSection.hidden = content.mode !== 'art';
        const settings = content.art.settings;
        if (colorwayStatus) {
            const matching = entry.palettes?.find(p => p.id === settings.palette && p.grout_color.toUpperCase() === settings.grout_color.toUpperCase());
            colorwayStatus.textContent = matching ? `Colorway: ${matching.name}` : 'Colorway: Customized';
            swatchButtons.forEach(([node, id]) => node.setAttribute('aria-pressed', String(matching?.id === id)));
        }
        const colors = settings.palette === 'custom' ? settings.custom_cell_colors : entry.palettes?.find(p => p.id === settings.palette)?.colors;
        if (entry.palettes?.length && (!colors?.every(validColor) || colors.length < 2 || colors.length > 12)) return;
        if (!Number.isInteger(content.art.seed) || content.art.seed < 0 || content.art.seed > 2147483647) return;
        for (const spec of entry.schema) {
            if (spec.type === 'color' && !validColor(settings[spec.key])) return;
            if (spec.type === 'number' || spec.type === 'integer') {
                const value = settings[spec.key];
                if (!Number.isFinite(value) || value < spec.min || value > spec.max || (spec.type === 'integer' && !Number.isInteger(value))) return;
            }
        }
        host.configure({ ...content.art, revision: String(++revision), settings: { ...settings, ...(colors ? { cell_colors: colors } : {}) } }, entry);
    }

    function colorControl(parent, label, key) {
        const row = element('div', null, 'art-color-control');
        const picker = element('input');
        picker.type = 'color'; picker.value = content.art.settings[key]; picker.setAttribute('aria-label', label);
        const hex = element('input');
        hex.type = 'text'; hex.value = content.art.settings[key]; hex.maxLength = 7;
        hex.pattern = '#[0-9a-fA-F]{6}'; hex.setAttribute('aria-label', `${label} hex`);
        hex.addEventListener('input', () => {
            content.art.settings[key] = hex.value;
            if (validColor(hex.value)) picker.value = hex.value;
            refresh();
        });
        picker.addEventListener('input', () => { hex.value = picker.value; content.art.settings[key] = picker.value; refresh(); });
        row.append(picker, hex);
        field(parent, label, row);
        const swatches = element('div', null, 'art-grout-swatches');
        for (const [name, color] of [['Near black', '#171411'], ['Charcoal', '#303030'], ['Warm brown', '#514639'], ['Deep navy', '#0B1D2B'], ['Forest green', '#101D17'], ['Gray', '#808080'], ['Ivory', '#F5F0E8'], ['White', '#FFFFFF']]) {
            const swatch = button(name, () => { picker.value = hex.value = color; content.art.settings[key] = color; refresh(); });
            swatch.style.backgroundColor = color;
            swatch.style.color = ['Ivory', 'White'].includes(name) ? '#171411' : '#ffffff';
            swatch.title = `${name} ${color}`;
            swatches.append(swatch);
        }
        parent.append(swatches);
    }

    function buildForm() {
        entry = catalog.artworks.find(a => a.id === content.art.artwork_id);
        description.textContent = entry.description;
        form.replaceChildren(); swatchButtons = []; colorwayStatus = null;
        if (entry.palettes?.length) {
            form.append(element('h4', 'Colorways'));
            const swatches = element('div', null, 'art-colorways');
            for (const palette of entry.palettes) {
                const node = button(palette.name, () => {
                    content.art.settings.palette = palette.id;
                    content.art.settings.custom_cell_colors = null;
                    content.art.settings.grout_color = palette.grout_color;
                    buildForm(); refresh();
                });
                node.className = 'art-colorway';
                const strip = element('span', null, 'art-palette-strip');
                strip.style.background = palette.grout_color;
                for (const color of palette.colors) {
                    const tile = element('span'); tile.style.background = color; strip.append(tile);
                }
                node.prepend(strip); swatches.append(node); swatchButtons.push([node, palette.id]);
            }
            form.append(swatches);
            colorwayStatus = element('p', null, 'art-help'); form.append(colorwayStatus);
        }
        for (const spec of entry.schema) {
            const settings = content.art.settings;
            if (spec.type === 'palette') {
                const input = select([...entry.palettes.map(p => [p.id, p.name]), ['custom', 'Custom']], settings[spec.key]);
                input.addEventListener('change', () => {
                    const colors = settings.palette === 'custom' ? settings.custom_cell_colors : entry.palettes.find(p => p.id === settings.palette).colors;
                    settings.palette = input.value;
                    settings.custom_cell_colors = input.value === 'custom' ? [...colors] : null;
                    buildForm(); refresh();
                });
                field(form, spec.label, input);
            } else if (spec.type === 'color') {
                colorControl(form, spec.label, spec.key);
            } else if (spec.type === 'color-list' && settings.palette === 'custom') {
                const list = element('div', null, 'art-custom-colors');
                list.append(element('h4', spec.label));
                const colors = settings[spec.key];
                colors.forEach((color, index) => {
                    const row = element('div', null, 'art-custom-row');
                    const picker = element('input'); picker.type = 'color'; picker.value = color;
                    picker.setAttribute('aria-label', `Cell color ${index + 1}`);
                    const hex = element('input'); hex.type = 'text'; hex.value = color; hex.maxLength = 7;
                    hex.setAttribute('aria-label', `Cell color ${index + 1} hex`);
                    picker.addEventListener('input', () => { colors[index] = hex.value = picker.value; refresh(); });
                    hex.addEventListener('input', () => { colors[index] = hex.value; if (validColor(hex.value)) picker.value = hex.value; refresh(); });
                    row.append(picker, hex);
                    const up = button('↑', () => { [colors[index - 1], colors[index]] = [colors[index], colors[index - 1]]; buildForm(); refresh(); });
                    up.disabled = index === 0; up.setAttribute('aria-label', `Move color ${index + 1} earlier`);
                    const down = button('↓', () => { [colors[index + 1], colors[index]] = [colors[index], colors[index + 1]]; buildForm(); refresh(); });
                    down.disabled = index === colors.length - 1; down.setAttribute('aria-label', `Move color ${index + 1} later`);
                    const remove = button('Remove', () => { colors.splice(index, 1); buildForm(); refresh(); });
                    remove.disabled = colors.length <= spec.min;
                    row.append(up, down, remove); list.append(row);
                });
                const add = button('Add color', () => { colors.push('#E8DCC8'); buildForm(); refresh(); });
                add.disabled = colors.length >= spec.max; list.append(add); form.append(list);
            } else if (spec.type === 'integer' || spec.type === 'number') {
                const input = element('input'); input.type = 'number';
                input.min = spec.min; input.max = spec.max; input.step = spec.step; input.value = settings[spec.key];
                input.addEventListener('input', () => { settings[spec.key] = input.value === '' ? null : Number(input.value); refresh(); });
                field(form, spec.label, input);
            }
        }
        const seed = element('input'); seed.type = 'number'; seed.min = 0; seed.max = 2147483647; seed.step = 1; seed.value = content.art.seed;
        seed.addEventListener('input', () => { content.art.seed = seed.value === '' ? null : Number(seed.value); refresh(); });
        field(form, 'Pattern seed', seed);
        form.append(button('New pattern', () => { content.art.seed = crypto.getRandomValues(new Uint32Array(1))[0] & 0x7fffffff; seed.value = content.art.seed; refresh(); }));
    }
    mode.addEventListener('change', () => { content.mode = mode.value; refresh(); });
    artworkSelect.addEventListener('change', () => {
        const next = catalog.artworks.find(a => a.id === artworkSelect.value);
        content.art = { artwork_id: next.id, settings_version: next.settings_version, seed: content.art.seed, settings: clone(next.defaults) };
        buildForm(); refresh();
    });
    buildForm(); refresh();
    return { dispose() { disposed = true; host.dispose(); container.replaceChildren(); container.hidden = true; } };
}
