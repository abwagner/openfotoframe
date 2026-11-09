/* Fragment details never leave the device until the destination is confirmed. */
(() => {
    'use strict';
    const raw = location.hash.slice(1);
    history.replaceState(null, '', location.pathname);
    const error = document.getElementById('link-error');
    const localHost = value => {
        const host = value.toLowerCase();
        if (host === 'localhost' || host.endsWith('.local') || host === '[::1]' ||
            /^\[(?:fc|fd)[0-9a-f]{2}:/.test(host) || /^\[fe[89ab][0-9a-f]:/.test(host)) return true;
        const parts = host.split('.');
        if (parts.length !== 4 || !parts.every(part => /^\d{1,3}$/.test(part) && +part <= 255)) return false;
        const [a, b] = parts.map(Number);
        return a === 10 || a === 127 || (a === 192 && b === 168) ||
            (a === 172 && b >= 16 && b <= 31) || (a === 100 && b >= 64 && b <= 127);
    };
    try {
        if (!raw || raw.length > 4096) throw new Error('This connection link is invalid.');
        const params = new URLSearchParams(raw);
        const allowed = new Set(['v', 'purpose', 'mode', 'server', 'id', 'token']);
        const seen = new Set();
        for (const [key] of params) {
            if (!allowed.has(key) || seen.has(key)) throw new Error('This connection link is invalid.');
            seen.add(key);
        }
        const purpose = params.get('purpose'), mode = params.get('mode');
        if (params.get('v') !== '1' || !['setup', 'invite', 'pair'].includes(purpose) ||
            !['hosted', 'self_hosted'].includes(mode)) throw new Error('This connection link is not supported.');
        const server = new URL(params.get('server'));
        if (!['http:', 'https:'].includes(server.protocol) || server.username || server.password ||
            server.pathname !== '/' || server.search || server.hash ||
            (server.protocol === 'http:' && (mode === 'hosted' || !localHost(server.hostname))))
            throw new Error('This link does not contain a valid server root address.');
        if (purpose === 'setup' && (params.has('id') || params.has('token'))) throw new Error('A server setup link cannot include credentials.');
        if (purpose !== 'setup' && (!params.get('token') || params.get('token').length > 200)) throw new Error('This invitation or login code is incomplete.');
        if (purpose === 'pair' && !/^[A-Za-z0-9_-]{1,64}$/.test(params.get('id') || '')) throw new Error('This login code is incomplete.');
        if (purpose === 'invite' && params.has('id')) throw new Error('This invitation is invalid.');
        document.getElementById('connection').hidden = false;
        document.getElementById('purpose').textContent = ({setup:'Connect to a server',invite:'Accept a collection and frame invitation',pair:'Request quick login from another device'})[purpose];
        document.getElementById('mode').textContent = mode === 'hosted' ? 'OpenFotoFrame hosted service' : 'Self-hosted server';
        document.getElementById('destination').textContent = server.origin;
        if (server.protocol === 'http:') {
            document.getElementById('transport').textContent = 'This local server uses unencrypted HTTP. Continue only on a network you trust; HTTPS is recommended.';
        }
        const open = document.getElementById('open-app');
        const confirmation = document.getElementById('confirm-destination');
        const form = document.getElementById('browser-form');
        if (purpose === 'invite') {
            form.hidden = false;
            // Cross-origin navigation keeps the secret in a fragment. The
            // destination confirms again before its own same-origin POST.
            const sameOrigin = server.origin === location.origin;
            form.action = '/accept';
            if (sameOrigin) document.getElementById('invite-token').value = params.get('token');
            form.addEventListener('submit', event => {
                if (!confirmation.checked) { event.preventDefault(); return; }
                if (!sameOrigin) {
                    event.preventDefault();
                    location.assign(server.origin + '/connect#' + params.toString());
                }
            });
        }
        confirmation.addEventListener('change', () => {
            open.setAttribute('aria-disabled', String(!confirmation.checked));
            if (confirmation.checked) open.href = 'openfotoframe://connect#' + params.toString();
            else open.removeAttribute('href');
            document.getElementById('continue-browser').disabled = !confirmation.checked;
        });
    } catch (failure) {
        error.textContent = failure.message || 'This connection link is invalid.';
    }
})();
