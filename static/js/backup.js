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

        let pollTimer = null;

        // Load on page init
        loadBackupStatus();

        async function loadBackupStatus() {
            try {
                const response = await fetch('/api/backup/status');
                if (!response.ok) return;
                const data = await response.json();
                renderStatus(data);
                loadHistory();
            } catch (err) {
                console.error('Failed to load backup status:', err);
            }
        }

        function renderStatus(status) {
            const dot = document.getElementById('status-dot');
            const text = document.getElementById('status-text');
            const meta = document.getElementById('status-meta');
            const setupPanel = document.getElementById('setup-panel');
            const controlsPanel = document.getElementById('controls-panel');
            const settingsPanel = document.getElementById('settings-panel');
            const historyPanel = document.getElementById('history-panel');

            if (status.restore_in_progress) {
                dot.className = 'status-dot blue';
                text.textContent = 'Restore in progress...';
                meta.textContent = 'Copying files from Dropbox';
                if (!pollTimer) {
                    pollTimer = setInterval(loadBackupStatus, 5000);
                }
            } else if (status.in_progress) {
                dot.className = 'status-dot blue';
                text.textContent = 'Backup in progress...';
                meta.textContent = '';
                if (!pollTimer) {
                    pollTimer = setInterval(loadBackupStatus, 5000);
                }
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

            // Toggle panels
            setupPanel.style.display = status.configured ? 'none' : 'block';
            controlsPanel.style.display = status.configured ? 'block' : 'none';
            settingsPanel.style.display = status.configured ? 'block' : 'none';
            historyPanel.style.display = status.configured ? 'block' : 'none';

            // Update settings fields
            if (status.backup_time) {
                document.getElementById('backup-time').value = status.backup_time;
            }
            if (status.backup_path) {
                document.getElementById('backup-path').value = status.backup_path;
            }
            if (status.max_backup_history) {
                document.getElementById('max-history').value = status.max_backup_history;
            }

            // Update backup button state
            const btn = document.getElementById('backup-now-btn');
            const restoreBtn = document.getElementById('restore-now-btn');
            const busy = status.in_progress || status.restore_in_progress;
            if (status.in_progress) {
                btn.disabled = true;
                btn.textContent = 'Backing up...';
            } else {
                btn.disabled = busy;
                btn.textContent = 'Backup Now';
            }
            if (status.restore_in_progress) {
                restoreBtn.disabled = true;
                restoreBtn.textContent = 'Restoring...';
            } else {
                restoreBtn.disabled = busy;
                restoreBtn.textContent = 'Restore from Dropbox';
            }
        }

        function formatMeta(status) {
            let parts = [];
            if (status.last_backup) {
                const d = new Date(status.last_backup);
                parts.push('Last: ' + d.toLocaleString());
            }
            if (status.next_scheduled) {
                const d = new Date(status.next_scheduled);
                parts.push('Next: ' + d.toLocaleString());
            }
            return parts.join(' | ');
        }

        function clearPollTimer() {
            if (pollTimer) {
                clearInterval(pollTimer);
                pollTimer = null;
            }
        }

        async function loadHistory() {
            try {
                const response = await fetch('/api/backup/history');
                if (!response.ok) return;
                const data = await response.json();
                renderHistory(data.history || []);
            } catch (err) {
                console.error('Failed to load history:', err);
            }
        }

        function renderHistory(history) {
            const body = document.getElementById('history-body');
            if (history.length === 0) {
                body.innerHTML = '<div class="history-empty">No backups yet</div>';
                return;
            }

            // Show most recent first
            const rows = [...history].reverse().map(entry => {
                const d = new Date(entry.timestamp);
                const time = d.toLocaleString();
                const resultLabel = entry.result === 'success' ? 'Success'
                    : entry.result === 'no_changes' ? 'No changes'
                    : 'Failed';
                const duration = entry.duration_seconds ? entry.duration_seconds + 's' : '—';
                const error = entry.error ? `<br><span style="color:#888;font-size:0.8em;">${escapeHtml(entry.error.substring(0, 100))}</span>` : '';
                return `<tr class="${entry.result}">
                    <td>${resultLabel}${error}</td>
                    <td>${time}</td>
                    <td>${duration}</td>
                </tr>`;
            }).join('');

            body.innerHTML = `<table class="history-table">
                <thead><tr><th>Result</th><th>Time</th><th>Duration</th></tr></thead>
                <tbody>${rows}</tbody>
            </table>`;
        }

        function escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }

        async function configureBackup() {
            const token = document.getElementById('dropbox-token').value.trim();
            if (!token) {
                showMessage('Please paste a token', 'error');
                return;
            }

            const btn = document.getElementById('connect-btn');
            btn.disabled = true;
            btn.textContent = 'Connecting...';

            try {
                const response = await fetch('/api/backup/configure', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ token })
                });
                const data = await response.json();
                if (response.ok) {
                    showMessage('Dropbox connected successfully', 'success');
                    loadBackupStatus();
                } else {
                    showMessage(data.error || 'Failed to connect', 'error');
                }
            } catch (err) {
                showMessage('Connection failed: ' + err.message, 'error');
            } finally {
                btn.disabled = false;
                btn.textContent = 'Connect Dropbox';
            }
        }

        async function triggerBackup() {
            const btn = document.getElementById('backup-now-btn');
            btn.disabled = true;
            btn.textContent = 'Starting...';

            try {
                const response = await fetch('/api/backup/run', { method: 'POST' });
                const data = await response.json();
                if (response.ok) {
                    showMessage('Backup started', 'success');
                    // Start polling for completion
                    pollTimer = setInterval(loadBackupStatus, 5000);
                    loadBackupStatus();
                } else {
                    showMessage(data.error || 'Failed to start backup', 'error');
                    btn.disabled = false;
                    btn.textContent = 'Backup Now';
                }
            } catch (err) {
                showMessage('Failed: ' + err.message, 'error');
                btn.disabled = false;
                btn.textContent = 'Backup Now';
            }
        }

        async function triggerRestore() {
            if (!confirm('Restore from Dropbox? This will copy photos and settings from your backup. Local files not in the backup will be kept. Your login credentials will not be changed.')) return;

            const btn = document.getElementById('restore-now-btn');
            btn.disabled = true;
            btn.textContent = 'Starting...';

            try {
                const response = await fetch('/api/backup/restore', { method: 'POST' });
                const data = await response.json();
                if (response.ok) {
                    showMessage('Restore started', 'success');
                    pollTimer = setInterval(loadBackupStatus, 5000);
                    loadBackupStatus();
                } else {
                    showMessage(data.error || 'Failed to start restore', 'error');
                    btn.disabled = false;
                    btn.textContent = 'Restore from Dropbox';
                }
            } catch (err) {
                showMessage('Failed: ' + err.message, 'error');
                btn.disabled = false;
                btn.textContent = 'Restore from Dropbox';
            }
        }

        async function disconnectBackup() {
            if (!confirm('Disconnect Dropbox? Existing backups in Dropbox will not be deleted.')) return;

            try {
                const response = await fetch('/api/backup/configure', { method: 'DELETE' });
                if (response.ok) {
                    showMessage('Dropbox disconnected', 'success');
                    loadBackupStatus();
                }
            } catch (err) {
                showMessage('Failed to disconnect', 'error');
            }
        }

        async function saveBackupSettings() {
            const backupTime = document.getElementById('backup-time').value;
            const backupPath = document.getElementById('backup-path').value.trim();
            const maxHistory = parseInt(document.getElementById('max-history').value, 10);

            try {
                const response = await fetch('/api/backup/settings', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        backup_time: backupTime,
                        backup_path: backupPath,
                        max_backup_history: maxHistory
                    })
                });
                if (response.ok) {
                    showMessage('Backup settings saved', 'success');
                    loadBackupStatus();
                } else {
                    const data = await response.json();
                    showMessage(data.error || 'Failed to save settings', 'error');
                }
            } catch (err) {
                showMessage('Failed to save settings', 'error');
            }
        }

        function showMessage(message, type) {
            const el = document.getElementById('status-msg');
            el.textContent = message;
            el.className = 'status-msg ' + type;
            setTimeout(() => {
                el.className = 'status-msg';
            }, 5000);
        }
