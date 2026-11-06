// Entry is idempotent so a scheduled pointer click cannot exit restored fullscreen.
async function enterFullscreen() {
    if (document.fullscreenElement) return;
    try {
        await document.documentElement.requestFullscreen();
    } catch (_) { /* Fullscreen may be unsupported or require a user gesture. */ }
}

async function exitFullscreen() {
    if (!document.fullscreenElement) return;
    try {
        await document.exitFullscreen();
    } catch (_) { /* The browser may already have left fullscreen. */ }
}

// Preserve the deliberate F-key shortcut for people controlling the display.
function toggleFullscreen() {
    return document.fullscreenElement ? exitFullscreen() : enterFullscreen();
}

function syncFullscreenControls() {
    const active = Boolean(document.fullscreenElement);
    const enter = document.getElementById('fullscreen-btn');
    const exit = document.getElementById('exit-fullscreen-btn');
    if (enter) {
        // Keep the entry button visible in its calibrated slot, but inactive.
        enter.disabled = active;
    }
    if (exit) exit.hidden = !active;
}

document.addEventListener('fullscreenchange', syncFullscreenControls);
window.addEventListener('pageshow', syncFullscreenControls);
syncFullscreenControls();
