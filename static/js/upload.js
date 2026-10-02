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

        previewSection.addEventListener('click', function(e) {
            if (e.target === previewSection) closePreview();
        });

        // Track modifier key for ctrl/cmd+click group selection
        let _modKey = false;
        document.addEventListener('mousedown', function(e) { _modKey = e.ctrlKey || e.metaKey; });

        // Extend ctrl/cmd+click to the whole card (not just the image thumb)
        document.addEventListener('click', function(e) {
            if (!_modKey) return;
            const card = e.target.closest('.image-card');
            if (!card) return;
            if (e.target.closest('.card-thumb')) return; // handleCardClick already handles this
            if (e.target.closest('.card-actions')) return; // don't hijack action buttons
            e.stopPropagation();
            _modKey = false;
            const filename = card.dataset.filename;
            if (!selectMode) startGroupMode();
            toggleSelect(filename);
        }, true);

        // Neutral presets: shown by default (white to medium-brown warm tones)
        const NEUTRAL_PRESETS = [
            {color: '#ffffff', title: 'White'}, {color: '#f5f0e6', title: 'Cream'},
            {color: '#faebd7', title: 'Antique White'}, {color: '#f0ead6', title: 'Eggshell'},
            {color: '#e8dfd5', title: 'Sand'}, {color: '#d6cec5', title: 'Pebble'},
            {color: '#c4a882', title: 'Tan'}, {color: '#b8a088', title: 'Khaki'},
            {color: '#a08870', title: 'Driftwood'}, {color: '#8b7355', title: 'Medium Brown'},
        ];

        // Accent presets: shown when "More Colors" is expanded
        const ACCENT_PRESETS = [
            {color: '#faf9f6', title: 'Soft White'}, {color: '#fffff0', title: 'Ivory'},
            {color: '#faf0e6', title: 'Linen'}, {color: '#e0d8cf', title: 'Oyster'},
            {color: '#d4a574', title: 'Camel'}, {color: '#a9a9a9', title: 'Silver'},
            {color: '#808080', title: 'Gray'}, {color: '#2c2c2c', title: 'Charcoal'},
            {color: '#111111', title: 'Black'}, {color: '#708090', title: 'Slate Gray'},
            {color: '#1a1a2e', title: 'Dark Navy'}, {color: '#3d0c02', title: 'Dark Burgundy'},
            {color: '#1b3a2d', title: 'Forest Green'}, {color: '#4a3728', title: 'Espresso'},
            {color: '#8b0000', title: 'Dark Red'}, {color: '#003366', title: 'Navy Blue'},
            {color: '#2e5339', title: 'Hunter Green'}, {color: '#4b0082', title: 'Indigo'},
            {color: '#704214', title: 'Sepia'}, {color: '#b8860b', title: 'Dark Gold'},
            {color: '#556b2f', title: 'Olive'}, {color: '#800020', title: 'Burgundy'},
            {color: '#c08081', title: 'Dusty Rose'}, {color: '#7b9ea8', title: 'Steel Blue'},
            {color: '#8fbc8f', title: 'Sage'},
        ];

        // Combined for per-image controls
        const COLOR_PRESETS = [...NEUTRAL_PRESETS, ...ACCENT_PRESETS];

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
        const autoMatColorCheckbox = document.getElementById('auto-mat-color');
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
        buildSidebarPresets();

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
            document.querySelectorAll('.settings-modal-body .color-preset').forEach(preset => {
                preset.onclick = () => {
                    matColorInput.value = preset.dataset.color;
                    updateColorPresets();
                    updatePreviewTexture();
                };
            });
        }
        bindSidebarPresets();

        const colorPresets = document.querySelectorAll('.settings-modal-body .color-preset');

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
        let displayProfiles = [];
        let activeDisplayId = null;
        let selectedImages = new Set();
        let currentFilter = 'all';
        let previewFilename = null;
        let previewGroupId = null;

        // ===== Initialize =====
        updatePreviewAspectRatio();
        loadDisplayProfiles().then(loadGallery);

        // ===== Upload Handling =====

        // Page-level drag & drop
        let _dragCounter = 0;
        const pageDragOverlay = document.getElementById('page-drag-overlay');
        document.addEventListener('dragenter', (e) => {
            if (!e.dataTransfer || !e.dataTransfer.types.includes('Files')) return;
            _dragCounter++;
            pageDragOverlay.classList.add('active');
        });
        document.addEventListener('dragleave', () => {
            _dragCounter = Math.max(0, _dragCounter - 1);
            if (_dragCounter === 0) pageDragOverlay.classList.remove('active');
        });
        document.addEventListener('dragover', (e) => {
            if (e.dataTransfer && e.dataTransfer.types.includes('Files')) e.preventDefault();
        });
        document.addEventListener('drop', (e) => {
            _dragCounter = 0;
            pageDragOverlay.classList.remove('active');
            if (!e.dataTransfer || !e.dataTransfer.files.length) return;
            e.preventDefault();
            handleFiles(e.dataTransfer.files);
        });

        fileInput.addEventListener('change', (e) => {
            handleFiles(e.target.files);
        });

        function openSettingsModal() {
            document.getElementById('settings-modal-overlay').classList.add('active');
        }
        function closeSettingsModal() {
            document.getElementById('settings-modal-overlay').classList.remove('active');
        }
        function triggerFileInput() {
            fileInput.click();
        }

        document.getElementById('settings-modal-overlay').addEventListener('click', function(e) {
            if (e.target === this) closeSettingsModal();
        });

        // Pending files for the two-phase upload flow
        let pendingUploadFiles = [];

        async function handleFiles(files) {
            pendingUploadFiles = Array.from(files);
            if (!pendingUploadFiles.length) return;

            // Phase 1: Check for duplicates and dimensions
            showStatus('Checking for duplicates...', 'success');
            let checkResults = null;
            try {
                // Check one image at a time. A mobile camera roll can easily exceed
                // the request-size limit when all selected images are bundled.
                const results = {};
                for (const file of pendingUploadFiles) {
                    const formData = new FormData();
                    formData.append('files', file);
                    const resp = await fetch('/api/check-duplicates', {
                        method: 'POST',
                        body: formData
                    });
                    if (!resp.ok) throw new Error(`Could not check ${file.name}`);
                    const data = await resp.json();
                    Object.assign(results, data.results || {});
                }
                checkResults = { results };
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
            const uploaded = [];
            const errors = [];

            try {
                // Upload independently so a large multi-select does not exceed the
                // server request limit or leave the whole batch stalled.
                for (let index = 0; index < files.length; index++) {
                    const file = files[index];
                    showStatus(`Uploading ${index + 1} of ${files.length}…`, 'success');
                    const formData = new FormData();
                    formData.append('files', file);
                    const response = await fetch('/api/upload', { method: 'POST', body: formData });
                    const contentType = response.headers.get('content-type') || '';
                    const data = contentType.includes('application/json') ? await response.json() : {};
                    if (!response.ok) {
                        errors.push(data.error || (data.errors || []).join(', ') || `${file.name}: upload failed (${response.status})`);
                        continue;
                    }
                    uploaded.push(...(data.uploaded || []));
                    errors.push(...(data.errors || []));
                }
                if (uploaded.length) {
                    showStatus(`Uploaded ${uploaded.length} image(s)`, 'success');
                    await loadDisplayProfiles();
                    await loadGallery();
                }
                if (errors.length) showStatus(errors.join(', '), 'error');
            } catch (err) {
                showStatus('Upload failed: ' + err.message, 'error');
            }

            pendingUploadFiles = [];
            fileInput.value = '';
        }

        // ===== Gallery Loading =====
        async function loadDisplayProfiles() {
            const response = await fetch("/api/displays");
            const data = await response.json();
            displayProfiles = data.displays || [];
            renderDisplayProfiles();
            activeDisplayId = (displayProfiles.find(d => d.active) || displayProfiles[0] || {}).id || null;
        }

        let displayContentEditor = null;
        let displayEditorGeneration = 0;
        function closeDisplayContentEditor() {
            displayEditorGeneration++;
            displayContentEditor?.dispose();
            displayContentEditor = null;
        }
        function renderDisplayProfiles() {
            closeDisplayContentEditor();
            const list = document.getElementById("display-profiles-list");
            if (!list) return;
            const escape = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
            list.innerHTML = displayProfiles.map(d => `<div class="group-edit-item" style="flex-wrap:wrap"><b>${escape(d.name)}</b><span>${d.content.mode === 'art' ? 'Art' : 'Photos'}</span><label>Width <input id="display-width-${escape(d.id)}" type="number" value="${d.width}" style="width:88px"></label><label>Height <input id="display-height-${escape(d.id)}" type="number" value="${d.height}" style="width:88px"></label><button class="btn btn-edit" data-onclick="saveDisplayProfile('${escape(d.id)}')">Save size</button><button class="btn btn-edit" data-content-display="${escape(d.id)}">Edit content</button><button class="btn btn-edit" data-onclick="toggleDisplayProfile('${escape(d.id)}', ${!d.active})">${d.active ? "Deactivate" : "Activate"}</button><a class="btn btn-edit" target="_blank" href="/display?display=${encodeURIComponent(d.id)}">Open</a><button class="btn btn-danger" data-onclick="deleteDisplayProfile('${escape(d.id)}')">Delete</button></div>`).join("");
            list.querySelectorAll('[data-content-display]').forEach(button => button.addEventListener('click', async () => {
                closeDisplayContentEditor();
                const generation = displayEditorGeneration;
                try {
                    const [{ createArtEditor }, response] = await Promise.all([import('/static/js/art-editor.js'), fetch('/api/artworks')]);
                    if (!response.ok) throw new Error('Could not load artworks');
                    const catalog = await response.json();
                    if (generation !== displayEditorGeneration) return;
                    const profile = displayProfiles.find(d => d.id === button.dataset.contentDisplay);
                    displayContentEditor = createArtEditor(document.getElementById('display-content-editor'), profile, catalog, {
                        onClose: closeDisplayContentEditor,
                        onSaved: async () => { await loadDisplayProfiles(); showStatus('Display content updated', 'success'); }
                    });
                } catch (error) { showStatus(error.message, 'error'); }
            }));
        }
        function applyDisplayPreset(value) { if (!value) return; const [width,height]=value.split("x"); document.getElementById("new-display-width").value=width; document.getElementById("new-display-height").value=height; }
        async function saveDisplayProfile(id) { const width=+document.getElementById(`display-width-${id}`).value, height=+document.getElementById(`display-height-${id}`).value; const r=await fetch(`/api/displays/${id}`,{method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify({width,height})}); if(!r.ok) return showStatus("Could not save display size","error"); await loadDisplayProfiles(); showStatus("Display size updated","success"); }
        async function createDisplayProfile() { const b={name:document.getElementById("new-display-name").value,width:+document.getElementById("new-display-width").value,height:+document.getElementById("new-display-height").value,synchronized:document.getElementById("new-display-sync").checked}; const r=await fetch("/api/displays",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(b)}); if (!r.ok) return showStatus("Could not create display","error"); await loadDisplayProfiles(); renderDisplayProfiles(); }
        async function toggleDisplayProfile(id, active) { const r=await fetch(`/api/displays/${id}`,{method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify({active})}); if (!r.ok) return showStatus("At least one display must remain active","error"); await loadDisplayProfiles(); }
        async function deleteDisplayProfile(id) { if (!confirm("Delete this display?")) return; const r=await fetch(`/api/displays/${id}`,{method:"DELETE"}); if (!r.ok) return showStatus("Could not delete display","error"); await loadDisplayProfiles(); }
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
                        <div class="card-actions">
                            <button class="btn ${img.enabled ? 'btn-warning' : 'btn-success'}"
                                    data-onclick="toggleImage('${escAttr(img.filename)}', ${!img.enabled})">
                                ${img.enabled ? '⊘ Hide' : '✓ Show'}
                            </button>
                            <a class="btn btn-edit" href="/uploads/${escAttr(img.filename)}" download="${escAttr(img.filename)}" title="Download">⬇</a>
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

        function renderSinglePreviewImage(filename, forceLocalPreview = false) {
            const img = images.find(i => i.filename === filename);
            if (!img) return;
            const profile = displayProfiles.find(d => d.id === activeDisplayId);
            if (profile && !forceLocalPreview) {
                matPreview.style.aspectRatio = `${profile.width} / ${profile.height}`;
                previewContent.innerHTML = `<img src="/snapshots/${filename}.${profile.id}.display.png?v=${Date.now()}" alt="${filename}" style="width:100%;height:100%;object-fit:contain;display:block;">`;
                return;
            }

            const noMat = !!img.no_mat;
            const matColor = img.mat_color || matColorInput.value;
            const finish = img.mat_finish || matFinishSelect.value;

            matPreview.style.backgroundColor = matColor;
            matPreview.classList.remove('mat-eggshell', 'mat-linen', 'mat-suede', 'mat-silk');
            if (finish !== 'flat') matPreview.classList.add('mat-' + finish);

            const containerW = previewContent.clientWidth;
            const containerH = previewContent.clientHeight;
            const rawRatio = (img.width && img.height) ? img.width / img.height : 4 / 3;
            const crop = img.crop;

            if (noMat) {
                // Cover mode — fill the entire preview container, no mat visible
                let imgHtml;
                if (crop) {
                    const cropRatio = rawRatio * crop.w / crop.h;
                    let dW, dH;
                    if (cropRatio > containerW / containerH) {
                        dH = containerH; dW = containerH * cropRatio;
                    } else {
                        dW = containerW; dH = containerW / cropRatio;
                    }
                    const fullW = Math.round(dW / crop.w);
                    const fullH = Math.round(dH / crop.h);
                    const offsetX = Math.round(-crop.x * fullW);
                    const offsetY = Math.round(-crop.y * fullH);
                    imgHtml = `<div style="width:${containerW}px;height:${containerH}px;overflow:hidden;line-height:0;">` +
                              `<img src="/uploads/${filename}" alt="${filename}" style="width:${fullW}px;height:${fullH}px;margin-left:${offsetX}px;margin-top:${offsetY}px;display:block;max-width:none;max-height:none;">` +
                              `</div>`;
                } else {
                    imgHtml = `<img src="/uploads/${filename}" alt="${filename}" style="width:${containerW}px;height:${containerH}px;object-fit:cover;display:block;">`;
                }
                previewContent.innerHTML = imgHtml;
                return;
            }

            const rawEffectSize = img.bevel_width ?? parseInt(bevelWidthInput.value);
            const effectSize = previewScaledEffect(rawEffectSize);
            const borderEffect = img.border_effect || borderEffectSelect.value;
            const scale = img.scale || 1.0;

            // Compute pixel dimensions — photo+effect must fit in frame
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
            const noMat = !!img.no_mat;
            previewControlsTitle.textContent = cleanFilename(filename);
            document.querySelector('.preview-controls-actions').style.display = 'none';
            const currentFinish = img.mat_finish || '';
            const currentBevel = img.bevel_width ?? '';
            const currentBorderEffect = img.border_effect || '';
            const effectLabel = (currentBorderEffect || borderEffectSelect.value) === 'shadow' ? 'Shadow' : 'Bevel';

            const currentMatColor = img.mat_color || matColorInput.value;
            const profileOptions = displayProfiles.filter(d => d.active).map(d => `<option value="${d.id}" ${d.id === activeDisplayId ? "selected" : ""}>${d.name} (${d.width}×${d.height})</option>`).join("");

            previewControlsBody.innerHTML = `
                <div class="group-edit-item" style="flex-wrap:wrap;gap:8px;"><label>Display</label><select style="flex:1" data-onchange="selectEditorDisplay(this.value, '${escAttr(filename)}')">${profileOptions}</select><button class="btn btn-edit" data-onclick="rotateForDisplay('${escAttr(filename)}', -90)">↺</button><button class="btn btn-edit" data-onclick="rotateForDisplay('${escAttr(filename)}', 90)">↻</button></div>
                <div class="group-edit-item">
                    <div class="group-edit-controls">
                        <label style="font-size:0.75rem;color:#aaa;white-space:nowrap;display:flex;align-items:center;gap:5px;">
                            <input type="checkbox" ${noMat ? 'checked' : ''}
                                   data-onchange="updateImageField('${escAttr(filename)}','no_mat',this.checked); renderSinglePreviewImage('${escAttr(filename)}'); renderSingleControls('${escAttr(filename)}')">
                            No mat
                        </label>
                        <label style="font-size:0.75rem;color:#aaa;white-space:nowrap;">Zoom</label>
                        <input type="range" min="${noMat ? '1' : '0.25'}" max="3" step="0.05"
                               value="${Math.max(noMat ? 1.0 : 0.25, scale)}"
                               data-oninput="updateSingleScalePreview('${escAttr(filename)}', this.value); this.nextElementSibling.textContent = parseFloat(this.value).toFixed(2) + 'x'"
                               data-onchange="updateSingleScale('${escAttr(filename)}', this.value)">
                        <span class="scale-value">${parseFloat(Math.max(noMat ? 1.0 : 0.25, scale)).toFixed(2)}x</span>
                    </div>
                </div>
                <div class="group-edit-item" style="flex-direction:column;gap:8px;">
                    <div style="display:flex;align-items:center;gap:8px;">
                        <label style="font-size:0.8rem;color:#aaa;white-space:nowrap;">Mat Color:</label>
                        <button style="font-size:0.7rem;padding:2px 6px;border:1px solid rgba(255,255,255,0.1);border-radius:4px;background:rgba(0,0,0,0.3);color:#aaa;cursor:pointer;"
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
                        <label style="font-size:0.8rem;color:#aaa;white-space:nowrap;">Finish:</label>
                        <select style="flex:1;padding:6px;border:1px solid rgba(255,255,255,0.1);border-radius:6px;background:rgba(0,0,0,0.3);color:#e0e0e0;font-size:0.8rem;"
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
                        <label style="font-size:0.8rem;color:#aaa;white-space:nowrap;">Effect:</label>
                        <select style="flex:1;padding:6px;border:1px solid rgba(255,255,255,0.1);border-radius:6px;background:rgba(0,0,0,0.3);color:#e0e0e0;font-size:0.8rem;"
                                data-onchange="updateImageField('${escAttr(filename)}','border_effect',this.value||null); renderSingleControls('${escAttr(filename)}')">
                            <option value="" ${!currentBorderEffect ? 'selected' : ''}>Default</option>
                            <option value="bevel" ${currentBorderEffect==='bevel' ? 'selected' : ''}>Bevel (classic)</option>
                            <option value="bevel-lit" ${currentBorderEffect==='bevel-lit' ? 'selected' : ''}>Bevel (lit)</option>
                            <option value="shadow" ${currentBorderEffect==='shadow' ? 'selected' : ''}>Shadow</option>
                        </select>
                    </div>
                </div>
                <div class="group-edit-item" style="flex-wrap:wrap;gap:12px;">
                    <div style="display:flex;align-items:center;gap:8px;flex:1;min-width:150px;">
                        <label style="font-size:0.8rem;color:#aaa;white-space:nowrap;">${effectLabel}: <span id="single-bevel-val">${currentBevel !== '' ? currentBevel + 'px' : parseInt(bevelWidthInput.value) + 'px'}</span></label>
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
                    <a class="btn btn-edit" href="/uploads/${escAttr(filename)}" download="${escAttr(filename)}">⬇ Download</a>
                    <button class="btn btn-danger" data-onclick="deleteImage('${escAttr(filename)}')">🗑 Delete</button>
                </div>
            `;
        }

        // ===== Crop Mode =====
        let cropState = null; // {filename, x, y, w, h, imgRect, imgWidth, imgHeight}
        let cropAspectLocked = false;
        let cropAspectRatio = '16:9';
        let cropZoom = 1.0;

        function enterCropMode(filename) {
            const img = images.find(i => i.filename === filename);
            if (!img) return;

            const existing = img.crop || {x: 0, y: 0, w: 1, h: 1};
            cropState = {
                filename,
                x: existing.x, y: existing.y, w: existing.w, h: existing.h,
                imgWidth: img.width || 1, imgHeight: img.height || 1,
            };
            previewSection.classList.add('crop-editing');

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

            // Portrait display: toolbar on the right side; landscape: bottom center
            const isPortraitDisplay = dispH > dispW;
            const toolbarStyle = isPortraitDisplay
                ? 'left:auto;right:12px;top:50%;bottom:auto;transform:translateY(-50%);flex-direction:column;align-items:stretch;'
                : '';

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
                <div class="crop-toolbar" ${toolbarStyle ? `style="${toolbarStyle}"` : ''}>
                    <label class="crop-aspect-toggle">
                        <input type="checkbox" id="crop-lock-aspect" data-onchange="updateCropAspectLock(this.checked)" ${cropAspectLocked ? 'checked' : ''}>
                        Lock ratio
                    </label>
                    <select id="crop-aspect-select" data-onchange="updateCropAspectRatio(this.value)" style="display:${cropAspectLocked ? 'inline-block' : 'none'}">
                        <option value="1:1" ${cropAspectRatio==='1:1' ? 'selected' : ''}>1:1</option>
                        <option value="4:3" ${cropAspectRatio==='4:3' ? 'selected' : ''}>4:3</option>
                        <option value="3:2" ${cropAspectRatio==='3:2' ? 'selected' : ''}>3:2</option>
                        <option value="16:9" ${cropAspectRatio==='16:9' ? 'selected' : ''}>16:9</option>
                        <option value="9:16" ${cropAspectRatio==='9:16' ? 'selected' : ''}>9:16</option>
                        <option value="2:3" ${cropAspectRatio==='2:3' ? 'selected' : ''}>2:3</option>
                        <option value="3:4" ${cropAspectRatio==='3:4' ? 'selected' : ''}>3:4</option>
                    </select>
                    <label class="crop-aspect-toggle" style="margin-left:8px;">
                        🔍 <input type="range" id="crop-zoom-slider" min="1" max="4" step="0.1"
                               value="${cropZoom}"
                               style="width:70px;accent-color:#00d4ff;vertical-align:middle;"
                               data-oninput="updateCropPreviewZoom(this.value)">
                    </label>
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

                    if (cropAspectLocked) {
                        const [arw, arh] = cropAspectRatio.split(':').map(Number);
                        const iw = cropState.imgWidth, ih = cropState.imgHeight;
                        // normRatio = nw/nh when pixel aspect = arw:arh
                        const normRatio = (arw * ih) / (arh * iw);
                        if (dragMode.includes('e') || dragMode.includes('w')) {
                            nh = Math.max(minSize, nw / normRatio);
                            nw = nh * normRatio;
                            if (dragMode.includes('n')) ny = startCrop.y + startCrop.h - nh;
                        } else {
                            nw = Math.max(minSize, nh * normRatio);
                            nh = nw / normRatio;
                            nx = startCrop.x + (startCrop.w - nw) / 2;
                        }
                        nx = Math.max(0, Math.min(1 - nw, nx));
                        ny = Math.max(0, Math.min(1 - nh, ny));
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
            const previousCrop = img ? img.crop : null;
            if (img) img.crop = saveCrop;

            // Crop is image metadata shared by every display, not a per-display override.
            const saved = await updateImageField(fn, 'crop', saveCrop);
            if (!saved) {
                if (img) img.crop = previousCrop;
                showStatus('Could not save crop. Please try again.', 'error');
                return;
            }
            previewSection.classList.remove('crop-editing');
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
            previewSection.classList.remove('crop-editing');
            if (groupId) {
                renderGroupPreviewImage(groupId);
                renderGroupControls(groupId);
            } else {
                renderSinglePreviewImage(fn);
                renderSingleControls(fn);
            }
        }

        function updateCropAspectLock(checked) {
            cropAspectLocked = checked;
            const sel = document.getElementById('crop-aspect-select');
            if (sel) sel.style.display = checked ? 'inline-block' : 'none';
            if (checked && cropState) {
                const [arw, arh] = cropAspectRatio.split(':').map(Number);
                const iw = cropState.imgWidth, ih = cropState.imgHeight;
                const normRatio = (arw * ih) / (arh * iw);
                let nh = cropState.w / normRatio;
                if (cropState.y + nh > 1) {
                    nh = 1 - cropState.y;
                    cropState.w = Math.min(1, nh * normRatio);
                }
                cropState.h = Math.max(0.05, nh);
                updateCropSelection();
            }
        }

        function updateCropAspectRatio(value) {
            cropAspectRatio = value;
            if (cropAspectLocked && cropState) {
                updateCropAspectLock(true);
            }
        }

        function updateCropPreviewZoom(value) {
            cropZoom = parseFloat(value) || 1.0;
            if (!cropState) return;
            const img = images.find(i => i.filename === cropState.filename);
            if (!img) return;
            const containerW = previewContent.clientWidth;
            const containerH = previewContent.clientHeight;
            const imgRatio = (img.width || 1) / (img.height || 1);
            let baseW, baseH;
            if (imgRatio > containerW / containerH) {
                baseW = containerW * 0.92;
                baseH = baseW / imgRatio;
            } else {
                baseH = containerH * 0.92;
                baseW = baseH * imgRatio;
            }
            const dispW = baseW * cropZoom;
            const dispH = baseH * cropZoom;
            // Center view on the crop selection
            const selCX = (cropState.x + cropState.w / 2) * dispW;
            const selCY = (cropState.y + cropState.h / 2) * dispH;
            const offsetX = Math.min(0, Math.max(containerW - dispW, containerW / 2 - selCX));
            const offsetY = Math.min(0, Math.max(containerH - dispH, containerH / 2 - selCY));
            cropState.imgRect = {x: offsetX, y: offsetY, w: dispW, h: dispH};
            const imgEl = document.getElementById('crop-image');
            if (imgEl) {
                imgEl.style.left = offsetX + 'px';
                imgEl.style.top = offsetY + 'px';
                imgEl.style.width = dispW + 'px';
                imgEl.style.height = dispH + 'px';
            }
            updateCropSelection();
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
                        <label style="font-size:0.8rem;color:#aaa;white-space:nowrap;">Finish:</label>
                        <select style="flex:1;padding:6px;border:1px solid rgba(255,255,255,0.1);border-radius:6px;background:rgba(0,0,0,0.3);color:#e0e0e0;font-size:0.8rem;"
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
                        <label style="font-size:0.8rem;color:#aaa;white-space:nowrap;">Effect:</label>
                        <select style="flex:1;padding:6px;border:1px solid rgba(255,255,255,0.1);border-radius:6px;background:rgba(0,0,0,0.3);color:#e0e0e0;font-size:0.8rem;"
                                data-onchange="updateGroupField('${groupId}','border_effect',this.value||null); renderGroupControls('${groupId}')">
                            <option value="" ${!currentBorderEffect ? 'selected' : ''}>Default</option>
                            <option value="bevel" ${currentBorderEffect==='bevel' ? 'selected' : ''}>Bevel (classic)</option>
                            <option value="bevel-lit" ${currentBorderEffect==='bevel-lit' ? 'selected' : ''}>Bevel (lit)</option>
                            <option value="shadow" ${currentBorderEffect==='shadow' ? 'selected' : ''}>Shadow</option>
                        </select>
                    </div>
                    <div style="display:flex;align-items:center;gap:8px;flex:1;min-width:150px;">
                        <label style="font-size:0.8rem;color:#aaa;white-space:nowrap;">${effectLabel}: <span id="group-bevel-val">${currentBevel !== '' ? currentBevel + 'px' : parseInt(bevelWidthInput.value) + 'px'}</span></label>
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
                        <a class="btn btn-edit" href="/uploads/${escAttr(img.filename)}" download="${escAttr(img.filename)}" style="font-size:0.75rem;padding:4px 8px;">⬇</a>
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
            if (cropState?._cleanup) cropState._cleanup();
            cropState = null;
            previewSection.classList.remove('crop-editing');
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
            if (_modKey) {
                _modKey = false;
                if (!selectMode) startGroupMode();
                toggleSelect(filename);
                return;
            }
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
                    loadDisplayProfiles().then(loadGallery);
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
                    loadDisplayProfiles().then(loadGallery);
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
                loadDisplayProfiles().then(loadGallery);
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
            // The normal profile preview is a server snapshot, so use the local
            // renderer while dragging to make both smaller and larger zoom visible.
            renderSinglePreviewImage(filename, true);
        }

        async function updateSingleScale(filename, scaleValue) {
            const img = images.find(i => i.filename === filename);
            if (!img) return;
            const scale = parseFloat(scaleValue);
            const saved = await updateImageField(filename, 'scale', scale);
            if (saved) renderSinglePreviewImage(filename);
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

        async function resetAllMatColors() {
            if (!confirm('Reset all mat colors to the default? This clears every per-image color — it cannot be undone.')) return;
            const btn = document.getElementById('reset-mat-colors');
            btn.disabled = true;
            btn.textContent = 'Resetting…';
            try {
                await fetch('/api/reset-mat-colors', { method: 'POST' });
                btn.textContent = '✓ Reset';
                setTimeout(() => {
                    btn.textContent = 'Reset all to default';
                    btn.disabled = false;
                }, 1500);
                loadDisplayProfiles().then(loadGallery);
            } catch {
                btn.textContent = 'Reset all to default';
                btn.disabled = false;
            }
        }

        function applyMatSettings() {
            const { intensity, v, h } = getLitSliderValues();
            const settings = {
                mat_color: matColorInput.value,
                mat_finish: matFinishSelect.value,
                bevel_width: parseInt(bevelWidthInput.value),
                border_effect: borderEffectSelect.value,
                auto_mat_color: autoMatColorCheckbox.checked,
                bevel_lit_intensity: intensity,
                bevel_lit_v: v,
                bevel_lit_h: h,
            };

            const applyBtn = document.getElementById('apply-mat-settings');
            fetch('/api/settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(settings)
            }).then(() => {
                applyBtn.textContent = '✓ Applied';
                applyBtn.classList.add('confirmed');
                setTimeout(() => {
                    applyBtn.textContent = 'Apply';
                    applyBtn.classList.remove('confirmed');
                }, 1500);
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
            document.getElementById('bevel-lit-controls').style.display =
                borderEffectSelect.value === 'bevel-lit' ? '' : 'none';
        });
        bevelWidthInput.addEventListener('input', () => {
            bevelValueLabel.textContent = bevelWidthInput.value + 'px';
        });
        document.getElementById('bevel-lit-intensity').addEventListener('input', function() {
            document.getElementById('bevel-lit-intensity-value').textContent = this.value + '%';
            updatePreviewTexture();
        });

        // Light direction circular picker
        (function initLightPicker() {
            const picker = document.getElementById('light-picker');
            const handle = document.getElementById('light-handle');
            const vInput = document.getElementById('bevel-lit-v');
            const hInput = document.getElementById('bevel-lit-h');
            if (!picker) return;

            function setHandleFromVH(v, h) {
                // map 0-100 → -1..1, clamped to 85% of radius so dot stays inside
                const nx = ((h / 100) * 2 - 1) * 0.85;
                const ny = ((v / 100) * 2 - 1) * 0.85;
                const len = Math.sqrt(nx * nx + ny * ny);
                const scale = len > 0.85 ? 0.85 / len : 1;
                handle.style.left = ((nx * scale / 0.85 + 1) / 2 * 100) + '%';
                handle.style.top  = ((ny * scale / 0.85 + 1) / 2 * 100) + '%';
            }

            function updateFromClientXY(clientX, clientY, save) {
                const rect = picker.getBoundingClientRect();
                const cx = rect.left + rect.width  / 2;
                const cy = rect.top  + rect.height / 2;
                const r  = rect.width / 2;
                let nx = (clientX - cx) / r;
                let ny = (clientY - cy) / r;
                const len = Math.sqrt(nx * nx + ny * ny);
                if (len > 0.85) { nx = nx / len * 0.85; ny = ny / len * 0.85; }
                handle.style.left = ((nx / 0.85 + 1) / 2 * 100) + '%';
                handle.style.top  = ((ny / 0.85 + 1) / 2 * 100) + '%';
                hInput.value = Math.round((nx / 0.85 + 1) / 2 * 100);
                vInput.value = Math.round((ny / 0.85 + 1) / 2 * 100);
                if (save) applyMatSettings(); else updatePreviewTexture();
            }

            // Init from saved values
            setHandleFromVH(parseInt(vInput.value || 15), parseInt(hInput.value || 15));

            let dragging = false;
            picker.addEventListener('mousedown', e => {
                dragging = true;
                updateFromClientXY(e.clientX, e.clientY, false);
                e.preventDefault();
            });
            picker.addEventListener('touchstart', e => {
                dragging = true;
                updateFromClientXY(e.touches[0].clientX, e.touches[0].clientY, false);
            }, { passive: true });
            document.addEventListener('mousemove', e => {
                if (dragging) updateFromClientXY(e.clientX, e.clientY, false);
            });
            document.addEventListener('touchmove', e => {
                if (dragging) updateFromClientXY(e.touches[0].clientX, e.touches[0].clientY, false);
            });
            document.addEventListener('mouseup',  () => { if (dragging) { dragging = false; applyMatSettings(); } });
            document.addEventListener('touchend', () => { if (dragging) { dragging = false; applyMatSettings(); } });
        })();

        // Slideshow settings: Apply button
        document.getElementById('apply-slideshow-settings').addEventListener('click', saveSlideshowSettings);
        targetAspectRatioSelect.addEventListener('change', updatePreviewAspectRatio);

        function updateColorPresets() {
            document.querySelectorAll('.settings-modal-body .color-preset').forEach(preset => {
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
                const response = await fetch(`/api/gallery/${encodeURIComponent(filename)}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ [field]: value })
                });
                if (!response.ok) throw new Error(`Request failed (${response.status})`);
                // Re-render preview
                if (previewFilename === filename) renderSinglePreviewImage(filename);
                showStatus(`${field.replace('_', ' ')} updated`, 'success');
                return true;
            } catch (err) {
                showStatus(`Failed to update ${field}`, 'error');
                return false;
            }
        }

        function selectEditorDisplay(id, filename) {
            activeDisplayId = id;
            renderSinglePreviewImage(filename);
            renderSingleControls(filename);
        }
        async function updateDisplayOverride(filename, updates) {
            if (!activeDisplayId) return;
            const img = images.find(i => i.filename === filename);
            if (img) { img.display_overrides ||= {}; img.display_overrides[activeDisplayId] = { ...(img.display_overrides[activeDisplayId] || {}), ...updates }; }
            const response = await fetch(`/api/gallery//displays/`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(updates) });
            if (!response.ok) throw new Error("Could not save display override");
            renderSinglePreviewImage(filename);
        }
        async function rotateForDisplay(filename, delta) {
            const img = images.find(i => i.filename === filename);
            const current = ((img?.display_overrides || {})[activeDisplayId] || {}).rotation || 0;
            await updateDisplayOverride(filename, { rotation: (current + delta + 360) % 360 });
            renderSingleControls(filename);
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

        function getBevelColorsLit(intensity, v, h) {
            const i = intensity / 100;
            // vf/hf: positive = lit side is at top/left; light source direction is opposite
            const vf = (v - 50) / 50;
            const hf = (h - 50) / 50;
            function edgeColor(factor) {
                if (factor > 0) {
                    const a = +(factor * i * 0.55).toFixed(3);
                    return a < 0.005 ? 'transparent' : `rgba(255,255,255,${a})`;
                } else {
                    // shadow range: 24% at brightness=0 → 4% at brightness=100.
                    // The darker bezel base supplies the rest of the separation.
                    const a = +((1 - factor) / 2 * (0.04 + 0.20 * (1 - i))).toFixed(3);
                    return a < 0.005 ? 'transparent' : `rgba(0,0,0,${a})`;
                }
            }
            // Corner colors blend the two adjacent face factors at each 45° corner
            return {
                tl: edgeColor((vf + hf) / 2),
                tr: edgeColor((vf - hf) / 2),
                bl: edgeColor((hf - vf) / 2),
                br: edgeColor(-(vf + hf) / 2),
            };
        }

        function getLitSliderValues() {
            return {
                intensity: parseInt(document.getElementById('bevel-lit-intensity')?.value ?? 50),
                v:         parseInt(document.getElementById('bevel-lit-v')?.value ?? 15),
                h:         parseInt(document.getElementById('bevel-lit-h')?.value ?? 15),
            };
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

        function makeBevelLitStripHtml(bevelWidth) {
            const w = bevelWidth + 'px';
            const { intensity, v, h } = getLitSliderValues();
            const { tl, tr, bl, br } = getBevelColorsLit(intensity, v, h);
            // Each strip: background gradient runs ALONG the strip (corner-to-corner);
            // mask gradient runs ACROSS the strip (outer opaque → inner transparent) for depth.
            const strips = [
                { pos: `top:0;left:0;right:0;height:${w}`,    clip: `polygon(0 0,100% 0,calc(100% - ${w}) 100%,${w} 100%)`,  bg: `to right,${tl},${tr}`,  mask: 'to bottom' },
                { pos: `bottom:0;left:0;right:0;height:${w}`, clip: `polygon(${w} 0,calc(100% - ${w}) 0,100% 100%,0 100%)`,  bg: `to right,${bl},${br}`,  mask: 'to top'    },
                { pos: `top:0;left:0;bottom:0;width:${w}`,    clip: `polygon(0 0,100% ${w},100% calc(100% - ${w}),0 100%)`,  bg: `to bottom,${tl},${bl}`, mask: 'to right'  },
                { pos: `top:0;right:0;bottom:0;width:${w}`,   clip: `polygon(0 ${w},100% 0,100% 100%,0 calc(100% - ${w}))`,  bg: `to bottom,${tr},${br}`, mask: 'to left'   },
            ];
            const stripHtml = strips.map(({ pos, clip, bg, mask }) => {
                const msk = `linear-gradient(${mask},black,transparent)`;
                // Keep the faces clean. A shadow on every clipped strip makes the
                // joins read as dark seams instead of one continuous bevel.
                return `<div style="position:absolute;${pos};clip-path:${clip};background:linear-gradient(${bg});-webkit-mask-image:${msk};mask-image:${msk};pointer-events:none;z-index:1;"></div>`;
            }).join('');
            // A single restrained contact shadow gives the mat depth without
            // outlining each face or muddying the corner joints.
            const accent = `<div style="position:absolute;inset:${w};box-shadow:0 0 0 1px rgba(0,0,0,0.16),inset 0 1px 2px rgba(0,0,0,0.12);pointer-events:none;z-index:3;"></div>`;
            // 45° corner cut lines — one per corner, same weight as inner accent
            const d = Math.round(bevelWidth * Math.SQRT2) + 'px';
            const cornerLines = [
                `left:0;top:0;transform-origin:0 50%;transform:rotate(45deg)`,
                `right:0;top:0;transform-origin:100% 50%;transform:rotate(-45deg)`,
                `left:0;bottom:0;transform-origin:0 50%;transform:rotate(-45deg)`,
                `right:0;bottom:0;transform-origin:100% 50%;transform:rotate(45deg)`,
            ].map(pos =>
                `<div style="position:absolute;${pos};width:${d};height:1px;background:rgba(0,0,0,0.12);pointer-events:none;z-index:4;"></div>`
            ).join('');
            return stripHtml + accent + cornerLines;
        }

        function getShadowStyle(size) {
            const blur = size * 2;
            const spread = Math.round(size * 0.5);
            const yOffset = Math.round(size * 0.5);
            return `0 ${yOffset}px ${blur}px ${spread}px rgba(0,0,0,0.35)`;
        }

        function makeBevelHtml(innerHtml, bevelWidth, matColor, bevelStyle) {
            if (!bevelWidth || bevelWidth <= 0) return innerHtml;
            const bw = Math.round(bevelWidth);
            if (bevelStyle === 'bevel-lit') {
                return `<div class="mat-bevel" data-bevel-lit="${bw}" style="--bevel-w:${bw}px;--bevel-mat:${matColor}">${innerHtml}${makeBevelLitStripHtml(bw)}</div>`;
            }
            const bevelColors = getBevelColors(matColor);
            return `<div class="mat-bevel" style="--bevel-w:${bw}px;--bevel-mat:${matColor};--bevel-outer:${bevelColors.outer};--bevel-inner:${bevelColors.inner}">${innerHtml}${makeBevelStripHtml(bw)}</div>`;
        }

        function previewScaledEffect(effectSize) {
            const pw = previewContent.clientWidth || matPreview.clientWidth || 800;
            return Math.max(1, Math.round(effectSize * pw / 1920));
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
            return makeBevelHtml(innerHtml, effectSize, matColor, borderEffect);
        }

        function updatePreviewTexture() {
            const finish = matFinishSelect.value;
            matPreview.classList.remove('mat-eggshell', 'mat-linen', 'mat-suede', 'mat-silk');
            if (finish !== 'flat') {
                matPreview.classList.add('mat-' + finish);
            }
            // Rebuild bevel-lit strips live (picker drag, intensity slider)
            document.querySelectorAll('[data-bevel-lit]').forEach(bevelDiv => {
                const bw = parseInt(bevelDiv.dataset.bevelLit);
                while (bevelDiv.children.length > 1) bevelDiv.removeChild(bevelDiv.lastChild);
                bevelDiv.insertAdjacentHTML('beforeend', makeBevelLitStripHtml(bw));
            });
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
