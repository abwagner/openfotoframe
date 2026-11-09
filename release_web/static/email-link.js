// Capture a fragment locally, strip it immediately, and require an explicit form
// submission. Never send it on GET, store it, or place it in a redirect/query.
(() => {
    const token = new URLSearchParams(window.location.hash.slice(1)).get('token');
    window.history.replaceState(null, '', window.location.pathname);
    document.addEventListener('DOMContentLoaded', () => {
        const input = document.getElementById('email-link-token');
        if (input && token && token.length <= 200) input.value = token;
        const button = document.getElementById('complete-link');
        if (button && input) button.disabled = !input.value;
    });
})();
