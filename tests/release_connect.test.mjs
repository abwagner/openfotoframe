import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../release_web/static/connect.js', import.meta.url), 'utf8');
function landing(fragment, origin = 'https://frame.example.com') {
    const elements = new Map();
    const node = id => {
        if (!elements.has(id)) elements.set(id, {
            hidden: ['connection', 'browser-form'].includes(id), attributes: {}, events: {},
            textContent: '', disabled: true, checked: false,
            setAttribute(k, v) { this.attributes[k] = v; },
            removeAttribute(k) { delete this[k]; delete this.attributes[k]; },
            addEventListener(k, fn) { this.events[k] = fn; },
        });
        return elements.get(id);
    };
    const navigation = [];
    vm.runInNewContext(source, {
        URL, URLSearchParams, location: {hash: '#' + fragment, pathname: '/connect', origin, assign(url) { navigation.push(url); }},
        history: {replaceState(...args) { navigation.push(args); }},
        document: {getElementById: node},
        fetch() { throw new Error('Landing page must not fetch a destination.'); },
    });
    return {node, navigation};
}
function link(fields = {}) {
    return new URLSearchParams({v:'1', purpose:'invite', mode:'self_hosted',
        server:'https://frame.example.com', token:'an-opaque-invitation-secret', ...fields}).toString();
}

test('invite destination is visible, fragment is removed, and navigation requires confirmation', () => {
    const {node, navigation} = landing(link());
    assert.equal(node('link-error').textContent, '');
    assert.equal(node('destination').textContent, 'https://frame.example.com');
    assert.equal(node('open-app').href, undefined);
    assert.equal(node('continue-browser').disabled, true);
    assert.equal(navigation[0][2], '/connect');
    node('confirm-destination').checked = true;
    node('confirm-destination').events.change();
    assert.match(node('open-app').href, /^openfotoframe:\/\/connect#/);
    assert.equal(node('browser-form').action, '/accept');
    assert.equal(node('continue-browser').disabled, false);
    node('confirm-destination').checked = false;
    node('confirm-destination').events.change();
    assert.equal(node('open-app').href, undefined);
});

test('ambiguous, credential-bearing, unsupported and public cleartext destinations fail closed', () => {
    for (const fragment of [link() + '&token=another', link() + '&unexpected=yes',
        link({server:'https://user:password@example.com'}), link({server:'javascript:alert(1)'}),
        link({server:'https://example.com/upload'}), link({server:'https://example.com#secret'}),
        link({server:'http://public.example.com'}), link({mode:'hosted',server:'http://192.168.1.5'}),
        link({purpose:'setup'}), link({purpose:'pair'}), link({v:'2'})]) {
        const {node} = landing(fragment);
        assert.notEqual(node('link-error').textContent, '', fragment);
        assert.equal(node('connection').hidden, true, fragment);
        assert.equal(node('open-app').href, undefined, fragment);
    }
});

test('local HTTP is explicit and remains behind destination confirmation', () => {
    const {node} = landing(link({server:'http://192.168.1.5'}));
    assert.equal(node('link-error').textContent, '');
    assert.match(node('transport').textContent, /unencrypted HTTP/);
    assert.equal(node('continue-browser').disabled, true);
});

 test('cross-server invitation navigation uses a fragment and keeps form action same-origin', () => {
    const {node, navigation} = landing(link(), 'https://landing.example.com');
    assert.equal(node('invite-token').value, undefined);
    assert.equal(node('browser-form').action, '/accept');
    let prevented = false;
    node('browser-form').events.submit({preventDefault() { prevented = true; }});
    assert.equal(prevented, true);
    assert.equal(navigation.length, 1);
    node('confirm-destination').checked = true;
    node('browser-form').events.submit({preventDefault() { prevented = true; }});
    const target = new URL(navigation[1]);
    assert.equal(target.origin, 'https://frame.example.com');
    assert.equal(target.pathname, '/connect');
    assert.equal(target.search, '');
    assert.match(target.hash, /token=/);
 });
