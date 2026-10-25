(function () {
    var qrContainer = document.getElementById('qr-container');
    var statusMsg = document.getElementById('status-message');
    var countdownEl = document.getElementById('countdown');

    var pollTimer = null;
    var countdownTimer = null;
    var currentCode = null;
    var expiresAt = null;

    function fmtCountdown(secs) {
        var m = Math.floor(secs / 60);
        var s = secs % 60;
        return m + ':' + (s < 10 ? '0' : '') + s;
    }

    function startCountdown() {
        if (countdownTimer) clearInterval(countdownTimer);
        countdownTimer = setInterval(function () {
            var remaining = Math.max(0, Math.floor((expiresAt - Date.now()) / 1000));
            countdownEl.textContent = fmtCountdown(remaining);
            if (remaining === 0) {
                clearInterval(countdownTimer);
                clearInterval(pollTimer);
                statusMsg.textContent = 'Code expired. Generating a new one…';
                setTimeout(initDeviceCode, 1500);
            }
        }, 1000);
    }

    function startPolling() {
        if (pollTimer) clearInterval(pollTimer);
        pollTimer = setInterval(function () {
            fetch('/api/display/device-code/status/' + currentCode)
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    if (data.status === 'approved') {
                        clearInterval(pollTimer);
                        clearInterval(countdownTimer);
                        statusMsg.textContent = 'Approved! Loading display…';
                        window.location.href = data.redirect;
                    } else if (data.status === 'expired' || data.status === 'invalid') {
                        clearInterval(pollTimer);
                        clearInterval(countdownTimer);
                        setTimeout(initDeviceCode, 500);
                    }
                })
                .catch(function () { /* network hiccup, keep polling */ });
        }, 2000);
    }

    function initDeviceCode() {
        fetch('/api/display/device-code')
            .then(function (r) {
                if (!r.ok) throw new Error('server error');
                return r.json();
            })
            .then(function (data) {
                currentCode = data.code;
                expiresAt = Date.now() + data.expires_in * 1000;
                var img = document.createElement('img');
                img.src = data.qr_data_url;
                img.alt = 'Scan to approve display';
                img.width = 200;
                img.height = 200;
                qrContainer.innerHTML = '';
                qrContainer.appendChild(img);
                statusMsg.textContent = 'Scan the QR code with your phone to approve this display.';
                startCountdown();
                startPolling();
            })
            .catch(function () {
                statusMsg.textContent = 'Could not reach server. Retrying…';
                setTimeout(initDeviceCode, 5000);
            });
    }

    initDeviceCode();
}());
