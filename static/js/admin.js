const csrfToken = document.querySelector('meta[name="csrf-token"]').getAttribute('content');
const _origFetch = window.fetch;
window.fetch = function(url, options = {}) {
    if (options.method && options.method !== 'GET') {
        options.headers = options.headers || {};
        if (options.headers instanceof Headers) {
            options.headers.set('X-CSRFToken', csrfToken);
        } else {
            options.headers['X-CSRFToken'] = csrfToken;
        }
    }
    return _origFetch.call(this, url, options);
};

// ===== Tab switching =====
function switchTab(tab) {
    document.getElementById('tab-users').style.display    = tab === 'users'  ? '' : 'none';
    document.getElementById('tab-backup').style.display   = tab === 'backup' ? '' : 'none';
    document.getElementById('tab-users-btn').classList.toggle('active',  tab === 'users');
    document.getElementById('tab-backup-btn').classList.toggle('active', tab === 'backup');
    history.replaceState(null, '', tab === 'backup' ? '#backup' : '#users');
    if (tab === 'backup') loadBackupStatus();
}

// Auto-select tab from URL hash
const initTab = location.hash === '#backup' ? 'backup' : 'users';
if (initTab === 'backup') switchTab('backup');

// ===== Users =====
let currentResetUser = null;

document.getElementById('add-user-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const username = document.getElementById('new-username').value.trim();
    const password = document.getElementById('new-password').value;
    const role     = document.getElementById('new-role').value;
    try {
        const res = await fetch('/api/admin/users', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, password, role })
        });
        const data = await res.json();
        if (res.ok) { showMessage('User created successfully', 'success'); setTimeout(() => location.reload(), 1000); }
        else showMessage(data.error || 'Failed to create user', 'error');
    } catch { showMessage('Error creating user', 'error'); }
});

async function deleteUser(username) {
    if (!confirm(`Delete user "${username}"?`)) return;
    try {
        const res = await fetch(`/api/admin/users/${username}`, { method: 'DELETE' });
        const data = await res.json();
        if (res.ok) { showMessage('User deleted', 'success'); document.querySelector(`tr[data-username="${username}"]`).remove(); }
        else showMessage(data.error || 'Failed to delete user', 'error');
    } catch { showMessage('Error deleting user', 'error'); }
}

function showResetPassword(username) {
    currentResetUser = username;
    document.getElementById('reset-username').textContent = username;
    document.getElementById('reset-password').value = '';
    document.getElementById('reset-modal').classList.add('active');
}

function closeModal() {
    document.getElementById('reset-modal').classList.remove('active');
    currentResetUser = null;
}

async function resetPassword() {
    const password = document.getElementById('reset-password').value;
    if (password.length < 12) { showMessage('Password must be at least 12 characters', 'error'); return; }
    try {
        const res = await fetch(`/api/admin/users/${currentResetUser}/password`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ password })
        });
        const data = await res.json();
        if (res.ok) { showMessage('Password reset successfully', 'success'); closeModal(); }
        else showMessage(data.error || 'Failed to reset password', 'error');
    } catch { showMessage('Error resetting password', 'error'); }
}

document.getElementById('reset-modal').addEventListener('click', (e) => {
    if (e.target.classList.contains('modal-overlay')) closeModal();
});

// ===== Backup =====
let pollTimer = null;

async function loadBackupStatus() {
    try {
        const res = await fetch('/api/backup/status');
        if (!res.ok) return;
        renderStatus(await res.json());
        loadHistory();
    } catch (err) { console.error('Failed to load backup status:', err); }
}

function renderStatus(status) {
    const dot      = document.getElementById('status-dot');
    const text     = document.getElementById('status-text');
    const meta     = document.getElementById('status-meta');
    const setup    = document.getElementById('setup-panel');
    const controls = document.getElementById('controls-panel');
    const settings = document.getElementById('settings-panel');
    const historyPanel = document.getElementById('history-panel');

    if (status.restore_in_progress) {
        dot.className = 'status-dot blue';
        text.textContent = 'Restore in progress...';
        meta.textContent = 'Copying files from Dropbox';
        if (!pollTimer) pollTimer = setInterval(loadBackupStatus, 5000);
    } else if (status.in_progress) {
        dot.className = 'status-dot blue';
        text.textContent = 'Backup in progress...';
        meta.textContent = '';
        if (!pollTimer) pollTimer = setInterval(loadBackupStatus, 5000);
    } else if (!status.configured) {
        dot.className = 'status-dot gray';
        text.textContent = 'Not configured';
        meta.textContent = 'Connect your Dropbox account to enable backups.';
        clearPollTimer();
    } else if (status.last_result === 'success' || status.last_result === 'no_changes') {
        dot.className = 'status-dot green';
        text.textContent = status.last_result === 'no_changes'
            ? 'Connected — Last run: no changes to back up'
            : 'Connected — Last backup successful';
        meta.textContent = formatMeta(status);
        clearPollTimer();
    } else if (status.last_result === 'error') {
        dot.className = 'status-dot red';
        text.textContent = 'Connected — Last backup failed';
        meta.textContent = status.last_error || '';
        clearPollTimer();
    } else {
        dot.className = 'status-dot yellow';
        text.textContent = 'Connected — No backups run yet';
        meta.textContent = formatMeta(status);
        clearPollTimer();
    }

    setup.style.display    = status.configured ? 'none'  : 'block';
    controls.style.display = status.configured ? 'block' : 'none';
    settings.style.display = status.configured ? 'block' : 'none';
    historyPanel.style.display  = status.configured ? 'block' : 'none';

    if (status.backup_time)        document.getElementById('backup-time').value   = status.backup_time;
    if (status.backup_path)        document.getElementById('backup-path').value   = status.backup_path;
    if (status.max_backup_history) document.getElementById('max-history').value   = status.max_backup_history;

    const busy = status.in_progress || status.restore_in_progress;
    const backupBtn  = document.getElementById('backup-now-btn');
    const restoreBtn = document.getElementById('restore-now-btn');
    backupBtn.disabled  = busy;
    backupBtn.textContent  = status.in_progress ? 'Backing up...' : 'Backup Now';
    restoreBtn.disabled = busy;
    restoreBtn.textContent = status.restore_in_progress ? 'Restoring...' : 'Restore from Dropbox';
}

function formatMeta(status) {
    const parts = [];
    if (status.last_backup)     parts.push('Last: '  + new Date(status.last_backup).toLocaleString());
    if (status.next_scheduled)  parts.push('Next: '  + new Date(status.next_scheduled).toLocaleString());
    return parts.join(' | ');
}

function clearPollTimer() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
}

async function loadHistory() {
    try {
        const res = await fetch('/api/backup/history');
        if (!res.ok) return;
        renderHistory((await res.json()).history || []);
    } catch (err) { console.error('Failed to load history:', err); }
}

function renderHistory(history) {
    const body = document.getElementById('history-body');
    if (!history.length) { body.innerHTML = '<div class="history-empty">No backups yet</div>'; return; }
    const rows = [...history].reverse().map(entry => {
        const label    = entry.result === 'success' ? 'Success' : entry.result === 'no_changes' ? 'No changes' : 'Failed';
        const duration = entry.duration_seconds ? entry.duration_seconds + 's' : '—';
        const err      = entry.error ? `<br><span style="color:#888;font-size:0.8em;">${escHtml(entry.error.substring(0,100))}</span>` : '';
        return `<tr class="${entry.result}"><td>${label}${err}</td><td>${new Date(entry.timestamp).toLocaleString()}</td><td>${duration}</td></tr>`;
    }).join('');
    body.innerHTML = `<table class="history-table"><thead><tr><th>Result</th><th>Time</th><th>Duration</th></tr></thead><tbody>${rows}</tbody></table>`;
}

function escHtml(text) {
    const d = document.createElement('div');
    d.textContent = text;
    return d.innerHTML;
}

async function configureBackup() {
    const token = document.getElementById('dropbox-token').value.trim();
    if (!token) { showMessage('Please paste a token', 'error'); return; }
    const btn = document.getElementById('connect-btn');
    btn.disabled = true; btn.textContent = 'Connecting...';
    try {
        const res = await fetch('/api/backup/configure', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ token })
        });
        const data = await res.json();
        if (res.ok) { showMessage('Dropbox connected successfully', 'success'); loadBackupStatus(); }
        else showMessage(data.error || 'Failed to connect', 'error');
    } catch (err) { showMessage('Connection failed: ' + err.message, 'error'); }
    finally { btn.disabled = false; btn.textContent = 'Connect Dropbox'; }
}

async function triggerBackup() {
    const btn = document.getElementById('backup-now-btn');
    btn.disabled = true; btn.textContent = 'Starting...';
    try {
        const res = await fetch('/api/backup/run', { method: 'POST' });
        const data = await res.json();
        if (res.ok) { showMessage('Backup started', 'success'); pollTimer = setInterval(loadBackupStatus, 5000); loadBackupStatus(); }
        else { showMessage(data.error || 'Failed to start backup', 'error'); btn.disabled = false; btn.textContent = 'Backup Now'; }
    } catch (err) { showMessage('Failed: ' + err.message, 'error'); btn.disabled = false; btn.textContent = 'Backup Now'; }
}

async function triggerRestore() {
    if (!confirm('Restore from Dropbox? This copies photos and settings from your backup. Local files not in the backup are kept. Login credentials will not be changed.')) return;
    const btn = document.getElementById('restore-now-btn');
    btn.disabled = true; btn.textContent = 'Starting...';
    try {
        const res = await fetch('/api/backup/restore', { method: 'POST' });
        const data = await res.json();
        if (res.ok) { showMessage('Restore started', 'success'); pollTimer = setInterval(loadBackupStatus, 5000); loadBackupStatus(); }
        else { showMessage(data.error || 'Failed to start restore', 'error'); btn.disabled = false; btn.textContent = 'Restore from Dropbox'; }
    } catch (err) { showMessage('Failed: ' + err.message, 'error'); btn.disabled = false; btn.textContent = 'Restore from Dropbox'; }
}

async function disconnectBackup() {
    if (!confirm('Disconnect Dropbox? Existing backups in Dropbox will not be deleted.')) return;
    try {
        const res = await fetch('/api/backup/configure', { method: 'DELETE' });
        if (res.ok) { showMessage('Dropbox disconnected', 'success'); loadBackupStatus(); }
    } catch { showMessage('Failed to disconnect', 'error'); }
}

async function saveBackupSettings() {
    try {
        const res = await fetch('/api/backup/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                backup_time:        document.getElementById('backup-time').value,
                backup_path:        document.getElementById('backup-path').value.trim(),
                max_backup_history: parseInt(document.getElementById('max-history').value, 10)
            })
        });
        if (res.ok) { showMessage('Backup settings saved', 'success'); loadBackupStatus(); }
        else { const d = await res.json(); showMessage(d.error || 'Failed to save settings', 'error'); }
    } catch { showMessage('Failed to save settings', 'error'); }
}

// ===== Shared message =====
function showMessage(text, type) {
    const el = document.getElementById('message');
    el.textContent = text;
    el.className = 'message ' + type;
    el.style.display = 'block';
    setTimeout(() => { el.style.display = 'none'; }, 4000);
}
