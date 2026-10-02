// Seeded, bounded geometry. Density sets typical size; color changes never
// regenerate the layout. Regular tilings keep their shape; only edge tiles are clipped.
export function patternedCells(width, height, settings, rng) {
    const cells = [];
    const count = settings.cell_density;
    const unit = Math.sqrt(width * height / count);
    function polygon(vertices) {
        const x = vertices.reduce((sum, p) => sum + p[0], 0) / vertices.length;
        const y = vertices.reduce((sum, p) => sum + p[1], 0) / vertices.length;
        const path = new Path2D();
        vertices.forEach(([vx, vy], i) => {
            if (i) path.lineTo(vx, vy); else path.moveTo(vx, vy);
        });
        path.closePath();
        cells.push({ x, y, vertices, path, phase: rng() * Math.PI * 2 });
    }
    function circle(x, y, radius) {
        const path = new Path2D(); path.arc(x, y, radius, 0, Math.PI * 2);
        cells.push({ x, y, radius, path, phase: rng() * Math.PI * 2 });
    }
    function clippedPolygon(vertices) {
        // Intersect a genuine tile with the viewport without warping its interior.
        for (const [axis, limit, sign] of [[0,0,-1],[0,width,1],[1,0,-1],[1,height,1]]) {
            const clipped = [];
            vertices.forEach((a, i) => {
                const b = vertices[(i + 1) % vertices.length];
                const da = sign * (a[axis] - limit), db = sign * (b[axis] - limit);
                if (da <= 0) clipped.push(a);
                if ((da <= 0) !== (db <= 0)) {
                    const t = da / (da - db);
                    clipped.push([a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])]);
                }
            });
            vertices = clipped;
        }
        if (vertices.length >= 3) polygon(vertices);
    }
    if (settings.shape === 'circles') {
        const gap = Math.max(2, Math.min(width, height) * 0.004);
        const maxRadius = unit * 0.7;
        function place(limit, minimum, attempts) {
            let best = null;
            for (let attempt = 0; attempt < attempts; attempt++) {
                const x = rng() * width, y = rng() * height;
                let radius = Math.min(x, y, width - x, height - y) - gap;
                for (const cell of cells) {
                    radius = Math.min(radius, Math.hypot(x - cell.x, y - cell.y) - cell.radius - gap);
                    if (radius < minimum) break;
                }
                radius = Math.min(radius, limit);
                if (radius >= minimum && (!best || radius > best.radius)) best = {x, y, radius};
            }
            if (best) circle(best.x, best.y, best.radius);
            return Boolean(best);
        }
        // Largest-free-space sampling avoids a visible grid while keeping every
        // circle whole. The base layout is independent of the gap-fill setting.
        for (let i = 0; i < count; i++) {
            place(maxRadius * (0.5 + rng() * 0.5), unit * 0.1, 24);
        }
        const target = Math.round(count * 3 * (settings.circle_fill ?? 0.5));
        // One unlucky candidate batch must not stop filling all remaining gaps.
        let added = 0, misses = 0;
        for (let i = 0; i < count * 12 && added < target && misses < 16; i++) {
            if (place(maxRadius * 0.4, Math.max(gap, unit * 0.025), 32)) { added++; misses = 0; }
            else misses++;
        }
    } else if (settings.shape === 'rectangles') {
        const aspect = Math.pow(4, 1 - (settings.rectangle_squareness ?? 0.5));
        const tiles = [{x: 0, y: 0, w: width, h: height}];
        while (tiles.length < count) {
            let largest = 0, score = 0;
            tiles.forEach((tile, i) => {
                const candidate = tile.w * tile.h * (0.75 + rng() * 0.5);
                if (candidate > score) { largest = i; score = candidate; }
            });
            const tile = tiles.splice(largest, 1)[0];
            const fraction = 0.32 + rng() * 0.36;
            if (tile.w / tile.h > aspect * (0.8 + rng() * 0.4)) {
                const w = tile.w * fraction;
                tiles.push({...tile,w}, {...tile,x:tile.x+w,w:tile.w-w});
            } else {
                const h = tile.h * fraction;
                tiles.push({...tile,h}, {...tile,y:tile.y+h,h:tile.h-h});
            }
        }
        for (const {x,y,w,h} of tiles) polygon([[x,y],[x+w,y],[x+w,y+h],[x,y+h]]);
    } else if (settings.shape === 'hexagons') {
        const radius = Math.sqrt(unit * unit / (3 * Math.sqrt(3) / 2));
        const dx = radius * 1.5, dy = radius * Math.sqrt(3);
        const ox = rng() * dx, oy = rng() * dy;
        for (let col = -2; col <= Math.ceil(width / dx) + 1; col++) {
            for (let row = -2; row <= Math.ceil(height / dy) + 1; row++) {
                const x = ox + col * dx, y = oy + (row + (col & 1) / 2) * dy;
                clippedPolygon(Array.from({length: 6}, (_, i) => {
                    const angle = i * Math.PI / 3;
                    return [x + radius * Math.cos(angle), y + radius * Math.sin(angle)];
                }));
            }
        }
    } else if (settings.shape === 'triangles') {
        const side = Math.sqrt(unit * unit * 4 / Math.sqrt(3));
        const dy = side * Math.sqrt(3) / 2;
        const ox = rng() * side, oy = rng() * dy;
        for (let row = -2; row <= Math.ceil(height / dy) + 1; row++) {
            for (let col = -2; col <= Math.ceil(width / side) + 1; col++) {
                const x = ox + (col + (row & 1) / 2) * side, y = oy + row * dy;
                const shift = (row & 1) ? -side / 2 : side / 2;
                const a = [x,y], b = [x+side,y];
                const c = [x+side+shift,y+dy], d = [x+shift,y+dy];
                if (row & 1) { clippedPolygon([a,b,c]); clippedPolygon([a,c,d]); }
                else { clippedPolygon([a,b,d]); clippedPolygon([b,c,d]); }
            }
        }
    } else if (settings.shape === 'diamonds') {
        const radius = unit / Math.sqrt(2);
        const dx = radius * 2, dy = radius;
        const ox = rng() * dx, oy = rng() * dy;
        for (let row = -2; row <= Math.ceil(height / dy) + 1; row++) {
            for (let col = -2; col <= Math.ceil(width / dx) + 1; col++) {
                const x = ox + (col + (row & 1) / 2) * dx, y = oy + row * dy;
                clippedPolygon([[x,y-radius],[x+radius,y],[x,y+radius],[x-radius,y]]);
            }
        }
    }
    return cells;
}
