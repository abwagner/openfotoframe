// Seeded, bounded geometry. Density sets typical size; color changes never
// regenerate the layout. Polygon partitions share edges instead of cropping tiles.
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
    function divisions(length, segments) {
        const weights = Array.from({length: segments}, () => 0.55 + rng());
        const scale = length / weights.reduce((sum, w) => sum + w, 0);
        const cuts = [0];
        for (const weight of weights) cuts.push(cuts.at(-1) + weight * scale);
        cuts[cuts.length - 1] = length;
        return cuts;
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
        // Staggered seeds produce varied hexagonal cells. Boundary cells are
        // solved against the screen itself, rather than half a cropped hex row.
        const columns = Math.max(2, Math.round(Math.sqrt(count * width / height)));
        const rows = Math.max(2, Math.round(count / columns));
        const xs = divisions(width, columns), ys = divisions(height, rows);
        const points = [];
        for (let row = 0; row < rows; row++) for (let col = 0; col < columns; col++) {
            const dx = xs[col+1]-xs[col], dy = ys[row+1]-ys[row];
            points.push([xs[col] + dx * (0.4 + rng() * 0.2 + (row % 2 ? 0.25 : -0.15)),
                ys[row] + dy * (0.35 + rng() * 0.3)]);
        }
        for (const [px,py] of points) {
            let vertices = [[0,0],[width,0],[width,height],[0,height]];
            for (const [qx,qy] of points) {
                if (px === qx && py === qy) continue;
                const nx = qx-px, ny = qy-py, limit = (qx*qx+qy*qy-px*px-py*py)/2;
                const clipped = [];
                vertices.forEach((a,i) => {
                    const b = vertices[(i+1)%vertices.length];
                    const da = a[0]*nx+a[1]*ny-limit, db = b[0]*nx+b[1]*ny-limit;
                    if (da <= 0) clipped.push(a);
                    if ((da <= 0) !== (db <= 0)) {
                        const t = da/(da-db);clipped.push([a[0]+t*(b[0]-a[0]),a[1]+t*(b[1]-a[1])]);
                    }
                });
                vertices = clipped;
            }
            polygon(vertices);
        }
    } else if (settings.shape === 'triangles' || settings.shape === 'diamonds') {
        const desired = settings.shape === 'triangles' ? count / 2 : count;
        const columns = Math.max(2, Math.round(Math.sqrt(desired * width / height)));
        const rows = Math.max(2, Math.round(desired / columns));
        const xs = divisions(width, columns), ys = divisions(height, rows);
        const mesh = ys.map((y,row) => xs.map((x,col) => {
            // Keep perimeter vertices on their boundary, while interior joints
            // move together so adjacent cells remain a complete partition.
            const dx = Math.min(xs[col]-xs[col-1],xs[col+1]-xs[col]);
            const dy = Math.min(ys[row]-ys[row-1],ys[row+1]-ys[row]);
            return [col === 0 || col === columns ? x : x + (rng()-0.5)*dx*(settings.shape === 'diamonds' ? 0.25 : 0.7),
                row === 0 || row === rows ? y : y + (rng()-0.5)*dy*(settings.shape === 'diamonds' ? 0.25 : 0.7)];
        }));
        function diamond(vertices) {
            // Turn the fitted mesh into a diamond lattice. A radial square map
            // preserves boundary order; explicitly include any screen corner
            // crossed by an edge so every perimeter cell stays whole.
            const mapped = vertices.map(([x,y]) => {
                const u=x/width*2-1,v=y/height*2-1,r=Math.max(Math.abs(u),Math.abs(v));
                const angle=Math.atan2(v,u)+Math.PI/4;
                const cx=Math.cos(angle),cy=Math.sin(angle),scale=r/Math.max(Math.abs(cx),Math.abs(cy));
                return [(cx*scale+1)*width/2,(cy*scale+1)*height/2];
            });
            const fitted=[];
            const close=(a,b)=>Math.abs(a-b)<1e-7;
            for(let i=0;i<vertices.length;i++) {
                const a=vertices[i],b=vertices[(i+1)%vertices.length];
                const p=mapped[i],q=mapped[(i+1)%mapped.length];
                fitted.push(p);
                const boundary=(close(a[0],b[0]) && (close(a[0],0)||close(a[0],width)))
                    || (close(a[1],b[1]) && (close(a[1],0)||close(a[1],height)));
                if(boundary && !close(p[0],q[0]) && !close(p[1],q[1])) {
                    for(const [x,y] of [[0,0],[width,0],[width,height],[0,height]]) {
                        if((close(p[0],x)&&close(q[1],y)) || (close(q[0],x)&&close(p[1],y))) fitted.push([x,y]);
                    }
                }
            }
            polygon(fitted);
        }
        for (let row = 0; row < rows; row++) for (let col = 0; col < columns; col++) {
            const a=mesh[row][col],b=mesh[row][col+1],c=mesh[row+1][col+1],d=mesh[row+1][col];
            if (settings.shape === 'diamonds') diamond([a,b,c,d]);
            else if (rng() < 0.5) { polygon([a,b,c]);polygon([a,c,d]); }
            else { polygon([a,b,d]);polygon([b,c,d]); }
        }
    }
    return cells;
}
