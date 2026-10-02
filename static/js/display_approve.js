(function () {
    var view = document.getElementById('approve-view');
    var approveUrl = view.dataset.approveUrl;
    var csrf = view.dataset.csrf;
    var remaining = parseInt(view.dataset.remaining, 10);

    var countdownEl = document.getElementById('countdown');
    var timer = setInterval(function () {
        remaining--;
        if (remaining <= 0) { clearInterval(timer); countdownEl.textContent = '0'; return; }
        countdownEl.textContent = remaining;
    }, 1000);

    document.getElementById('approve-btn').addEventListener('click', function () {
        var btn = this;
        btn.disabled = true;
        btn.textContent = 'Approving…';
        fetch(approveUrl, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf },
            body: JSON.stringify({})
        })
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (data.success || data.already) {
                clearInterval(timer);
                document.getElementById('approve-view').style.display = 'none';
                document.getElementById('success-view').style.display = '';
            } else {
                btn.disabled = false;
                btn.textContent = 'Approve this display';
                alert(data.error || 'Something went wrong. Please try again.');
            }
        })
        .catch(function () {
            btn.disabled = false;
            btn.textContent = 'Approve this display';
            alert('Network error. Please try again.');
        });
    });
}());
