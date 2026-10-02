// Seeded Voronoi geometry is cached; only the broad color field moves each frame.
export function createArtwork({ container, seed, settings }) {
    const canvas = document.createElement('canvas');
    canvas.setAttribute('aria-label', 'Organic Cells generated artwork');
    canvas.style.cssText = 'display:block;width:100%;height:100%';
    container.append(canvas);
    const ctx = canvas.getContext('2d');
    if (!ctx) { canvas.remove(); throw new Error('Canvas 2D unavailable'); }
    let width = 0, height = 0, ratio = 1, cells = [], palette = [], people = [], influences = [];
    const random = () => {
        let state = seed >>> 0;
        return () => {
            state += 0x6D2B79F5;
            let n = Math.imul(state ^ state >>> 15, 1 | state);
            n ^= n + Math.imul(n ^ n >>> 7, 61 | n);
            return ((n ^ n >>> 14) >>> 0) / 4294967296;
        };
    };
    function rebuild() {
        if (!width || !height) return;
        const rng = random();
        const count = settings.cell_density;
        const columns = Math.max(2, Math.round(Math.sqrt(count * width / height)));
        const rows = Math.max(2, Math.ceil(count / columns));
        const points = Array.from({ length: count }, (_, i) => ({
            x: ((i % columns) + 0.15 + rng() * 0.7) / columns * width,
            y: (Math.floor(i / columns) + 0.15 + rng() * 0.7) / rows * height,
            phase: rng() * Math.PI * 2,
        }));
        cells = points.map(p => {
            let polygon = [[0, 0], [width, 0], [width, height], [0, height]];
            for (const q of points) {
                if (p === q) continue;
                const nx = q.x - p.x, ny = q.y - p.y;
                const limit = (q.x * q.x + q.y * q.y - p.x * p.x - p.y * p.y) / 2;
                const clipped = [];
                for (let i = 0; i < polygon.length; i++) {
                    const a = polygon[i], b = polygon[(i + 1) % polygon.length];
                    const da = a[0] * nx + a[1] * ny - limit;
                    const db = b[0] * nx + b[1] * ny - limit;
                    if (da <= 0) clipped.push(a);
                    if ((da <= 0) !== (db <= 0)) {
                        const fraction = da / (da - db);
                        clipped.push([a[0] + fraction * (b[0] - a[0]), a[1] + fraction * (b[1] - a[1])]);
                    }
                }
                polygon = clipped;
                if (!polygon.length) break;
            }
            const path = new Path2D();
            polygon.forEach(([x, y], i) => {
                // Shrink slightly toward the cell center to keep rounded grout joints.
                x = p.x + (x - p.x) * 0.98;
                y = p.y + (y - p.y) * 0.98;
                if (i) path.lineTo(x, y); else path.moveTo(x, y);
            });
            path.closePath();
            return { ...p, path };
        });
    }
    function setSettings(next) {
        const densityChanged = settings.cell_density !== next.cell_density;
        settings = { ...next };
        palette = settings.cell_colors.map(hex => [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16)));
        if (densityChanged) rebuild();
    }
    setSettings(settings);
    return {
        setSettings,
        resize(size) {
            const changed = width !== size.width || height !== size.height;
            width = size.width; height = size.height; ratio = size.pixelRatio;
            canvas.width = Math.max(1, Math.round(width * ratio));
            canvas.height = Math.max(1, Math.round(height * ratio));
            ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
            if (changed || !cells.length) rebuild();
        },
        updateInteractionState(state) { people = state.people || []; },
        render({ elapsedSeconds: time, deltaSeconds: delta }) {
            const positionBlend = 1 - Math.exp(-delta * 8);
            const blend = 1 - Math.exp(-delta * 4);
            const tracked = new Set(people.map(p => p.id));
            for (const p of people) {
                let influence = influences.find(v => v.id === p.id);
                if (!influence) {
                    influence = { ...p, weight: 0 };
                    influences.push(influence);
                }
                influence.x += (p.x - influence.x) * positionBlend;
                influence.y += (p.y - influence.y) * positionBlend;
                influence.speed += (p.speed - influence.speed) * blend;
                influence.weight += (1 - influence.weight) * blend;
            }
            for (const influence of influences) if (!tracked.has(influence.id)) influence.weight *= Math.exp(-delta * 0.7);
            influences = influences.filter(p => p.weight > 0.001 || tracked.has(p.id));
            ctx.fillStyle = settings.grout_color;
            ctx.fillRect(0, 0, width, height);
            ctx.strokeStyle = settings.grout_color;
            ctx.lineWidth = Math.max(2, Math.min(width, height) * 0.003);
            ctx.lineJoin = 'round';
            const drift = time * settings.idle_speed;
            for (const cell of cells) {
                const x = cell.x / width, y = cell.y / height;
                const field = Math.sin(x * 3.1 + drift * 0.43) + Math.cos(y * 3.7 - drift * 0.31)
                    + Math.sin((x + y) * 2.5 + drift * 0.17 + cell.phase * 0.12);
                let position = Math.max(0, Math.min(palette.length - 1, (0.5 + field / 7) * (palette.length - 1)));
                let illumination = 0;
                for (const p of influences) {
                    const distance = Math.hypot(x - (p.x + 1) / 2, (y - 0.5) * 0.6);
                    const reach = Math.exp(-distance * distance * 8) * (1 - p.y * 0.7)
                        * p.weight * settings.interaction_strength;
                    // Sweep through whole palette stops instead of nudging a
                    // compressed color field. Motion adds a broad moving ripple.
                    position += reach * (palette.length - 1)
                        * (1.6 + Math.min(p.speed, 2) * 0.55 * Math.sin(distance * 12 - time * 1.5));
                    illumination += reach * 0.12;
                }
                // Wrap smoothly so strong interaction keeps changing colors
                // rather than clipping a whole region to the last palette stop.
                const wrapped = position % palette.length;
                const index = Math.floor(wrapped), fraction = wrapped - index;
                const first = palette[index], second = palette[(index + 1) % palette.length];
                const light = Math.min(1.1, 0.9 + Math.sin(cell.phase + drift * 0.23) * 0.06 + illumination);
                const rgb = first.map((v, i) => Math.min(255, Math.round((v + (second[i] - v) * fraction) * light)));
                ctx.fillStyle = `rgb(${rgb.join(',')})`;
                ctx.fill(cell.path);
                ctx.stroke(cell.path);
            }
        },
        dispose() { canvas.remove(); cells = []; influences = []; }
    };
}
