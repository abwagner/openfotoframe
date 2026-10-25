const csrfToken = document.querySelector('meta[name="csrf-token"]').content;
let currentUrl = null;
let isPaused = false;
let activeSlot = 'a';
const displayId = document.body.dataset.displayId;
const displayQuery = displayId ? '?display=' + encodeURIComponent(displayId) : '';

const imgA = document.getElementById('slide-a');
const pauseButton = document.getElementById('pause-btn');
const imgB = document.getElementById('slide-b');
const controls = document.querySelector('.controls');
const artSurface = document.getElementById('art-surface');
const photoSurface = document.querySelector('.frame-container');
let contentMode = 'photos';
let artHost = null;
let modeGeneration = 0;
let catalogPromise = null;
let requestSequence = 0;
let appliedSequence = 0;

async function applyState(state, sequence) {
    if (sequence < appliedSequence) return;
    appliedSequence = sequence;
    setPaused(state.paused);
    const mode = state.content_mode || contentMode;
    if (mode !== contentMode) {
        contentMode = mode;
        modeGeneration++;
        artHost?.dispose();
        artHost = null;
        currentUrl = null;
        photoSurface.hidden = mode === 'art';
        artSurface.hidden = mode !== 'art';
        document.getElementById('previous-btn').hidden = mode === 'art';
        document.getElementById('next-btn').hidden = mode === 'art';
    }
    if (mode === 'art') {
        const generation = modeGeneration;
        const stateSequence = appliedSequence;
        try {
            catalogPromise ||= fetch('/api/artworks').then(response => {
                if (!response.ok) throw new Error('Unable to load artwork catalog');
                return response.json();
            }).catch(error => { catalogPromise = null; throw error; });
            const [{ ArtworkHost }, catalog] = await Promise.all([import('/static/js/display-art.js'), catalogPromise]);
            if (generation !== modeGeneration || stateSequence !== appliedSequence) return;
            artHost ||= new ArtworkHost(artSurface, {
                simulate: new URLSearchParams(location.search).get('simulate') === '1'
            });
            artHost.setPaused(state.paused);
            await artHost.configure(state.art, catalog.artworks.find(entry => entry.id === state.art.artwork_id));
        } catch (error) { console.error('Artwork display failed', error); }
        return;
    }
    if (state.mat_color) {
        document.body.style.setProperty('--mat-color', state.mat_color);
        document.body.style.background = state.mat_color;
    }
    if (state.transition_duration != null) {
        document.body.style.setProperty('--transition-duration', state.transition_duration + 's');
    }
    if (state.snapshot_url && state.snapshot_url !== currentUrl) {
        crossfadeTo(state.snapshot_url);
    }
}

function setPaused(paused) {
    isPaused = paused;
    pauseButton.textContent = paused ? '▶ Play' : '⏸ Pause';
    pauseButton.setAttribute('aria-label', paused ? 'Play slideshow' : 'Pause slideshow');
}

async function pollState() {
    const sequence = ++requestSequence;
    try {
        const response = await fetch('/api/display/state' + displayQuery, { cache: 'no-store' });
        if (!response.ok) return;
        await applyState(await response.json(), sequence);
    } catch (e) {
        // network hiccup — retry on next interval
    }
}

function crossfadeTo(url) {
    const generation = modeGeneration;
    const next = activeSlot === 'a' ? imgB : imgA;
    const prev = activeSlot === 'a' ? imgA : imgB;
    next.onload = () => {
        if (contentMode !== 'photos' || generation !== modeGeneration) return;
        currentUrl = url;
        next.classList.add('active');
        prev.classList.remove('active');
        activeSlot = activeSlot === 'a' ? 'b' : 'a';
    };
    next.onerror = () => { /* snapshot not ready yet; keep current slide */ };
    next.src = url;
}

async function sendControl(action) {
    if (contentMode === 'art' && (action === 'next' || action === 'prev')) return;
    const sequence = ++requestSequence;
    try {
        const resp = await fetch('/api/display/control', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
            body: JSON.stringify({ action, display: displayId || undefined }),
        });
        if (!resp.ok) return;
        const state = await resp.json();
        await applyState(state, sequence);
    } catch (e) { /* ignore */ }
}

function nextSlide()     { sendControl('next'); }
function previousSlide() { sendControl('prev'); }
function togglePause()   { sendControl(isPaused ? 'play' : 'pause'); }

function toggleFullscreen() {
    if (!document.fullscreenElement) {
        document.documentElement.requestFullscreen().catch(() => {});
    } else {
        document.exitFullscreen();
    }
}

document.addEventListener('fullscreenchange', () => {
    const btn = document.getElementById('fullscreen-btn');
    if (btn) btn.textContent = document.fullscreenElement ? '⛶ Exit Full' : '⛶ Full';
});

// Show controls on mouse move, hide cursor + controls after 3 s
(function() {
    let hideTimer = null;
    document.body.style.cursor = 'none';
    document.addEventListener('mousemove', () => {
        controls.classList.add('visible');
        document.body.style.cursor = 'default';
        clearTimeout(hideTimer);
        hideTimer = setTimeout(() => {
            controls.classList.remove('visible');
            document.body.style.cursor = 'none';
        }, 3000);
    });
})();

// Keyboard controls
document.addEventListener('keydown', e => {
    switch (e.key) {
        case 'ArrowLeft':  previousSlide(); break;
        case 'ArrowRight':
        case ' ':          nextSlide(); break;
        case 'p':          togglePause(); break;
        case 'f':
        case 'F':          toggleFullscreen(); break;
    }
});

pollState();
setInterval(pollState, 3000);
