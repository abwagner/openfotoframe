// Build geometry only when dimensions, seed, or shape settings change.
// Every shape shares the artwork's color field, grout, and viewer response.
export function patternedCells(width, height, settings, rng) {
    const cells = [];
    const unit = Math.sqrt(width * height / settings.cell_density);
    function polygon(vertices) {
        const x = vertices.reduce((sum, p) => sum + p[0], 0) / vertices.length;
        const y = vertices.reduce((sum, p) => sum + p[1], 0) / vertices.length;
        const path = new Path2D();
        vertices.forEach(([vx, vy], i) => {
            vx = x + (vx - x) * 0.98; vy = y + (vy - y) * 0.98;
            if (i) path.lineTo(vx, vy); else path.moveTo(vx, vy);
        });
        path.closePath();
        cells.push({ x, y, path, phase: rng() * Math.PI * 2 });
    }
    function circle(x, y, radius) {
        const path = new Path2D(); path.arc(x, y, radius, 0, Math.PI * 2);
        cells.push({ x, y, radius, path, phase: rng() * Math.PI * 2 });
    }
    if (settings.shape === 'circles') {
        const columns = Math.max(2, Math.round(width / unit));
        const rows = Math.max(2, Math.round(height / unit));
        const dx = width / columns, dy = height / rows;
        const radius = Math.min(dx, dy) * 0.46;
        for (let row = 0; row < rows; row++) for (let col = 0; col < columns; col++) {
            circle((col + 0.5) * dx, (row + 0.5) * dy, radius);
        }
        // Seeded rejection packing inserts smaller circles only where they fit.
        // Higher fill extends the same sequence, keeping existing circles stable.
        const target = Math.round(columns * rows * 3 * (settings.circle_fill ?? 0.5));
        const gap = Math.min(width, height) * 0.004;
        const baseCount = cells.length;
        for (let attempt = 0; attempt < columns * rows * 40 && cells.length - baseCount < target; attempt++) {
            const x = rng() * width, y = rng() * height;
            let available = Math.min(x, y, width - x, height - y, radius * 0.5);
            for (const cell of cells) {
                available = Math.min(available, Math.hypot(x - cell.x, y - cell.y) - cell.radius - gap);
                if (available < radius * 0.08) break;
            }
            if (available >= radius * 0.08) circle(x, y, available);
        }
    } else if (settings.shape === 'rectangles') {
        const aspect = Math.pow(4, 1 - (settings.rectangle_squareness ?? 0.5));
        const dy = unit / Math.sqrt(aspect), dx = dy * aspect;
        for (let y = 0; y < height; y += dy) for (let x = 0; x < width; x += dx) {
            polygon([[x, y], [x + dx, y], [x + dx, y + dy], [x, y + dy]]);
        }
    } else if (settings.shape === 'hexagons') {
        const radius = unit / Math.sqrt(3 * Math.sqrt(3) / 2);
        const dx = radius * 1.5, dy = radius * Math.sqrt(3);
        for (let col = 0; col * dx < width + radius; col++) {
            for (let row = -1; row * dy < height + dy; row++) {
                const x = col * dx, y = (row + (col % 2) / 2) * dy;
                polygon(Array.from({ length: 6 }, (_, i) => [x + radius * Math.cos(i * Math.PI / 3), y + radius * Math.sin(i * Math.PI / 3)]));
            }
        }
    } else if (settings.shape === 'triangles') {
        const side = unit * Math.sqrt(4 / Math.sqrt(3)), dy = side * Math.sqrt(3) / 2;
        for (let row = 0; row * dy < height; row++) {
            const y = row * dy, offset = (row % 2) * side / 2;
            for (let x = -side + offset; x < width; x += side) {
                const bottom = x + side / 2;
                polygon([[x, y], [x + side, y], [bottom, y + dy]]);
                polygon([[x + side, y], [bottom, y + dy], [bottom + side, y + dy]]);
            }
        }
    } else if (settings.shape === 'diamonds') {
        const radius = unit / Math.sqrt(2);
        for (let row = 0; row * radius < height + radius; row++) {
            for (let x = -(row % 2) * radius; x < width + radius; x += radius * 2) {
                const y = row * radius;
                polygon([[x, y - radius], [x + radius, y], [x, y + radius], [x - radius, y]]);
            }
        }
    }
    return cells;
}
