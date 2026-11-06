import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../static/js/display-fullscreen.js', import.meta.url), 'utf8');

function display(active = false) {
    const listeners = {};
    const buttons = {
        'fullscreen-btn': { style: {}, disabled: false },
        'exit-fullscreen-btn': { hidden: true },
    };
    const calls = { enter: 0, exit: 0 };
    const document = {
        fullscreenElement: active ? {} : null,
        documentElement: {
            async requestFullscreen() { calls.enter++; document.fullscreenElement = this; },
        },
        async exitFullscreen() { calls.exit++; document.fullscreenElement = null; },
        getElementById(id) { return buttons[id]; },
        addEventListener(name, callback) { listeners[name] = callback; },
    };
    const context = vm.createContext({ document,
        addEventListener(name, callback) { listeners[name] = callback; },
    });
    context.window = context;
    vm.runInContext(source, context);
    return { context, document, buttons, calls, listeners };
}

test('repeated scheduled entry enters once and never exits', async () => {
    const d = display();
    await d.context.enterFullscreen();
    await d.context.enterFullscreen();
    assert.deepEqual(d.calls, { enter: 1, exit: 0 });
});

test('restored fullscreen keeps the entry slot inactive and exit separate', async () => {
    const d = display(true);
    await d.context.enterFullscreen();
    assert.deepEqual(d.calls, { enter: 0, exit: 0 });
    assert.equal(d.buttons['fullscreen-btn'].disabled, true);
    assert.equal(d.buttons['exit-fullscreen-btn'].hidden, false);
});

test('fullscreen changes and restored pages synchronize both controls', () => {
    const d = display();
    d.document.fullscreenElement = {};
    d.listeners.fullscreenchange();
    assert.equal(d.buttons['fullscreen-btn'].disabled, true);
    d.document.fullscreenElement = null;
    d.listeners.pageshow();
    assert.equal(d.buttons['fullscreen-btn'].disabled, false);
    assert.equal(d.buttons['exit-fullscreen-btn'].hidden, true);
});

test('explicit exit and the F-key toggle still work', async () => {
    const d = display();
    await d.context.exitFullscreen();
    assert.equal(d.calls.exit, 0);
    await d.context.toggleFullscreen();
    await d.context.toggleFullscreen();
    assert.deepEqual(d.calls, { enter: 1, exit: 1 });
});

test('denied entry leaves the controls available and never tries exiting', async () => {
    const d = display();
    d.document.documentElement.requestFullscreen = async () => { throw new Error('Gesture required'); };
    await d.context.enterFullscreen();
    assert.equal(d.document.fullscreenElement, null);
    assert.equal(d.buttons['fullscreen-btn'].disabled, false);
    assert.equal(d.calls.exit, 0);
});

test('display markup uses separate entry and exit actions and preserves Full text', () => {
    const html = readFileSync(new URL('../templates/display.html', import.meta.url), 'utf8');
    assert.match(html, /id="fullscreen-btn" data-onclick="enterFullscreen\(\)">⛶ Full<\/button>/);
    assert.match(html, /class="control-btn fullscreen-exit" id="exit-fullscreen-btn" data-onclick="exitFullscreen\(\)" hidden/);
    assert.ok(html.indexOf('js/display-fullscreen.js') < html.indexOf('js/display.js'));
});

test('real CSP event bindings reach the entry and exit handlers', () => {
    const d = display();
    for (const [id, button] of Object.entries(d.buttons)) {
        const attributes = { 'data-onclick': id === 'fullscreen-btn' ? 'enterFullscreen()' : 'exitFullscreen()' };
        button.events = {};
        button.hasAttribute = name => name in attributes;
        button.getAttribute = name => attributes[name];
        button.removeAttribute = name => delete attributes[name];
        button.addEventListener = (name, callback) => { button.events[name] = callback; };
    }
    d.document.querySelectorAll = () => Object.values(d.buttons);
    d.context.MutationObserver = class { observe() {} };
    vm.runInContext(readFileSync(new URL('../static/js/legacy-events.js', import.meta.url), 'utf8'), d.context);
    d.listeners.DOMContentLoaded();
    d.buttons['fullscreen-btn'].events.click();
    d.buttons['fullscreen-btn'].events.click();
    assert.deepEqual(d.calls, { enter: 1, exit: 0 });
    d.buttons['exit-fullscreen-btn'].events.click();
    assert.deepEqual(d.calls, { enter: 1, exit: 1 });
});
