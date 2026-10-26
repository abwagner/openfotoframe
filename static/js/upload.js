// ===== CSRF Token =====
        const csrfToken = document.querySelector('meta[name="csrf-token"]').getAttribute('content');

        // Wrap fetch to automatically include CSRF token
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

        // ===== DOM Elements =====
        const uploadZone = document.getElementById('upload-zone');
        const fileInput = document.getElementById('file-input');
        const galleryGrid = document.getElementById('gallery-grid');
        const emptyState = document.getElementById('empty-state');
        const statusEl = document.getElementById('status');
        const previewSection = document.getElementById('preview-section');
        const matPreview = document.getElementById('mat-preview');
        const previewContent = document.getElementById('preview-content');
        const previewControls = document.getElementById('preview-controls');
        const previewControlsBody = document.getElementById('preview-controls-body');
        const previewControlsTitle = document.getElementById('preview-controls-title');

        // Shared with the native app; theme colors never change these mat values.
        const NEUTRAL_PRESETS = [];
        const ACCENT_PRESETS = [];

        async function loadMatPalette() {
            try {
                const response = await fetch('/static/mat-colors.json');
                if (!response.ok) throw new Error('Palette unavailable');
                const palette = await response.json();
                NEUTRAL_PRESETS.push(...palette.neutrals);
                ACCENT_PRESETS.push(...palette.accents);
                buildSidebarPresets();
                bindSidebarPresets();
                updateColorPresets();
                if (previewFilename) renderSingleControls(previewFilename);
                else if (previewGroupId) renderGroupControls(previewGroupId);
            } catch (error) {
                showStatus('Color presets could not load. Reload to try again.', 'error');
            }
        }

        // Settings elements
        const matColorInput = document.getElementById('mat-color');
        const matFinishSelect = document.getElementById('mat-finish');
        const borderEffectSelect = document.getElementById('border-effect');
        const bevelWidthInput = document.getElementById('bevel-width');
        const bevelValueLabel = document.getElementById('bevel-value');
        const effectSizeLabel = document.getElementById('effect-size-label');
        const slideshowIntervalInput = document.getElementById('slideshow-interval');
        const transitionDurationInput = document.getElementById('transition-duration');
        const fitModeSelect = document.getElementById('fit-mode');
        const targetAspectRatioSelect = document.getElementById('target-aspect-ratio');
        const shuffleCheckbox = document.getElementById('shuffle');
        // Populate sidebar color presets dynamically
        function buildSidebarPresets() {
            const neutralContainer = document.getElementById('default-color-presets');
            const accentContainer = document.getElementById('accent-color-presets');
            neutralContainer.innerHTML = NEUTRAL_PRESETS.map(p =>
                `<div class="color-preset" style="background:${p.color};${p.color === '#ffffff' ? 'border:1px solid rgba(255,255,255,0.2);' : ''}" data-color="${p.color}" title="${p.title}"></div>`
            ).join('');
            accentContainer.innerHTML = ACCENT_PRESETS.map(p =>
                `<div class="color-preset" style="background:${p.color};${p.color === '#ffffff' ? 'border:1px solid rgba(255,255,255,0.2);' : ''}" data-color="${p.color}" title="${p.title}"></div>`
            ).join('');
        }

        function toggleMoreColors() {
            const accentEl = document.getElementById('accent-color-presets');
            const btn = document.getElementById('toggle-more-colors');
            const showing = accentEl.style.display !== 'none';
            accentEl.style.display = showing ? 'none' : 'flex';
            btn.textContent = showing ? 'More Colors' : 'Fewer Colors';
            // Rebind click handlers for accent presets
            if (!showing) bindSidebarPresets();
        }

        function bindSidebarPresets() {
            document.querySelectorAll('.settings-column .color-preset').forEach(preset => {
                preset.onclick = () => {
                    matColorInput.value = preset.dataset.color;
                    updateColorPresets();
                    updatePreviewTexture();
                };
            });
        }

        // ===== Settings Tabs =====
        function switchSettingsTab(tab) {
            document.querySelectorAll('.settings-tab').forEach(t => t.classList.toggle('active', t.dataset.tab === tab));
            document.querySelectorAll('.settings-tab-content').forEach(c => c.style.display = 'none');
            document.getElementById('tab-' + tab).style.display = 'flex';
        }

        let galleryScrollView = false;
        function toggleGalleryView() {
            galleryScrollView = !galleryScrollView;
            const grid = document.getElementById('gallery-grid');
            const btn = document.getElementById('view-toggle');
            grid.classList.toggle('scroll-view', galleryScrollView);
            btn.textContent = galleryScrollView ? '▦' : '☰';
            btn.title = galleryScrollView ? 'Grid view' : 'Strip view';
        }

        // ===== State =====
        let images = [];
        let groups = {};  // group_id -> {images: [...], mat_color, created_at}
        let selectedImages = new Set();
        let currentFilter = 'all';
        let previewFilename = null;
        let previewGroupId = null;

        // ===== Initialize =====
        updatePreviewAspectRatio();
        loadMatPalette();
        loadGallery();

        // ===== Upload Handling =====
        uploadZone.addEventListener('click', () => fileInput.click());

        uploadZone.addEventListener('dragover', (e) => {
            e.preventDefault();
            uploadZone.classList.add('dragover');
        });

        uploadZone.addEventListener('dragleave', () => {
            uploadZone.classList.remove('dragover');
        });

        uploadZone.addEventListener('drop', (e) => {
            e.preventDefault();
            uploadZone.classList.remove('dragover');
            handleFiles(e.dataTransfer.files);
        });

        fileInput.addEventListener('change', (e) => {
            handleFiles(e.target.files);
        });

        // Pending files for the two-phase upload flow
        let pendingUploadFiles = [];

        async function handleFiles(files) {
            pendingUploadFiles = Array.from(files);

            // Phase 1: Check for duplicates and dimensions
            showStatus('Checking for duplicates...', 'success');
            const formData = new FormData();
            for (const file of pendingUploadFiles) {
                formData.append('files', file);
            }

            let checkResults = null;
            try {
                const resp = await fetch('/api/check-duplicates', {
                    method: 'POST',
                    body: formData
                });
                checkResults = await resp.json();
            } catch (err) {
                // If check fails, fall through to direct upload
            }

            // Determine if any warnings exist
            const MIN_WIDTH = 1280, MIN_HEIGHT = 720;
            let hasWarnings = false;

            if (checkResults && checkResults.results) {
                for (const fname of Object.keys(checkResults.results)) {
                    const r = checkResults.results[fname];
                    if (r.matches && r.matches.length > 0) hasWarnings = true;
                    if (r.width < MIN_WIDTH || r.height < MIN_HEIGHT) hasWarnings = true;
                }
            }

            if (!hasWarnings || !checkResults) {
                // No warnings — upload directly
                await doUpload(pendingUploadFiles);
                return;
            }

            // Phase 2: Show warning modal
            showUploadModal(checkResults.results);
        }

        function showUploadModal(results) {
            const MIN_WIDTH = 1280, MIN_HEIGHT = 720;
            const body = document.getElementById('upload-modal-body');
            let html = '';

            for (const file of pendingUploadFiles) {
                const r = results[file.name] || {};
                const matches = r.matches || [];
                const isSmall = (r.width && r.height) && (r.width < MIN_WIDTH || r.height < MIN_HEIGHT);
                const hasDupes = matches.length > 0;
                const hasIssue = isSmall || hasDupes;

                html += `<div class="upload-modal-file">
                    <input type="checkbox" data-filename="${escAttr(file.name)}" ${hasIssue ? '' : 'checked'}>
                    <div class="upload-modal-file-info">
                        <div class="upload-modal-file-name">${escAttr(file.name)}</div>`;

                if (isSmall) {
                    html += `<span class="upload-modal-badge badge-small">Low res: ${r.width}&times;${r.height}</span>`;
                }
                if (hasDupes) {
                    for (const m of matches) {
                        const similarity = Math.max(0, 100 - Math.round(m.distance * 100 / 64));
                        html += `<div class="upload-modal-match">
                            <img src="/thumbnails/${escAttr(m.filename)}" alt="match">
                            <span>Similar to <strong>${cleanFilename(m.filename)}</strong> (${similarity}% match)</span>
                        </div>`;
                    }
                    html += `<span class="upload-modal-badge badge-duplicate">Possible duplicate</span>`;
                }
                if (!hasIssue) {
                    html += `<span class="upload-modal-badge badge-ok">OK</span>`;
                }

                if (r.width && r.height) {
                    html += `<div style="font-size:0.75rem;color:#888;margin-top:4px;">${r.width}&times;${r.height}px</div>`;
                }

                html += `</div></div>`;
            }

            body.innerHTML = html;
            document.getElementById('upload-modal-overlay').classList.add('active');
            statusEl.className = 'status';
        }

        function cancelUploadModal() {
            document.getElementById('upload-modal-overlay').classList.remove('active');
            pendingUploadFiles = [];
            fileInput.value = '';
        }

        async function proceedUpload() {
            const checkboxes = document.querySelectorAll('#upload-modal-body input[type="checkbox"]');
            const selectedNames = new Set();
            checkboxes.forEach(cb => {
                if (cb.checked) selectedNames.add(cb.dataset.filename);
            });

            const filesToUpload = pendingUploadFiles.filter(f => selectedNames.has(f.name));
            document.getElementById('upload-modal-overlay').classList.remove('active');

            if (filesToUpload.length === 0) {
                showStatus('No files selected', 'error');
                pendingUploadFiles = [];
                fileInput.value = '';
                return;
            }

            await doUpload(filesToUpload);
        }

        async function doUpload(files) {
            const formData = new FormData();
            for (const file of files) {
                formData.append('files', file);
            }

            showStatus('Uploading...', 'success');

            try {
                const response = await fetch('/api/upload', {
                    method: 'POST',
                    body: formData
                });
                const data = await response.json();

                if (data.uploaded.length > 0) {
                    showStatus(`Uploaded ${data.uploaded.length} image(s)`, 'success');
                    loadGallery();
                }
                if (data.errors.length > 0) {
                    showStatus(data.errors.join(', '), 'error');
                }
            } catch (err) {
                showStatus('Upload failed: ' + err.message, 'error');
            }

            pendingUploadFiles = [];
            fileInput.value = '';
        }

        // ===== Gallery Loading =====
        async function loadGallery() {
            try {
                const response = await fetch('/api/gallery');
                const data = await response.json();
                images = data.images;
                groups = data.groups || {};
                renderGallery();
                updateStats();
            } catch (err) {
                showStatus('Failed to load gallery', 'error');
            }
        }

        function renderGallery() {
            const grid = galleryGrid;

            // Build set of grouped filenames
            const groupedFilenames = new Set();
            for (const group of Object.values(groups)) {
                (group.images || []).forEach(f => groupedFilenames.add(f));
            }

            let filtered = images;
            if (currentFilter === 'enabled') {
                filtered = images.filter(img => img.enabled);
            } else if (currentFilter === 'disabled') {
                filtered = images.filter(img => !img.enabled);
            }

            // Separate ungrouped images from grouped, sorted by upload time (newest first)
            const ungrouped = filtered.filter(img => !groupedFilenames.has(img.filename));
            ungrouped.sort((a, b) => (b.uploaded_at || '').localeCompare(a.uploaded_at || ''));

            if (images.length === 0 && Object.keys(groups).length === 0) {
                grid.style.display = 'none';
                emptyState.style.display = 'block';
                return;
            }

            grid.style.display = 'grid';
            emptyState.style.display = 'none';

            // Render group cards first, then ungrouped images
            let html = '';

            // Group cards (only show if filter is 'all' or 'enabled' since groups are visible)
            if (currentFilter !== 'disabled') {
                for (const [groupId, group] of Object.entries(groups)) {
                    const groupImages = (group.images || []).map(f => images.find(i => i.filename === f)).filter(Boolean);
                    if (groupImages.length < 2) continue;

                    const count = groupImages.length;
                    const mosaicClass = count === 2 ? 'cols-2' : count === 3 ? 'cols-3' : 'cols-2x2';
                    const displayImages = groupImages.slice(0, 4);

                    html += `
                        <div class="group-card" data-group-id="${groupId}">
                            <div class="group-mosaic ${mosaicClass}" data-onclick="openGroupPreview('${groupId}')">
                                <span class="group-badge">${count} images</span>
                                ${displayImages.map(img => `<img src="/thumbnails/${escAttr(img.filename)}" alt="${escAttr(img.filename)}" loading="lazy">`).join('')}
                            </div>
                            <div class="card-info">
                                <div class="card-filename">Group (${count} images)</div>
                                <div class="mat-color-row">
                                    <label>Mat:</label>
                                    <input type="color"
                                           class="mat-color-picker"
                                           value="${group.mat_color || matColorInput.value}"
                                           data-onchange="updateGroupMatColor('${groupId}', this.value)">
                                </div>
                                <div class="card-actions">
                                    <button class="btn btn-edit" data-onclick="openGroupPreview('${groupId}')">Edit</button>
                                    <button class="btn btn-ungroup" data-onclick="ungroupGroup('${groupId}')">Ungroup</button>
                                    <button class="btn btn-danger" data-onclick="deleteGroup('${groupId}')">🗑</button>
                                </div>
                            </div>
                        </div>
                    `;
                }
            }

            // Ungrouped image cards
            html += ungrouped.map(img => `
                <div class="image-card ${img.enabled ? '' : 'disabled'} ${selectedImages.has(img.filename) ? 'selected' : ''}"
                     data-filename="${escAttr(img.filename)}">
                    <div class="card-thumb" data-onclick="handleCardClick('${escAttr(img.filename)}')">
                        <span class="status-badge ${img.enabled ? 'status-enabled' : 'status-disabled'}">
                            ${img.enabled ? 'Visible' : 'Hidden'}
                        </span>
                        <img src="/thumbnails/${escAttr(img.filename)}" alt="${escAttr(img.filename)}" loading="lazy">
                    </div>
                    <div class="card-info">
                        <div class="card-filename" title="${escAttr(img.filename)}">${cleanFilename(escAttr(img.filename))}</div>
                        <div class="card-meta">
                            <span>${formatSize(img.size)}</span>
                            <span>${img.uploaded_by || 'Unknown'}</span>
                        </div>
                        <div class="mat-color-row">
                            <label>Mat:</label>
                            <input type="color"
                                   class="mat-color-picker"
                                   value="${img.mat_color || matColorInput.value}"
                                   data-filename="${escAttr(img.filename)}"
                                   data-onchange="updateMatColor('${escAttr(img.filename)}', this.value)">
                        </div>
                        <div class="card-actions">
                            <button class="btn ${img.enabled ? 'btn-warning' : 'btn-success'}"
                                    data-onclick="toggleImage('${escAttr(img.filename)}', ${!img.enabled})">
                                ${img.enabled ? '⊘ Hide' : '✓ Show'}
                            </button>
                            <button class="btn btn-danger" data-onclick="deleteImage('${escAttr(img.filename)}')">🗑</button>
                        </div>
                    </div>
                </div>
            `).join('');

            grid.innerHTML = html;
            updateSelectionUI();
        }

        // ===== Preview =====
        function openPreview(filename) {
            const img = images.find(i => i.filename === filename);
            if (!img) return;

            previewFilename = filename;
            previewGroupId = null;

            previewControls.classList.add('active');
            previewSection.classList.add('has-controls');
            previewSection.classList.add('active');
            document.getElementById('preview-ungroup-btn').style.display = 'none';
            document.getElementById('start-group').disabled = false;

            renderSinglePreviewImage(filename);
            renderSingleControls(filename);
        }

        function renderSinglePreviewImage(filename) {
            const img = images.find(i => i.filename === filename);
            if (!img) return;

            const matColor = img.mat_color || matColorInput.value;
            const rawEffectSize = img.bevel_width ?? parseInt(bevelWidthInput.value);
            const effectSize = previewScaledEffect(rawEffectSize);
            const borderEffect = img.border_effect || borderEffectSelect.value;
            const finish = img.mat_finish || matFinishSelect.value;
            const scale = img.scale || 1.0;

            matPreview.style.backgroundColor = matColor;
            matPreview.classList.remove('mat-eggshell', 'mat-linen', 'mat-suede', 'mat-silk');
            if (finish !== 'flat') matPreview.classList.add('mat-' + finish);

            // Compute pixel dimensions — photo+effect must fit in frame
            const containerW = previewContent.clientWidth;
            const containerH = previewContent.clientHeight;
            const effectSpace = borderEffect === 'shadow' ? Math.round(effectSize * 2) : effectSize;
            const effect2 = (effectSpace > 0 ? effectSpace : 0) * 2;

            // Photo ≤ 95% of container; photo+effect ≤ container
            const maxPhotoW = Math.min(containerW * 0.95, containerW - effect2);
            const maxPhotoH = Math.min(containerH * 0.95, containerH - effect2);

            // At scale=1 use 90%-effect as base, scale adjusts, hard-capped at max
            const baseW = Math.min(containerW * 0.90 - effect2, maxPhotoW);
            const baseH = Math.min(containerH * 0.90 - effect2, maxPhotoH);
            const scaledW = Math.max(1, Math.min(baseW * scale, maxPhotoW));
            const scaledH = Math.max(1, Math.min(baseH * scale, maxPhotoH));

            const rawRatio = (img.width && img.height) ? img.width / img.height : 4 / 3;
            const crop = img.crop;
            const imgRatio = crop ? (rawRatio * crop.w / crop.h) : rawRatio;
            let displayW, displayH;
            if (imgRatio > scaledW / scaledH) {
                displayW = scaledW;
                displayH = scaledW / imgRatio;
            } else {
                displayH = scaledH;
                displayW = scaledH * imgRatio;
            }

            let imgHtml;
            if (crop) {
                const dW = Math.round(displayW);
                const dH = Math.round(displayH);
                const fullW = Math.round(dW / crop.w);
                const fullH = Math.round(dH / crop.h);
                const offsetX = Math.round(-crop.x * fullW);
                const offsetY = Math.round(-crop.y * fullH);
                imgHtml = `<div style="width:${dW}px;height:${dH}px;overflow:hidden;line-height:0;">` +
                          `<img src="/uploads/${filename}" alt="${filename}" style="width:${fullW}px;height:${fullH}px;margin-left:${offsetX}px;margin-top:${offsetY}px;display:block;max-width:none;max-height:none;">` +
                          `</div>`;
            } else {
                imgHtml = `<img src="/uploads/${filename}" alt="${filename}" style="width:${Math.round(displayW)}px;height:${Math.round(displayH)}px;object-fit:contain;">`;
            }
            previewContent.innerHTML = makeEffectHtml(imgHtml, effectSize, matColor, borderEffect);
        }

        function renderSingleControls(filename) {
            const img = images.find(i => i.filename === filename);
            if (!img) return;

            const scale = img.scale || 1.0;
            previewControlsTitle.textContent = cleanFilename(filename);
            document.querySelector('.preview-controls-actions').style.display = 'none';
            const currentFinish = img.mat_finish || '';
            const currentBevel = img.bevel_width ?? '';
            const currentBorderEffect = img.border_effect || '';
            const effectLabel = (currentBorderEffect || borderEffectSelect.value) === 'shadow' ? 'Shadow' : 'Bevel';

            const currentMatColor = img.mat_color || matColorInput.value;

            previewControlsBody.innerHTML = `
                <div class="group-edit-item">
                    <img class="group-edit-thumb" src="/uploads/${filename}" alt="${filename}">
                    <div class="group-edit-controls">
                        <input type="range" min="0.25" max="3" step="0.05"
                               value="${scale}"
                               data-oninput="updateSingleScalePreview('${filename}', this.value); this.nextElementSibling.textContent = parseFloat(this.value).toFixed(2) + 'x'"
                               data-onchange="updateSingleScale('${filename}', this.value)">
                        <span class="scale-value">${parseFloat(scale).toFixed(2)}x</span>
                    </div>
                </div>
                <div class="group-edit-item" style="flex-direction:column;gap:8px;">
                    <div style="display:flex;align-items:center;gap:8px;">
                        <label style="font-size:0.8rem;color:var(--text-secondary);white-space:nowrap;">Mat Color:</label>
                        <button style="font-size:0.7rem;padding:2px 6px;border:1px solid rgba(255,255,255,0.1);border-radius:4px;background:var(--surface-muted);color:var(--text-secondary);cursor:pointer;"
                                data-onclick="updateMatColor('${escAttr(filename)}', null); renderSinglePreviewImage('${escAttr(filename)}'); renderSingleControls('${escAttr(filename)}')">Reset</button>
                    </div>
                    <div class="color-presets" style="margin-top:0;">
                        ${NEUTRAL_PRESETS.map(p => `<div class="color-preset${currentMatColor === p.color ? ' active' : ''}" style="background:${p.color};width:22px;height:22px;${p.color === '#ffffff' ? 'border:1px solid rgba(255,255,255,0.2);' : ''}" title="${p.title}" data-onclick="updateMatColor('${escAttr(filename)}','${p.color}'); renderSingleControls('${escAttr(filename)}')"></div>`).join('')}
                    </div>
                    <button type="button" class="btn-more-colors" data-onclick="this.nextElementSibling.style.display = this.nextElementSibling.style.display === 'none' ? 'flex' : 'none'; this.textContent = this.nextElementSibling.style.display === 'none' ? 'More Colors' : 'Fewer Colors'">${ACCENT_PRESETS.some(p => p.color === currentMatColor) ? 'Fewer Colors' : 'More Colors'}</button>
                    <div class="color-presets accent-colors" style="margin-top:0;${ACCENT_PRESETS.some(p => p.color === currentMatColor) ? '' : 'display:none;'}">
                        ${ACCENT_PRESETS.map(p => `<div class="color-preset${currentMatColor === p.color ? ' active' : ''}" style="background:${p.color};width:22px;height:22px;${p.color === '#ffffff' ? 'border:1px solid rgba(255,255,255,0.2);' : ''}" title="${p.title}" data-onclick="updateMatColor('${escAttr(filename)}','${p.color}'); renderSingleControls('${escAttr(filename)}')"></div>`).join('')}
                    </div>
                </div>
                <div class="group-edit-item" style="flex-wrap:wrap;gap:12px;">
                    <div style="display:flex;align-items:center;gap:8px;flex:1;min-width:150px;">
                        <label style="font-size:0.8rem;color:var(--text-secondary);white-space:nowrap;">Finish:</label>
                        <select style="flex:1;padding:6px;border:1px solid rgba(255,255,255,0.1);border-radius:6px;background:var(--surface-muted);color:var(--text-primary);font-size:0.8rem;"
                                data-onchange="updateImageField('${escAttr(filename)}','mat_finish',this.value||null)">
                            <option value="" ${!currentFinish ? 'selected' : ''}>Default</option>
                            <option value="flat" ${currentFinish==='flat' ? 'selected' : ''}>Flat</option>
                            <option value="eggshell" ${currentFinish==='eggshell' ? 'selected' : ''}>Eggshell</option>
                            <option value="linen" ${currentFinish==='linen' ? 'selected' : ''}>Linen</option>
                            <option value="suede" ${currentFinish==='suede' ? 'selected' : ''}>Suede</option>
                            <option value="silk" ${currentFinish==='silk' ? 'selected' : ''}>Silk</option>
                        </select>
                    </div>
                    <div style="display:flex;align-items:center;gap:8px;flex:1;min-width:150px;">
                        <label style="font-size:0.8rem;color:var(--text-secondary);white-space:nowrap;">Effect:</label>
                        <select style="flex:1;padding:6px;border:1px solid rgba(255,255,255,0.1);border-radius:6px;background:var(--surface-muted);color:var(--text-primary);font-size:0.8rem;"
                                data-onchange="updateImageField('${escAttr(filename)}','border_effect',this.value||null); renderSingleControls('${escAttr(filename)}')">
                            <option value="" ${!currentBorderEffect ? 'selected' : ''}>Default</option>
                            <option value="bevel" ${currentBorderEffect==='bevel' ? 'selected' : ''}>Bevel</option>
                            <option value="shadow" ${currentBorderEffect==='shadow' ? 'selected' : ''}>Shadow</option>
                        </select>
                    </div>
                </div>
                <div class="group-edit-item" style="flex-wrap:wrap;gap:12px;">
                    <div style="display:flex;align-items:center;gap:8px;flex:1;min-width:150px;">
                        <label style="font-size:0.8rem;color:var(--text-secondary);white-space:nowrap;">${effectLabel}: <span id="single-bevel-val">${currentBevel !== '' ? currentBevel + 'px' : parseInt(bevelWidthInput.value) + 'px'}</span></label>
                        <input type="range" min="0" max="16" step="1" value="${currentBevel !== '' ? currentBevel : parseInt(bevelWidthInput.value)}"
                               style="flex:1;accent-color:#00d4ff;"
                               data-oninput="this.previousElementSibling.querySelector('span').textContent = this.value + 'px'; previewSingleBevel('${escAttr(filename)}', this.value)"
                               data-onchange="updateImageField('${escAttr(filename)}','bevel_width',parseInt(this.value))">
                    </div>
                </div>
                <div class="group-edit-item" style="gap:8px;">
                    <button class="btn btn-edit" data-onclick="enterCropMode('${escAttr(filename)}')">Crop</button>
                    <button class="btn btn-warning" data-onclick="clearCrop('${escAttr(filename)}')" ${!img.crop ? 'disabled style="opacity:0.5"' : ''}>Clear Crop</button>
                </div>
                <div class="group-edit-item" style="gap:8px;">
                    <button class="btn ${img.enabled ? 'btn-warning' : 'btn-success'}"
                            data-onclick="toggleImage('${escAttr(filename)}', ${!img.enabled}); renderSingleControls('${escAttr(filename)}')">
                        ${img.enabled ? '⊘ Hide' : '✓ Show'}
                    </button>
                    <button class="btn btn-danger" data-onclick="deleteImage('${escAttr(filename)}')">🗑 Delete</button>
                </div>
            `;
        }

        // ===== Crop Mode =====
        let cropState = null; // {filename, x, y, w, h, imgRect}

        function enterCropMode(filename) {
            const img = images.find(i => i.filename === filename);
            if (!img) return;

            const existing = img.crop || {x: 0, y: 0, w: 1, h: 1};
            cropState = { filename, x: existing.x, y: existing.y, w: existing.w, h: existing.h };

            // Show uncropped image in preview
            matPreview.style.backgroundColor = '#111';
            matPreview.classList.remove('mat-eggshell', 'mat-linen', 'mat-suede', 'mat-silk');

            const containerW = previewContent.clientWidth;
            const containerH = previewContent.clientHeight;
            const imgRatio = (img.width || 1) / (img.height || 1);

            let dispW, dispH;
            if (imgRatio > containerW / containerH) {
                dispW = containerW * 0.92;
                dispH = dispW / imgRatio;
            } else {
                dispH = containerH * 0.92;
                dispW = dispH * imgRatio;
            }
            const offsetX = (containerW - dispW) / 2;
            const offsetY = (containerH - dispH) / 2;

            // Store image rect for coordinate conversion
            cropState.imgRect = {x: offsetX, y: offsetY, w: dispW, h: dispH};

            previewContent.style.position = 'relative';
            previewContent.innerHTML = `
                <img id="crop-image" src="/uploads/${filename}"
                     style="position:absolute;left:${offsetX}px;top:${offsetY}px;width:${dispW}px;height:${dispH}px;user-select:none;pointer-events:none;">
                <div class="crop-overlay" id="crop-overlay"></div>
                <div class="crop-selection" id="crop-selection">
                    <div class="crop-handle nw" data-handle="nw"></div>
                    <div class="crop-handle ne" data-handle="ne"></div>
                    <div class="crop-handle sw" data-handle="sw"></div>
                    <div class="crop-handle se" data-handle="se"></div>
                    <div class="crop-handle n" data-handle="n"></div>
                    <div class="crop-handle s" data-handle="s"></div>
                    <div class="crop-handle e" data-handle="e"></div>
                    <div class="crop-handle w" data-handle="w"></div>
                </div>
                <div class="crop-toolbar">
                    <button class="btn btn-success" data-onclick="applyCrop()">Apply</button>
                    <button class="btn btn-warning" data-onclick="cancelCrop()">Cancel</button>
                </div>
            `;

            updateCropSelection();
            initCropHandlers();
        }

        function updateCropSelection() {
            if (!cropState) return;
            const sel = document.getElementById('crop-selection');
            if (!sel) return;
            const r = cropState.imgRect;
            sel.style.left   = (r.x + cropState.x * r.w) + 'px';
            sel.style.top    = (r.y + cropState.y * r.h) + 'px';
            sel.style.width  = (cropState.w * r.w) + 'px';
            sel.style.height = (cropState.h * r.h) + 'px';
        }

        function initCropHandlers() {
            const overlay = document.getElementById('crop-overlay');
            const sel = document.getElementById('crop-selection');
            if (!overlay || !sel) return;

            let dragMode = null; // 'move' or handle name
            let startX, startY, startCrop;

            function getPointer(e) {
                const t = e.touches ? e.touches[0] : e;
                const rect = previewContent.getBoundingClientRect();
                return {x: t.clientX - rect.left, y: t.clientY - rect.top};
            }

            function onStart(e) {
                const target = e.target;
                // Don't intercept taps on toolbar buttons
                if (target.closest('.crop-toolbar')) return;
                const handle = target.dataset ? target.dataset.handle : null;
                if (handle) {
                    dragMode = handle;
                } else if (target === sel || sel.contains(target)) {
                    dragMode = 'move';
                } else {
                    return;
                }
                e.preventDefault();
                const p = getPointer(e);
                startX = p.x;
                startY = p.y;
                startCrop = {x: cropState.x, y: cropState.y, w: cropState.w, h: cropState.h};
            }

            function onMove(e) {
                if (!dragMode) return;
                e.preventDefault();
                const p = getPointer(e);
                const r = cropState.imgRect;
                const dx = (p.x - startX) / r.w;
                const dy = (p.y - startY) / r.h;
                const minSize = 0.05;

                if (dragMode === 'move') {
                    cropState.x = Math.max(0, Math.min(1 - startCrop.w, startCrop.x + dx));
                    cropState.y = Math.max(0, Math.min(1 - startCrop.h, startCrop.y + dy));
                } else {
                    let nx = startCrop.x, ny = startCrop.y, nw = startCrop.w, nh = startCrop.h;

                    if (dragMode.includes('w')) {
                        const newX = Math.max(0, Math.min(startCrop.x + startCrop.w - minSize, startCrop.x + dx));
                        nw = startCrop.w - (newX - startCrop.x);
                        nx = newX;
                    }
                    if (dragMode.includes('e')) {
                        nw = Math.max(minSize, Math.min(1 - startCrop.x, startCrop.w + dx));
                    }
                    if (dragMode.includes('n')) {
                        const newY = Math.max(0, Math.min(startCrop.y + startCrop.h - minSize, startCrop.y + dy));
                        nh = startCrop.h - (newY - startCrop.y);
                        ny = newY;
                    }
                    if (dragMode.includes('s')) {
                        nh = Math.max(minSize, Math.min(1 - startCrop.y, startCrop.h + dy));
                    }

                    cropState.x = nx;
                    cropState.y = ny;
                    cropState.w = nw;
                    cropState.h = nh;
                }

                updateCropSelection();
            }

            function onEnd() {
                dragMode = null;
            }

            // Mouse events on the entire preview content
            previewContent.addEventListener('mousedown', onStart);
            document.addEventListener('mousemove', onMove);
            document.addEventListener('mouseup', onEnd);

            // Touch events
            previewContent.addEventListener('touchstart', onStart, {passive: false});
            document.addEventListener('touchmove', onMove, {passive: false});
            document.addEventListener('touchend', onEnd);

            // Store cleanup function
            cropState._cleanup = () => {
                previewContent.removeEventListener('mousedown', onStart);
                document.removeEventListener('mousemove', onMove);
                document.removeEventListener('mouseup', onEnd);
                previewContent.removeEventListener('touchstart', onStart);
                document.removeEventListener('touchmove', onMove);
                document.removeEventListener('touchend', onEnd);
            };
        }

        async function applyCrop() {
            if (!cropState) return;
            const crop = {
                x: Math.round(cropState.x * 1000) / 1000,
                y: Math.round(cropState.y * 1000) / 1000,
                w: Math.round(cropState.w * 1000) / 1000,
                h: Math.round(cropState.h * 1000) / 1000
            };

            // Skip if it's the full image (no actual crop)
            const isFullImage = crop.x === 0 && crop.y === 0 && crop.w === 1 && crop.h === 1;
            const saveCrop = isFullImage ? null : crop;

            const fn = cropState.filename;
            if (cropState._cleanup) cropState._cleanup();
            cropState = null;

            const img = images.find(i => i.filename === fn);
            if (img) img.crop = saveCrop;

            await updateImageField(fn, 'crop', saveCrop);
            if (previewGroupId) {
                renderGroupPreviewImage(previewGroupId);
                renderGroupControls(previewGroupId);
            } else {
                renderSinglePreviewImage(fn);
                renderSingleControls(fn);
            }
        }

        function cancelCrop() {
            if (!cropState) return;
            const fn = cropState.filename;
            const groupId = previewGroupId;
            if (cropState._cleanup) cropState._cleanup();
            cropState = null;
            if (groupId) {
                renderGroupPreviewImage(groupId);
                renderGroupControls(groupId);
            } else {
                renderSinglePreviewImage(fn);
                renderSingleControls(fn);
            }
        }

        async function clearCrop(filename) {
            const img = images.find(i => i.filename === filename);
            if (img) img.crop = null;
            await updateImageField(filename, 'crop', null);
            if (previewGroupId) {
                renderGroupPreviewImage(previewGroupId);
                renderGroupControls(previewGroupId);
            } else {
                renderSinglePreviewImage(filename);
                renderSingleControls(filename);
            }
        }

        function openGroupPreview(groupId) {
            const group = groups[groupId];
            if (!group) return;

            previewGroupId = groupId;
            previewFilename = null;

            // Make section visible FIRST so container has real dimensions
            previewControls.classList.add('active');
            previewSection.classList.add('has-controls');
            previewSection.classList.add('active');
            document.querySelector('.preview-controls-actions').style.display = '';
            document.getElementById('preview-ungroup-btn').style.display = '';
            document.getElementById('start-group').disabled = true;

            // Now render (container clientWidth/clientHeight are non-zero)
            renderGroupPreviewImage(groupId);
            renderGroupControls(groupId);
        }

        function renderGroupPreviewImage(groupId) {
            const group = groups[groupId];
            if (!group) return;

            const matColor = group.mat_color || matColorInput.value;
            const finish = group.mat_finish || matFinishSelect.value;
            const rawGroupEffectSize = group.bevel_width ?? parseInt(bevelWidthInput.value);
            const groupEffectSize = previewScaledEffect(rawGroupEffectSize);
            const groupBorderEffect = group.border_effect || borderEffectSelect.value;

            matPreview.style.backgroundColor = matColor;
            matPreview.classList.remove('mat-eggshell', 'mat-linen', 'mat-suede', 'mat-silk');
            if (finish !== 'flat') matPreview.classList.add('mat-' + finish);

            const groupImages = (group.images || []).map(f => images.find(i => i.filename === f)).filter(Boolean);
            const scales = group.scales || {};
            const count = groupImages.length;

            if (count <= 3) {
                // Compute explicit pixel sizes so all images render at the same height (when scale=1)
                const containerW = matPreview.clientWidth;
                const containerH = matPreview.clientHeight;
                const effectSpace = groupBorderEffect === 'shadow' ? Math.round(groupEffectSize * 2) : groupEffectSize;
                const effect2 = (effectSpace > 0 ? effectSpace : 0) * 2;

                const imgInfos = groupImages.map(img => {
                    const rawAr = (img.width || 1) / (img.height || 1);
                    const c = img.crop;
                    const ar = c ? rawAr * c.w / c.h : rawAr;
                    return { filename: img.filename, ar, scale: scales[img.filename] || 1.0, crop: c };
                });

                // At base height h=1, each image has width = ar * scale
                const totalWidthAtUnitHeight = imgInfos.reduce((sum, info) => sum + info.ar * info.scale, 0);
                const maxScale = Math.max(...imgInfos.map(i => i.scale));
                // Reserve space for N+1 equal gaps (space-evenly) — each gap ~5% of container
                const gapFraction = 0.05;
                const totalGapFraction = gapFraction * (count + 1);
                // Subtract effect space from usable area
                const totalEffectW = effect2 * count;
                const usableW = containerW * (1 - totalGapFraction) - totalEffectW;
                const usableH = containerH * 0.85 - effect2;
                // Base height: largest h such that total width fits and tallest image fits
                const baseHeight = Math.min(usableH / maxScale, usableW / totalWidthAtUnitHeight);

                previewContent.innerHTML = `<div class="group-preview-images">${imgInfos.map(info => {
                    const h = baseHeight * info.scale;
                    const w = h * info.ar;
                    let imgHtml;
                    if (info.crop) {
                        const fullW = Math.round(w / info.crop.w);
                        const fullH = Math.round(h / info.crop.h);
                        const offsetX = Math.round(-info.crop.x * fullW);
                        const offsetY = Math.round(-info.crop.y * fullH);
                        imgHtml = `<div style="width:${Math.round(w)}px;height:${Math.round(h)}px;overflow:hidden;line-height:0;flex-shrink:0;">` +
                                  `<img src="/uploads/${info.filename}" alt="${info.filename}" style="width:${fullW}px;height:${fullH}px;margin-left:${offsetX}px;margin-top:${offsetY}px;display:block;max-width:none;max-height:none;">` +
                                  `</div>`;
                    } else {
                        imgHtml = `<img src="/uploads/${info.filename}" alt="${info.filename}" style="width:${Math.round(w)}px;height:${Math.round(h)}px;max-width:none;max-height:none;flex-shrink:0;">`;
                    }
                    return makeEffectHtml(imgHtml, groupEffectSize, matColor, groupBorderEffect);
                }).join('')}</div>`;
            } else {
                const cols = Math.ceil(Math.sqrt(count));
                const rows = Math.ceil(count / cols);
                const previewW = matPreview.clientWidth;
                const previewH = matPreview.clientHeight;
                const gapFrac = 0.04;
                const cellW = previewW * (1 - gapFrac * (cols + 1)) / cols;
                const cellH = previewH * (1 - gapFrac * (rows + 1)) / rows;
                const effectSpace = groupBorderEffect === 'shadow' ? Math.round(groupEffectSize * 2) : groupEffectSize;
                const usableCellW = cellW - 2 * effectSpace;
                const usableCellH = cellH - 2 * effectSpace;

                previewContent.innerHTML = `<div class="group-preview-grid" style="grid-template-columns:repeat(${cols},1fr);">${groupImages.map(img => {
                    const c = img.crop;
                    let imgHtml;
                    if (c) {
                        const rawAr = (img.width || 1) / (img.height || 1);
                        const cropAr = rawAr * c.w / c.h;
                        let dW, dH;
                        if (cropAr > usableCellW / usableCellH) {
                            dW = usableCellW;
                            dH = dW / cropAr;
                        } else {
                            dH = usableCellH;
                            dW = dH * cropAr;
                        }
                        const fullW = Math.round(dW / c.w);
                        const fullH = Math.round(dH / c.h);
                        const offsetX = Math.round(-c.x * fullW);
                        const offsetY = Math.round(-c.y * fullH);
                        imgHtml = `<div style="width:${Math.round(dW)}px;height:${Math.round(dH)}px;overflow:hidden;line-height:0;">` +
                                  `<img src="/uploads/${img.filename}" alt="${img.filename}" style="width:${fullW}px;height:${fullH}px;margin-left:${offsetX}px;margin-top:${offsetY}px;display:block;max-width:none;max-height:none;">` +
                                  `</div>`;
                    } else {
                        imgHtml = `<img src="/uploads/${img.filename}" alt="${img.filename}">`;
                    }
                    return makeEffectHtml(imgHtml, groupEffectSize, matColor, groupBorderEffect);
                }).join('')}</div>`;
            }
        }

        function renderGroupControls(groupId) {
            const group = groups[groupId];
            if (!group) return;

            const groupImages = (group.images || []).map(f => images.find(i => i.filename === f)).filter(Boolean);
            const scales = group.scales || {};
            const count = groupImages.length;
            const currentFinish = group.mat_finish || '';
            const currentBevel = group.bevel_width ?? '';
            const currentBorderEffect = group.border_effect || '';
            const effectLabel = (currentBorderEffect || borderEffectSelect.value) === 'shadow' ? 'Shadow' : 'Bevel';

            previewControlsTitle.textContent = `Group (${count} images)`;

            const groupSettingsHtml = `
                <div class="group-edit-item" style="flex-wrap:wrap;gap:12px;padding-bottom:12px;margin-bottom:8px;border-bottom:1px solid rgba(255,255,255,0.1);">
                    <div style="display:flex;align-items:center;gap:8px;flex:1;min-width:150px;">
                        <label style="font-size:0.8rem;color:var(--text-secondary);white-space:nowrap;">Finish:</label>
                        <select style="flex:1;padding:6px;border:1px solid rgba(255,255,255,0.1);border-radius:6px;background:var(--surface-muted);color:var(--text-primary);font-size:0.8rem;"
                                data-onchange="updateGroupField('${groupId}','mat_finish',this.value||null)">
                            <option value="" ${!currentFinish ? 'selected' : ''}>Default</option>
                            <option value="flat" ${currentFinish==='flat' ? 'selected' : ''}>Flat</option>
                            <option value="eggshell" ${currentFinish==='eggshell' ? 'selected' : ''}>Eggshell</option>
                            <option value="linen" ${currentFinish==='linen' ? 'selected' : ''}>Linen</option>
                            <option value="suede" ${currentFinish==='suede' ? 'selected' : ''}>Suede</option>
                            <option value="silk" ${currentFinish==='silk' ? 'selected' : ''}>Silk</option>
                        </select>
                    </div>
                    <div style="display:flex;align-items:center;gap:8px;flex:1;min-width:150px;">
                        <label style="font-size:0.8rem;color:var(--text-secondary);white-space:nowrap;">Effect:</label>
                        <select style="flex:1;padding:6px;border:1px solid rgba(255,255,255,0.1);border-radius:6px;background:var(--surface-muted);color:var(--text-primary);font-size:0.8rem;"
                                data-onchange="updateGroupField('${groupId}','border_effect',this.value||null); renderGroupControls('${groupId}')">
                            <option value="" ${!currentBorderEffect ? 'selected' : ''}>Default</option>
                            <option value="bevel" ${currentBorderEffect==='bevel' ? 'selected' : ''}>Bevel</option>
                            <option value="shadow" ${currentBorderEffect==='shadow' ? 'selected' : ''}>Shadow</option>
                        </select>
                    </div>
                    <div style="display:flex;align-items:center;gap:8px;flex:1;min-width:150px;">
                        <label style="font-size:0.8rem;color:var(--text-secondary);white-space:nowrap;">${effectLabel}: <span id="group-bevel-val">${currentBevel !== '' ? currentBevel + 'px' : parseInt(bevelWidthInput.value) + 'px'}</span></label>
                        <input type="range" min="0" max="16" step="1" value="${currentBevel !== '' ? currentBevel : parseInt(bevelWidthInput.value)}"
                               style="flex:1;accent-color:#00d4ff;"
                               data-oninput="this.previousElementSibling.querySelector('span').textContent = this.value + 'px'; previewGroupBevel('${groupId}', this.value)"
                               data-onchange="updateGroupField('${groupId}','bevel_width',parseInt(this.value))">
                    </div>
                </div>
            `;

            const imageControlsHtml = groupImages.map((img, idx) => `
                <div class="group-edit-item" style="flex-wrap:wrap;">
                    <img class="group-edit-thumb" src="/uploads/${img.filename}" alt="${img.filename}">
                    <div class="group-edit-controls">
                        <button class="move-btn" ${idx === 0 ? 'disabled' : ''} data-onclick="moveGroupImage('${groupId}', ${idx}, -1)" title="Move left">&larr;</button>
                        <input type="range" min="0.25" max="3" step="0.05"
                               value="${scales[img.filename] || 1.0}"
                               data-oninput="updateGroupScalePreview('${groupId}', '${img.filename}', this.value); this.nextElementSibling.textContent = parseFloat(this.value).toFixed(2) + 'x'"
                               data-onchange="updateGroupScale('${groupId}', '${img.filename}', this.value)">
                        <span class="scale-value">${parseFloat(scales[img.filename] || 1.0).toFixed(2)}x</span>
                        <button class="move-btn" ${idx === count - 1 ? 'disabled' : ''} data-onclick="moveGroupImage('${groupId}', ${idx}, 1)" title="Move right">&rarr;</button>
                    </div>
                    <div style="display:flex;gap:8px;width:100%;padding-left:48px;">
                        <button class="btn btn-edit" style="font-size:0.75rem;padding:4px 8px;" data-onclick="enterCropMode('${escAttr(img.filename)}')">Crop</button>
                        <button class="btn btn-warning" style="font-size:0.75rem;padding:4px 8px;${!img.crop ? 'opacity:0.5;' : ''}" data-onclick="clearCrop('${escAttr(img.filename)}')" ${!img.crop ? 'disabled' : ''}>Clear Crop</button>
                    </div>
                </div>
            `).join('');

            previewControlsBody.innerHTML = groupSettingsHtml + imageControlsHtml;
        }

        function refreshPreview() {
            if (previewGroupId) {
                renderGroupPreviewImage(previewGroupId);
            }
        }

        async function autoMatchHeights() {
            if (!previewGroupId) return;
            const group = groups[previewGroupId];
            if (!group) return;

            const groupImages = (group.images || []).map(f => images.find(i => i.filename === f)).filter(Boolean);
            if (groupImages.length < 2) return;

            // Set all scales to 1.0 — the explicit sizing logic in renderGroupPreviewImage
            // already ensures equal heights when all scales are the same
            const newScales = {};
            groupImages.forEach(img => {
                newScales[img.filename] = 1.0;
            });

            group.scales = newScales;

            try {
                await fetch(`/api/groups/${previewGroupId}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ scales: newScales })
                });
                renderGroupControls(previewGroupId);
                renderGroupPreviewImage(previewGroupId);
                showStatus('Heights matched', 'success');
            } catch (err) {
                showStatus('Failed to match heights', 'error');
            }
        }

        function closePreview() {
            previewSection.classList.remove('active');
            previewSection.classList.remove('has-controls');
            previewControls.classList.remove('active');
            document.querySelector('.preview-controls-actions').style.display = '';
            document.getElementById('start-group').disabled = false;
            previewFilename = null;
            previewGroupId = null;
        }

        // ===== Helpers =====
        function escAttr(str) {
            return String(str).replace(/[&"'<>]/g, c => ({'&':'&amp;','"':'&quot;',"'":'&#39;','<':'&lt;','>':'&gt;'}[c]));
        }

        function cleanFilename(filename) {
            return filename.replace(/^[a-f0-9]+_/, '');
        }

        function formatSize(bytes) {
            if (bytes < 1024) return bytes + ' B';
            if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
            return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
        }

        function updateStats() {
            const total = images.length;
            const enabled = images.filter(img => img.enabled).length;
            document.getElementById('total-count').textContent = total;
            document.getElementById('enabled-count').textContent = enabled;
            document.getElementById('disabled-count').textContent = total - enabled;
        }

        // ===== Selection & Select Mode =====
        let selectMode = false;

        function handleCardClick(filename) {
            if (selectMode) {
                toggleSelect(filename);
            } else {
                openPreview(filename);
            }
        }

        function startGroupMode() {
            selectMode = true;
            document.body.classList.add('select-mode');
            document.getElementById('start-group').style.display = 'none';
            document.getElementById('done-group').style.display = '';
            document.querySelector('.toolbar-right').style.display = '';
            document.getElementById('selection-count-wrapper').style.display = '';
        }

        function exitSelectMode() {
            selectMode = false;
            document.body.classList.remove('select-mode');
            document.getElementById('start-group').style.display = '';
            document.getElementById('done-group').style.display = 'none';
            document.querySelector('.toolbar-right').style.display = 'none';
            document.getElementById('selection-count-wrapper').style.display = 'none';
            selectedImages.clear();
            updateSelectionUI();
        }

        async function finishGroupMode() {
            if (selectedImages.size >= 2) {
                await createGroup();
            }
            exitSelectMode();
        }

        function toggleSelect(filename) {
            if (selectedImages.has(filename)) {
                selectedImages.delete(filename);
            } else {
                selectedImages.add(filename);
            }
            updateSelectionUI();
        }

        function updateSelectionUI() {
            document.getElementById('selected-count').textContent = selectedImages.size;

            const hasSelection = selectedImages.size > 0;
            document.getElementById('bulk-enable').disabled = !hasSelection;
            document.getElementById('bulk-disable').disabled = !hasSelection;
            document.getElementById('bulk-delete').disabled = !hasSelection;

            document.querySelectorAll('.image-card').forEach(card => {
                const filename = card.dataset.filename;
                card.classList.toggle('selected', selectedImages.has(filename));
            });
        }

        function getFilteredImages() {
            if (currentFilter === 'enabled') return images.filter(img => img.enabled);
            if (currentFilter === 'disabled') return images.filter(img => !img.enabled);
            return images;
        }

        // Filter tabs
        document.querySelectorAll('.filter-tab').forEach(tab => {
            tab.addEventListener('click', () => {
                document.querySelectorAll('.filter-tab').forEach(t => t.classList.remove('active'));
                tab.classList.add('active');
                currentFilter = tab.dataset.filter;
                renderGallery();
            });
        });

        // ===== Image Actions =====
        async function updateMatColor(filename, color) {
            try {
                const response = await fetch(`/api/gallery/${filename}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ mat_color: color })
                });
                if (response.ok) {
                    const img = images.find(i => i.filename === filename);
                    if (img) img.mat_color = color;
                    // Update preview if this image is being previewed
                    if (previewFilename === filename) {
                        renderSinglePreviewImage(filename);
                    }
                    showStatus('Mat color updated', 'success');
                }
            } catch (err) {
                showStatus('Failed to update mat color', 'error');
            }
        }

        async function toggleImage(filename, enabled) {
            try {
                const response = await fetch(`/api/gallery/${filename}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ enabled })
                });
                if (response.ok) {
                    const img = images.find(i => i.filename === filename);
                    if (img) img.enabled = enabled;
                    renderGallery();
                    updateStats();
                    showStatus(enabled ? 'Image visible' : 'Image hidden', 'success');
                }
            } catch (err) {
                showStatus('Failed to update image', 'error');
            }
        }

        async function deleteImage(filename) {
            if (!confirm('Delete this image permanently?')) return;

            try {
                const response = await fetch(`/api/gallery/${filename}`, {
                    method: 'DELETE'
                });
                if (response.ok) {
                    images = images.filter(i => i.filename !== filename);
                    selectedImages.delete(filename);
                    if (previewFilename === filename) closePreview();
                    renderGallery();
                    updateStats();
                    showStatus('Image deleted', 'success');
                }
            } catch (err) {
                showStatus('Failed to delete image', 'error');
            }
        }

        // ===== Bulk Actions =====
        document.getElementById('bulk-enable').addEventListener('click', () => bulkAction('enable'));
        document.getElementById('bulk-disable').addEventListener('click', () => bulkAction('disable'));
        document.getElementById('bulk-delete').addEventListener('click', async () => {
            if (!confirm(`Delete ${selectedImages.size} images permanently?`)) return;
            await bulkAction('delete');
        });

        async function bulkAction(action) {
            const filenames = Array.from(selectedImages);
            try {
                const response = await fetch('/api/gallery/bulk', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ action, filenames })
                });
                const data = await response.json();
                if (response.ok) {
                    showStatus(data.message, 'success');
                    if (action === 'delete') {
                        images = images.filter(i => !filenames.includes(i.filename));
                        if (filenames.includes(previewFilename)) closePreview();
                    } else {
                        filenames.forEach(f => {
                            const img = images.find(i => i.filename === f);
                            if (img) img.enabled = (action === 'enable');
                        });
                    }
                    selectedImages.clear();
                    renderGallery();
                    updateStats();
                }
            } catch (err) {
                showStatus('Action failed', 'error');
            }
        }

        // ===== Group Actions =====
        async function createGroup() {
            const filenames = Array.from(selectedImages);
            if (filenames.length < 2) return;

            try {
                const response = await fetch('/api/groups', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ images: filenames })
                });
                if (response.ok) {
                    selectedImages.clear();
                    showStatus(`Created group with ${filenames.length} images`, 'success');
                    loadGallery();
                }
            } catch (err) {
                showStatus('Failed to create group', 'error');
            }
        }

        async function ungroupFromPreview() {
            if (previewGroupId) await ungroupGroup(previewGroupId);
        }

        async function ungroupGroup(groupId) {
            try {
                const response = await fetch(`/api/groups/${groupId}`, {
                    method: 'DELETE'
                });
                if (response.ok) {
                    if (previewGroupId === groupId) closePreview();
                    showStatus('Group dissolved', 'success');
                    loadGallery();
                }
            } catch (err) {
                showStatus('Failed to ungroup', 'error');
            }
        }

        async function deleteGroup(groupId) {
            const group = groups[groupId];
            if (!group) return;
            if (!confirm(`Delete this group and all ${group.images.length} images permanently?`)) return;

            try {
                // Delete each image in the group
                for (const filename of group.images) {
                    await fetch(`/api/gallery/${filename}`, { method: 'DELETE' });
                }
                // Then delete the group itself
                await fetch(`/api/groups/${groupId}`, { method: 'DELETE' });
                if (previewGroupId === groupId) closePreview();
                showStatus('Group and images deleted', 'success');
                loadGallery();
            } catch (err) {
                showStatus('Failed to delete group', 'error');
            }
        }

        function updateGroupScalePreview(groupId, filename, scaleValue) {
            const group = groups[groupId];
            if (!group) return;
            if (!group.scales) group.scales = {};
            group.scales[filename] = parseFloat(scaleValue);
            renderGroupPreviewImage(groupId);
        }

        async function updateGroupScale(groupId, filename, scaleValue) {
            const group = groups[groupId];
            if (!group) return;

            if (!group.scales) group.scales = {};
            group.scales[filename] = parseFloat(scaleValue);

            try {
                await fetch(`/api/groups/${groupId}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ scales: group.scales })
                });
                renderGroupPreviewImage(groupId);
            } catch (err) {
                showStatus('Failed to update scale', 'error');
            }
        }

        function updateSingleScalePreview(filename, scaleValue) {
            const img = images.find(i => i.filename === filename);
            if (!img) return;
            img.scale = parseFloat(scaleValue);
            renderSinglePreviewImage(filename);
        }

        async function updateSingleScale(filename, scaleValue) {
            const img = images.find(i => i.filename === filename);
            if (!img) return;
            img.scale = parseFloat(scaleValue);
            try {
                await fetch(`/api/gallery/${filename}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ scale: img.scale })
                });
                renderSinglePreviewImage(filename);
            } catch (err) {
                showStatus('Failed to update scale', 'error');
            }
        }

        async function moveGroupImage(groupId, currentIdx, direction) {
            const group = groups[groupId];
            if (!group) return;

            const imgs = [...group.images];
            const newIdx = currentIdx + direction;
            if (newIdx < 0 || newIdx >= imgs.length) return;

            // Swap
            [imgs[currentIdx], imgs[newIdx]] = [imgs[newIdx], imgs[currentIdx]];
            group.images = imgs;

            try {
                const response = await fetch(`/api/groups/${groupId}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ images: imgs })
                });
                if (response.ok) {
                    renderGallery();
                    // Re-render controls and preview
                    renderGroupControls(groupId);
                    renderGroupPreviewImage(groupId);
                }
            } catch (err) {
                showStatus('Failed to reorder images', 'error');
            }
        }

        async function updateGroupMatColor(groupId, color) {
            try {
                const response = await fetch(`/api/groups/${groupId}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ mat_color: color })
                });
                if (response.ok) {
                    groups[groupId].mat_color = color;
                    if (previewGroupId === groupId) {
                        matPreview.style.backgroundColor = color;
                    }
                    showStatus('Group mat color updated', 'success');
                }
            } catch (err) {
                showStatus('Failed to update group mat color', 'error');
            }
        }

        async function updateGroupField(groupId, field, value) {
            const group = groups[groupId];
            if (!group) return;
            group[field] = value;

            try {
                await fetch(`/api/groups/${groupId}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ [field]: value })
                });
                renderGroupPreviewImage(groupId);
                showStatus(`Group ${field.replace('_', ' ')} updated`, 'success');
            } catch (err) {
                showStatus(`Failed to update group ${field}`, 'error');
            }
        }

        function previewGroupBevel(groupId, val) {
            const group = groups[groupId];
            if (!group) return;
            // Temporarily set for preview
            const origBevel = group.bevel_width;
            group.bevel_width = val == -1 ? undefined : parseInt(val);
            renderGroupPreviewImage(groupId);
            group.bevel_width = origBevel;
        }

        // ===== Settings =====
        function updatePreviewAspectRatio() {
            const ratio = targetAspectRatioSelect.value || '16:9';
            matPreview.style.setProperty('--preview-aspect-ratio', ratio.replace(':', '/'));
        }

        function saveSlideshowSettings() {
            const settings = {
                slideshow_interval: parseInt(slideshowIntervalInput.value),
                transition_duration: parseFloat(transitionDurationInput.value),
                fit_mode: fitModeSelect.value,
                shuffle: shuffleCheckbox.checked,
                target_aspect_ratio: targetAspectRatioSelect.value
            };

            fetch('/api/settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(settings)
            }).then(() => {
                showStatus('Slideshow settings applied', 'success');
            }).catch(() => {
                showStatus('Failed to apply slideshow settings', 'error');
            });
        }

        function applyMatSettings() {
            const settings = {
                mat_color: matColorInput.value,
                mat_finish: matFinishSelect.value,
                bevel_width: parseInt(bevelWidthInput.value),
                border_effect: borderEffectSelect.value
            };

            fetch('/api/settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(settings)
            }).then(() => {
                showStatus('Mat settings applied', 'success');
                // Re-render preview if an image/group is shown and uses defaults
                if (previewFilename) {
                    const img = images.find(i => i.filename === previewFilename);
                    if (img && !img.mat_color) renderSinglePreviewImage(previewFilename);
                } else if (previewGroupId) {
                    const group = groups[previewGroupId];
                    if (group && !group.mat_color) renderGroupPreviewImage(previewGroupId);
                }
            }).catch(() => {
                showStatus('Failed to apply mat settings', 'error');
            });

            updateColorPresets();
            updatePreviewTexture();
        }

        // Mat settings: Apply button (not auto-save)
        document.getElementById('apply-mat-settings').addEventListener('click', applyMatSettings);

        // Live preview updates for mat settings (no save)
        matFinishSelect.addEventListener('change', updatePreviewTexture);
        borderEffectSelect.addEventListener('change', () => {
            effectSizeLabel.textContent = borderEffectSelect.value === 'shadow' ? 'Shadow' : 'Bevel';
        });
        bevelWidthInput.addEventListener('input', () => {
            bevelValueLabel.textContent = bevelWidthInput.value + 'px';
        });

        // Slideshow settings: Apply button
        document.getElementById('apply-slideshow-settings').addEventListener('click', saveSlideshowSettings);
        targetAspectRatioSelect.addEventListener('change', updatePreviewAspectRatio);

        function updateColorPresets() {
            document.querySelectorAll('.settings-column .color-preset').forEach(preset => {
                preset.classList.toggle('active', preset.dataset.color === matColorInput.value);
            });
            // If selected color is in accent presets, auto-expand
            const inAccent = ACCENT_PRESETS.some(p => p.color === matColorInput.value);
            if (inAccent) {
                const accentEl = document.getElementById('accent-color-presets');
                if (accentEl.style.display === 'none') toggleMoreColors();
            }
        }
        updateColorPresets();

        // ===== Per-image Field Updates =====
        async function updateImageField(filename, field, value) {
            const img = images.find(i => i.filename === filename);
            if (img) img[field] = value;

            try {
                await fetch(`/api/gallery/${filename}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ [field]: value })
                });
                // Re-render preview
                if (previewFilename === filename) renderSinglePreviewImage(filename);
                showStatus(`${field.replace('_', ' ')} updated`, 'success');
            } catch (err) {
                showStatus(`Failed to update ${field}`, 'error');
            }
        }

        function previewSingleBevel(filename, val) {
            const img = images.find(i => i.filename === filename);
            if (!img) return;
            // Temporarily override bevel_width for preview, then render normally
            const savedBevel = img.bevel_width;
            img.bevel_width = parseInt(val);
            renderSinglePreviewImage(filename);
            img.bevel_width = savedBevel;
        }

        // ===== Bevel + Texture Helpers =====
        function getBevelColors(matHex) {
            let hex = matHex.replace('#', '');
            if (hex.length === 3) hex = hex[0]+hex[0]+hex[1]+hex[1]+hex[2]+hex[2];
            const r = parseInt(hex.substring(0, 2), 16);
            const g = parseInt(hex.substring(2, 4), 16);
            const b = parseInt(hex.substring(4, 6), 16);
            const luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
            const innerAlpha = luminance < 0.5 ? 0.30 : 0.20;
            return { outer: 'rgba(0,0,0,0.00)', inner: `rgba(0,0,0,${innerAlpha})` };
        }

        function makeBevelStripHtml(bevelWidth) {
            const w = bevelWidth + 'px';
            const strips = [
                { pos: `top:0;left:0;right:0;height:${w}`,    clip: `polygon(0 0,100% 0,calc(100% - ${w}) 100%,${w} 100%)`,       grad: 'to bottom' },
                { pos: `bottom:0;left:0;right:0;height:${w}`, clip: `polygon(${w} 0,calc(100% - ${w}) 0,100% 100%,0 100%)`,       grad: 'to top'    },
                { pos: `top:0;left:0;bottom:0;width:${w}`,    clip: `polygon(0 0,100% ${w},100% calc(100% - ${w}),0 100%)`,       grad: 'to right'  },
                { pos: `top:0;right:0;bottom:0;width:${w}`,   clip: `polygon(0 ${w},100% 0,100% 100%,0 calc(100% - ${w}))`,       grad: 'to left'   },
            ];
            return strips.map(({ pos, clip, grad }) =>
                `<div style="position:absolute;${pos};clip-path:${clip};background:linear-gradient(${grad},var(--bevel-outer),var(--bevel-inner));pointer-events:none;z-index:1;"></div>`
            ).join('');
        }

        function getShadowStyle(size) {
            const blur = size * 2;
            const spread = Math.round(size * 0.5);
            const yOffset = Math.round(size * 0.5);
            return `0 ${yOffset}px ${blur}px ${spread}px rgba(0,0,0,0.35)`;
        }

        function makeBevelHtml(innerHtml, bevelWidth, matColor) {
            if (!bevelWidth || bevelWidth <= 0) return innerHtml;
            const bw = Math.round(bevelWidth);
            const bevelColors = getBevelColors(matColor);
            return `<div class="mat-bevel" style="--bevel-w:${bw}px;--bevel-mat:${matColor};--bevel-outer:${bevelColors.outer};--bevel-inner:${bevelColors.inner}">${innerHtml}${makeBevelStripHtml(bw)}</div>`;
        }

        function previewScaledEffect(effectSize) {
            const pw = previewContent.clientWidth || matPreview.clientWidth || 800;
            return effectSize > 0 ? Math.max(0.5, effectSize * pw / 1920) : 0;
        }

        function makeEffectHtml(innerHtml, effectSize, matColor, borderEffect) {
            if (!effectSize || effectSize <= 0) return innerHtml;
            if (borderEffect === 'shadow') {
                const shadow = getShadowStyle(effectSize);
                // Wrap in shadow container and apply box-shadow to the inner element
                if (innerHtml.includes('style="')) {
                    return `<div class="mat-shadow">${innerHtml.replace('style="', 'style="box-shadow:' + shadow + ';')}</div>`;
                }
                return `<div class="mat-shadow" style="box-shadow:${shadow};display:inline-flex;line-height:0;">${innerHtml}</div>`;
            }
            return makeBevelHtml(innerHtml, effectSize, matColor);
        }

        function updatePreviewTexture() {
            const finish = matFinishSelect.value;
            matPreview.classList.remove('mat-eggshell', 'mat-linen', 'mat-suede', 'mat-silk');
            if (finish !== 'flat') {
                matPreview.classList.add('mat-' + finish);
            }
        }

        // ===== Status Messages =====
        function showStatus(message, type) {
            statusEl.textContent = message;
            statusEl.className = 'status ' + type;
            setTimeout(() => {
                statusEl.className = 'status';
            }, 3000);
        }

        // ===== TV Schedules =====
        const DAY_LABELS = ['M', 'T', 'W', 'T', 'F', 'S', 'S'];
        const DAY_NAMES = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
        let tvSchedules = [];

        async function loadTvSchedules() {
            try {
                const resp = await fetch('/api/tv-schedules');
                const data = await resp.json();
                tvSchedules = data.schedules || [];
                const cecAvailable = data.cec_available;

                document.getElementById('cec-unavailable-msg').style.display =
                    cecAvailable ? 'none' : 'block';
                document.getElementById('cec-controls').style.display =
                    cecAvailable ? 'block' : 'none';

                renderTvSchedules();
            } catch (err) {
                console.error('Failed to load TV schedules:', err);
            }
        }

        function renderTvSchedules() {
            const list = document.getElementById('tv-schedule-list');
            list.innerHTML = tvSchedules.map((sched, idx) => `
                <div class="tv-schedule-item">
                    <div class="tv-schedule-times">
                        <input type="time" value="${sched.on_time}"
                               data-onchange="updateScheduleField(${idx}, 'on_time', this.value)">
                        <span>to</span>
                        <input type="time" value="${sched.off_time}"
                               data-onchange="updateScheduleField(${idx}, 'off_time', this.value)">
                    </div>
                    <div class="tv-schedule-days">
                        ${DAY_LABELS.map((label, d) => `
                            <div class="day-checkbox${sched.days.includes(d) ? ' active' : ''}"
                                 title="${DAY_NAMES[d]}"
                                 data-onclick="toggleScheduleDay(${idx}, ${d})">${label}</div>
                        `).join('')}
                    </div>
                    <div class="tv-schedule-actions">
                        <label class="toggle" style="transform: scale(0.8);">
                            <input type="checkbox" ${sched.enabled ? 'checked' : ''}
                                   data-onchange="updateScheduleField(${idx}, 'enabled', this.checked)">
                            <span class="toggle-slider"></span>
                        </label>
                        <button class="btn btn-refresh" data-onclick="removeTvSchedule(${idx})"
                                style="padding: 4px 10px; font-size: 0.75rem;">Remove</button>
                    </div>
                </div>
            `).join('');
        }

        function addTvSchedule() {
            tvSchedules.push({
                id: 'sched_' + Math.random().toString(36).substr(2, 8),
                on_time: '07:00',
                off_time: '23:00',
                days: [0, 1, 2, 3, 4, 5, 6],
                enabled: true
            });
            renderTvSchedules();
            saveTvSchedules();
        }

        function removeTvSchedule(idx) {
            tvSchedules.splice(idx, 1);
            renderTvSchedules();
            saveTvSchedules();
        }

        function updateScheduleField(idx, field, value) {
            tvSchedules[idx][field] = value;
            saveTvSchedules();
        }

        function toggleScheduleDay(idx, day) {
            const days = tvSchedules[idx].days;
            const pos = days.indexOf(day);
            if (pos >= 0) {
                days.splice(pos, 1);
            } else {
                days.push(day);
                days.sort();
            }
            renderTvSchedules();
            saveTvSchedules();
        }

        async function saveTvSchedules() {
            try {
                await fetch('/api/tv-schedules', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ schedules: tvSchedules })
                });
            } catch (err) {
                showStatus('Failed to save TV schedules', 'error');
            }
        }

        async function testCec(command) {
            try {
                const resp = await fetch('/api/cec/test', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ command })
                });
                const data = await resp.json();
                if (data.success) {
                    showStatus(`TV ${command === 'on' ? 'on' : 'off'} command sent`, 'success');
                } else {
                    showStatus('CEC failed: ' + (data.error || 'Unknown error'), 'error');
                }
            } catch (err) {
                showStatus('CEC test failed', 'error');
            }
        }

        loadTvSchedules();

        // ===== Network Info =====
        if (document.body.dataset.loadNetworkInfo === 'true') (async function loadNetworkInfo() {
            try {
                const resp = await fetch('/api/network-info');
                if (!resp.ok) return;
                const info = await resp.json();
                const el = document.getElementById('network-info-content');
                if (!el) return;
                let html = '';
                if (info.local_ip) {
                    html += `<div style="margin-bottom: 6px;"><strong>Local IP:</strong> ${info.local_ip}</div>`;
                }
                if (info.tailscale_ip) {
                    html += `<div style="margin-bottom: 6px;"><strong>Tailscale IP:</strong> ${info.tailscale_ip}</div>`;
                }
                if (!info.local_ip && !info.tailscale_ip) {
                    html = 'No network info available.';
                }
                el.innerHTML = html;
            } catch (err) {
                const el = document.getElementById('network-info-content');
                if (el) el.textContent = 'Could not load network info.';
            }
        })();
