// One host owns module lifecycle, animation time, resizing, and interaction.
export class ArtworkHost {
    constructor(container, { simulate = false, onError = () => {} } = {}) {
        this.container = container;
        this.onError = onError;
        this.elapsed = 0;
        this.paused = false;
        this.generation = 0;
        this.people = [];
        this.simulate = simulate;
        this.observer = new ResizeObserver(() => this.resize());
        this.observer.observe(container);
        this.move = event => {
            if (this.paused) return;
            const rect = container.getBoundingClientRect();
            const x = (event.clientX - rect.left) / rect.width * 2 - 1;
            const y = (event.clientY - rect.top) / rect.height;
            const now = performance.now();
            const delta = Math.max(0.016, (now - (this.pointerTime || now)) / 1000);
            const speed = this.people.length ? Math.min(3, Math.hypot(x - this.people[0].x, y - this.people[0].y) / delta) : 0;
            this.people = [{ id: 'preview', x, y, speed }];
            this.pointerTime = now;
        };
        this.leave = () => { if (!this.paused) this.people = []; };
        if (simulate) {
            container.addEventListener('pointermove', this.move);
            container.addEventListener('pointerleave', this.leave);
        }
        this.visibility = () => { this.lastTime = null; };
        document.addEventListener('visibilitychange', this.visibility);
        this.frame = time => {
            if (this.disposed) return;
            this.raf = requestAnimationFrame(this.frame);
            if (!this.module || this.paused || document.hidden || !container.getClientRects().length) {
                this.lastTime = null;
                return;
            }
            if (this.lastTime != null && time - this.lastTime < 1000 / 30) return;
            const delta = this.lastTime == null ? 0 : Math.min(0.1, (time - this.lastTime) / 1000);
            this.lastTime = time;
            this.elapsed += delta;
            try {
                if (simulate && this.pointerTime && time - this.pointerTime > 1200) this.people = [];
                this.module.updateInteractionState({ version: 1, people: this.people, occupancy: this.people.length, activity: 0 });
                this.module.render({ elapsedSeconds: this.elapsed, deltaSeconds: delta });
            } catch (error) { this.fail(error); }
        };
        this.raf = requestAnimationFrame(this.frame);
    }

    resize() {
        if (!this.module) return;
        const { width, height } = this.container.getBoundingClientRect();
        if (!width || !height) return;
        const pixelRatio = Math.min(window.devicePixelRatio || 1, 1.5, Math.sqrt(1920 * 1080 / (width * height)));
        try {
            this.module.resize({ width, height, pixelRatio });
            this.module.render({ elapsedSeconds: this.elapsed, deltaSeconds: 0 });
        } catch (error) { this.fail(error); }
    }

    setPaused(paused) {
        this.paused = paused;
        this.lastTime = null;
    }

    async configure(art, entry) {
        if (this.disposed || this.revision === art.revision) return;
        // Update colors in place, preserving geometry and animation time.
        if (this.module && this.artworkId === art.artwork_id && this.seed === art.seed) {
            this.revision = art.revision;
            try {
                this.module.setSettings(art.settings);
                this.module.render({ elapsedSeconds: this.elapsed, deltaSeconds: 0 });
            } catch (error) { this.fail(error); }
            return;
        }
        const generation = ++this.generation;
        this.clearModule();
        this.revision = art.revision;
        this.artworkId = art.artwork_id;
        this.seed = art.seed;
        this.elapsed = 0;
        this.lastTime = null;
        try {
            if (!entry || !entry.module.startsWith('/static/js/artworks/')) throw new Error('Unknown artwork module');
            const module = await import(entry.module);
            if (this.disposed || generation !== this.generation) return;
            this.module = module.createArtwork({ container: this.container, seed: art.seed, settings: art.settings, quality: { targetFps: 30 } });
            this.resize();
        } catch (error) {
            if (!this.disposed && generation === this.generation) this.fail(error);
        }
    }

    clearModule() {
        const module = this.module;
        this.module = null;
        try { module?.dispose(); } catch (error) { console.error('Artwork cleanup failed', error); }
        this.container.replaceChildren();
    }

    fail(error) {
        this.clearModule();
        this.container.style.background = '#171411';
        console.error(`Artwork ${this.artworkId} failed`, error);
        this.onError(error);
    }

    dispose() {
        this.disposed = true;
        this.generation++;
        cancelAnimationFrame(this.raf);
        this.observer.disconnect();
        document.removeEventListener('visibilitychange', this.visibility);
        this.container.removeEventListener('pointermove', this.move);
        this.container.removeEventListener('pointerleave', this.leave);
        this.clearModule();
    }
}
