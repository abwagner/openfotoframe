import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../static/js/upload.js', import.meta.url), 'utf8');

test('automatic pairs render once as rectangular thumbnails and offer editing only after opening', () => {
    const helper = source.match(/function escAttr\(str\) \{[^\n]*\n[^\n]*\n\s*\}/)[0];
    const renderer = source.slice(source.indexOf('function renderDisplayedGallery()'), source.indexOf('function renderGallery()'));
    const grid = {style: {}, innerHTML: ''};
    const note = {};
    const controls = {innerHTML: ''};
    vm.runInNewContext(helper + renderer + '\nrenderDisplayedGallery();', {
        document: {getElementById: () => note, querySelector: () => ({style: {}})}, galleryGrid: grid, emptyState: {style: {}},
        previewSection: {classList: {add() {}}}, previewControls: {classList: {add() {}}},
        previewControlsTitle: {}, previewControlsBody: controls, matPreview: {style: {}}, previewContent: {},
        displayedError: null, images: [], displayedProfile: {name: '<TV>', width: 1920, height: 1080},
        displayedSlides: [{automatic: true, group_id: null, filenames: ['a.jpg', 'b.jpg'], thumbnail_path: '/api/gallery/display-thumbnails/default/pair'}],
        cleanFilename: value => value,
    });
    assert.equal((grid.innerHTML.match(/class="image-card display-slide-card"/g) || []).length, 1);
    assert.match(grid.innerHTML, /aria-label="Preview automatic pair"/);
    assert.match(grid.innerHTML, /aspect-ratio:1920\/1080/);
    assert.doesNotMatch(grid.innerHTML, /Edit photo|card-info|card-meta/);
    assert.match(grid.innerHTML, /openSlidePreview\(0\)/);
    assert.match(grid.innerHTML, /src="\/api\/gallery\/display-thumbnails/);
    assert.doesNotMatch(grid.innerHTML, /ungroupGroup|\/thumbnails\//);
    assert.match(note.textContent, /1 displayed slides · <TV>/);

    const context = {
        document: {querySelector: () => ({style: {}})},
        displayedSlides: [{automatic: true, group_id: null, filenames: ['a.jpg','b.jpg'], thumbnail_path:'/api/gallery/display-thumbnails/default/pair'}],
        displayedProfile: {width:1920,height:1080}, previewControlsBody:controls,
        previewSection:{classList:{add(){}}},previewControls:{classList:{add(){}}},
        previewControlsTitle:{},matPreview:{style:{}},previewContent:{},
    };
    vm.runInNewContext(helper + renderer + '\nopenSlidePreview(0);', context);
    assert.match(controls.innerHTML, /Edit photo 1/);
    assert.match(controls.innerHTML, /Edit photo 2/);
});

test('CSP event bindings reach gallery source, display selector, and photo editors', () => {
    const calls = [];
    const ready = {};
    const actions = ['toggleGallerySource()', 'selectGalleryDisplay(this.value)', "openPreview('a.jpg')", 'openSlidePreview(0)'];
    const elements = actions.map(action => {
        const attribute = action.startsWith('select') ? 'data-onchange' : 'data-onclick';
        const attrs = {[attribute]: action};
        return {value: 'frame_two', events: {}, hasAttribute: key => key in attrs,
            getAttribute: key => attrs[key], removeAttribute: key => delete attrs[key],
            addEventListener(type, callback) { this.events[type] = callback; }};
    });
    const context = vm.createContext({document: {querySelectorAll: () => elements,
        addEventListener(type, callback) { ready[type] = callback; }},
        MutationObserver: class { observe() {} },
        toggleGallerySource: () => calls.push('toggle'),
        selectGalleryDisplay: id => calls.push(id), openPreview: name => calls.push(name),openSlidePreview: index => calls.push(index)});
    context.window = context;
    vm.runInContext(fs.readFileSync(new URL('../static/js/legacy-events.js', import.meta.url), 'utf8'), context);
    ready.DOMContentLoaded();
    elements[0].events.click(); elements[1].events.change(); elements[2].events.click(); elements[3].events.click();
    assert.deepEqual(calls, ['toggle', 'frame_two', 'a.jpg', 0]);
});
