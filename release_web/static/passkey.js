(() => {
    'use strict';
    const decode = value => Uint8Array.from(atob(value.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - value.length % 4) % 4)), c => c.charCodeAt(0));
    const encode = value => btoa(String.fromCharCode(...new Uint8Array(value))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
    const button = document.getElementById('passkey-signin');
    button?.addEventListener('click', async () => {
        const error = document.getElementById('passkey-error');
        button.disabled = true;
        try {
            if (!window.PublicKeyCredential) throw new Error('Passkeys are unavailable in this browser.');
            const response = await fetch('/login/passkey/options', {method:'POST', headers:{'X-CSRFToken':document.querySelector('meta[name="csrf-token"]').content}});
            const options = await response.json();
            if (!response.ok) throw new Error(options.error?.message || 'Start sign-in again.');
            options.challenge = decode(options.challenge);
            const enrolling = button.dataset.enroll === 'true';
            if (enrolling) {
                options.user.id = decode(options.user.id);
                options.excludeCredentials = (options.excludeCredentials || []).map(item => ({...item, id:decode(item.id)}));
            } else options.allowCredentials = (options.allowCredentials || []).map(item => ({...item, id:decode(item.id)}));
            const credential = enrolling ? await navigator.credentials.create({publicKey:options}) : await navigator.credentials.get({publicKey:options});
            const result = {id:credential.id, rawId:encode(credential.rawId), type:credential.type,
                authenticatorAttachment:credential.authenticatorAttachment,
                clientExtensionResults:credential.getClientExtensionResults(), response:enrolling ? {
                    attestationObject:encode(credential.response.attestationObject),clientDataJSON:encode(credential.response.clientDataJSON),
                    transports:credential.response.getTransports?.() || []} : {
                    authenticatorData:encode(credential.response.authenticatorData),
                    clientDataJSON:encode(credential.response.clientDataJSON), signature:encode(credential.response.signature),
                    userHandle:credential.response.userHandle ? encode(credential.response.userHandle) : null}};
            document.getElementById('passkey-credential').value = JSON.stringify(result);
            document.getElementById('passkey-form').requestSubmit();
        } catch (failure) {
            error.textContent = failure.message || 'Passkey sign-in was cancelled.';
            button.disabled = false;
        }
    });
})();
