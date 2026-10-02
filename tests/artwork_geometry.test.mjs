// Run with: node --test tests/artwork_geometry.test.mjs
import { readFileSync } from 'node:fs';
import assert from 'node:assert/strict';
import test from 'node:test';

// Geometry is independent of Canvas rasterization; keep the real vertices and
// replace only the browser Path2D sink to run these invariants without a browser.
globalThis.Path2D = class { moveTo() {} lineTo() {} closePath() {} arc() {} };
const source = readFileSync(new URL('../static/js/artworks/cell-geometry.js', import.meta.url), 'utf8');
const { patternedCells } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
function random(seed) {
    let state = seed >>> 0;
    return () => {
        state += 0x6D2B79F5;
        let n = Math.imul(state ^ state >>> 15, 1 | state);
        n ^= n + Math.imul(n ^ n >>> 7, 61 | n);
        return ((n ^ n >>> 14) >>> 0) / 4294967296;
    };
}
function generate(shape, width, height, seed = 42173, extras = {}) {
    return patternedCells(width,height,{shape,cell_density:80,circle_fill:0.5,rectangle_squareness:0.5,...extras},random(seed));
}
function area(vertices) {
    return Math.abs(vertices.reduce((sum,a,i) => {
        const b=vertices[(i+1)%vertices.length]; return sum+a[0]*b[1]-a[1]*b[0];
    },0))/2;
}
function inside(x,y,vertices) {
    let result=false;
    for(let i=0,j=vertices.length-1;i<vertices.length;j=i++) {
        const a=vertices[i],b=vertices[j];
        if((a[1]>y)!==(b[1]>y) && x<(b[0]-a[0])*(y-a[1])/(b[1]-a[1])+a[0]) result=!result;
    }
    return result;
}
for(const shape of ['rectangles','triangles','diamonds','hexagons']) {
    test(`${shape}: exact bounded partition, seeded size variation`, () => {
        for(const [w,h] of [[1280,720],[720,1280],[853,479]]) for(const seed of [42173,7,891]) {
            const cells=generate(shape,w,h,seed);
            for(const cell of cells) for(const [x,y] of cell.vertices) {
                assert.ok(x>=-1e-7 && x<=w+1e-7 && y>=-1e-7 && y<=h+1e-7,'No clipped tiles');
            }
            const areas=cells.map(c=>area(c.vertices));
            assert.ok(Math.abs(areas.reduce((a,b)=>a+b,0)-w*h)<w*h*1e-8,'Areas exactly fill screen');
            assert.ok(Math.max(...areas)/Math.min(...areas)>1.5,'Sizes vary at default settings');
            // Check both overlaps and holes, including points next to every edge.
            for(let iy=0;iy<21;iy++) for(let ix=0;ix<31;ix++) {
                const x=(ix+0.17)/31*w,y=(iy+0.23)/21*h;
                assert.equal(cells.filter(c=>inside(x,y,c.vertices)).length,1,'Each point belongs to one complete cell');
            }
            assert.deepEqual(cells,generate(shape,w,h,seed),'Same seed reproduces geometry');
            assert.notDeepEqual(cells,generate(shape,w,h,seed+1),'New pattern changes geometry');
        }
    });
}
test('Circles: varied whole circles, no overlaps, stable increasing gap fill', () => {
    for(const [w,h] of [[1280,720],[720,1280]]) {
        const base=generate('circles',w,h,123,{circle_fill:0});
        const half=generate('circles',w,h,123,{circle_fill:0.5});
        const full=generate('circles',w,h,123,{circle_fill:1});
        assert.ok(base.length<half.length && half.length<full.length);
        assert.deepEqual(full.slice(0,base.length),base);
        assert.deepEqual(full.slice(0,half.length),half);
        assert.ok(Math.max(...base.map(c=>c.radius))/Math.min(...base.map(c=>c.radius))>1.5);
        for(let i=0;i<full.length;i++) {
            const a=full[i];assert.ok(a.x-a.radius>=0 && a.x+a.radius<=w && a.y-a.radius>=0 && a.y+a.radius<=h);
            for(let j=i+1;j<full.length;j++) {
                const b=full[j]; assert.ok(Math.hypot(a.x-b.x,a.y-b.y)>=a.radius+b.radius);
            }
        }
    }
});
test('Density controls typical size; squareness controls the rectangle distribution', () => {
    const low=generate('rectangles',1280,720,123,{cell_density:20});
    const high=generate('rectangles',1280,720,123,{cell_density:300});
    assert.equal(low.length,20);assert.equal(high.length,300);
    const aspect=setting=>generate('rectangles',1280,720,123,{rectangle_squareness:setting})
        .reduce((sum,c)=>sum+(c.vertices[1][0]-c.vertices[0][0])/(c.vertices[3][1]-c.vertices[0][1]),0)/80;
    assert.ok(aspect(0)>aspect(1)*2);
});
test('Circle gap fill continues after unsuccessful candidate batches', () => {
    const counts = [0,0.5,1].map(circle_fill => {
        let n=123;
        const rng=()=>((n=(Math.imul(n,1664525)+1013904223)>>>0)/4294967296);
        return patternedCells(1280,720,{shape:'circles',cell_density:80,circle_fill},rng).length;
    });
    assert.ok(counts[0]<counts[1] && counts[1]<counts[2]);
});
