import {ArtworkHost} from '/static/js/display-art.js';
const frame = document.body.dataset.frameId;
const physical = document.body.dataset.physical === 'true';
const query = '?display=' + encodeURIComponent(frame);
const image = document.getElementById('current-slide');
const message = document.getElementById('display-message');
const controls = document.querySelector('.controls');
const csrf = document.querySelector('meta[name="csrf-token"]').content;
const artSurface = document.getElementById('art-surface');
let artHost = null, current = null, lastSlide = '', loaded = null, advance = null, active = true, busy = false, hide = null;

async function read(response) {
    const body = await response.json();
    if (!response.ok) throw new Error(body.error?.message || 'Cannot connect to this frame.');
    return body;
}
async function control(action) {
    await read(await fetch('/frame/control' + query, {method:'POST',headers:{'Content-Type':'application/json','X-CSRFToken':csrf},body:JSON.stringify({action})}));
    await refresh();
}
function stopAdvance() { if (advance) clearTimeout(advance); advance = null; }
function scheduleAdvance(data) {
    stopAdvance();
    if (!physical || data.art || data.state.paused || data.slides.length < 2) return;
    const seconds = Number(data.settings.slideshow_interval ?? data.settings.interval ?? 60);
    advance = setTimeout(() => control('next').catch(showFailure), Math.max(1, Math.min(86400, seconds)) * 1000);
}
function showFailure(error) {
    stopAdvance(); image.removeAttribute('src'); lastSlide = '';
    if (image.dataset.blob) { URL.revokeObjectURL(image.dataset.blob); delete image.dataset.blob; }
    artHost?.dispose(); artHost = null; artSurface.hidden = true;
    message.textContent = error.message || 'Cannot connect to this frame.';
}
async function refresh() {
    if (busy || !active) return;
    busy = true;
    try {
        const data = await read(await fetch('/frame/playlist' + query));
        if (!active) return;
        current = data;
        document.getElementById('pause-btn').textContent = data.state.paused ? '▶ Play' : '⏸ Pause';
        if (data.art) {
            image.removeAttribute('src'); artSurface.hidden = false;
            artHost ||= new ArtworkHost(artSurface, {onError:showFailure});
            await artHost.configure(data.art, data.artwork);
            artHost.setPaused(data.state.paused);
            message.textContent = ''; stopAdvance();
        } else {
            artHost?.dispose(); artHost = null; artSurface.hidden = true;
            if (!data.slides.length || data.settings.active === false) { image.removeAttribute('src'); if(image.dataset.blob){URL.revokeObjectURL(image.dataset.blob);delete image.dataset.blob;} message.textContent = data.settings.active === false ? 'This frame is inactive.' : 'No photos are assigned to this frame.'; stopAdvance(); return; }
            const index = ((data.state.index || 0) % data.slides.length + data.slides.length) % data.slides.length;
            const slide = data.slides[index];
            const key = slide.snapshot_url;
            if (key !== lastSlide) {
                const revision = key; lastSlide = key;
                loaded?.abort(); loaded = new AbortController();
                const response = await fetch(slide.snapshot_url, {signal:loaded.signal});
                if (!response.ok) throw new Error('This photo is no longer available.');
                const blob = await response.blob();
                if (!active || lastSlide !== revision) return;
                const url = URL.createObjectURL(blob), previous = image.dataset.blob;
                image.src = url; image.dataset.blob = url;
                if (previous) URL.revokeObjectURL(previous);
                message.textContent = ''; scheduleAdvance(data);
            } else if (data.state.paused) stopAdvance();
            else if (!advance) scheduleAdvance(data);
        }
    } catch (error) { if (error.name !== 'AbortError') showFailure(error); }
    finally { busy = false; }
}
document.getElementById('previous-btn').addEventListener('click', () => control('previous').catch(showFailure));
document.getElementById('next-btn').addEventListener('click', () => control('next').catch(showFailure));
document.getElementById('pause-btn').addEventListener('click', () => control(current?.state.paused ? 'play' : 'pause').catch(showFailure));
document.getElementById('fullscreen-btn').addEventListener('click', () => window.enterFullscreen());
document.getElementById('exit-fullscreen-btn').addEventListener('click', () => window.exitFullscreen());
function showControls() { controls.classList.add('visible'); clearTimeout(hide); hide = setTimeout(() => controls.classList.remove('visible'), 4000); }
document.addEventListener('mousemove', showControls); document.addEventListener('touchstart', showControls, {passive:true});
document.addEventListener('keydown', event => { if (event.key.toLowerCase() === 'f') window.toggleFullscreen(); });
window.addEventListener('pagehide', () => { active=false; stopAdvance(); loaded?.abort(); artHost?.dispose(); clearInterval(poll); if(image.dataset.blob) URL.revokeObjectURL(image.dataset.blob); });
const poll = setInterval(refresh, 3000);
refresh();
