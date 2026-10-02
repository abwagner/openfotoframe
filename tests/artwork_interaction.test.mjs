// Exercise the actual renderer with a recording Canvas sink.
import { readFileSync } from 'node:fs';
import assert from 'node:assert/strict';
import test from 'node:test';
const geometry = readFileSync(new URL('../static/js/artworks/cell-geometry.js',import.meta.url),'utf8');
const source = readFileSync(new URL('../static/js/artworks/organic-cells.js',import.meta.url),'utf8')
    .replace("'./cell-geometry.js'", JSON.stringify(`data:text/javascript;base64,${Buffer.from(geometry).toString('base64')}`));
const { createArtwork } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
globalThis.Path2D = class {
    vertices=[];
    moveTo(x,y) { this.vertices.push([x,y]); }
    lineTo(x,y) { this.vertices.push([x,y]); }
    closePath() {}
};
function render(person,width,height) {
    const painted=[];
    const ctx={setTransform(){},fillRect(){},stroke(){},fill(path){
        painted.push({vertices:path.vertices,color:this.fillStyle.match(/\d+/g).map(Number)});
    }};
    globalThis.document={createElement(){return {setAttribute(){},style:{},getContext(){return ctx;},remove(){}};}};
    const art=createArtwork({container:{append(){}},seed:123,settings:{
        shape:'rectangles',cell_density:300,rectangle_squareness:1,
        cell_colors:['#123B5D','#236B8E','#54A6B5','#B3DAD8'],
        grout_color:'#000000',idle_speed:0,interaction_strength:0.7,
    }});
    art.resize({width,height,pixelRatio:1});
    art.updateInteractionState({people:person ? [person] : []});
    art.render({elapsedSeconds:0,deltaSeconds:10});
    art.dispose();
    return painted;
}
test('Highlight follows both axes and affects a small area in landscape and portrait',()=>{
    for(const [width,height] of [[1280,720],[720,1280]]) {
        const base=render(null,width,height);
        for(const [x,y] of [[-0.5,0.25],[0.5,0.75]]) {
            const painted=render({id:'preview',x,y,speed:0},width,height);
            let area=0, weightedX=0, weightedY=0;
            painted.forEach((cell,i)=>{
                const change=Math.hypot(...cell.color.map((v,j)=>v-base[i].color[j]));
                if(change<20) return;
                const a=cell.vertices[0],c=cell.vertices[2];
                const weight=(c[0]-a[0])*(c[1]-a[1]);
                area+=weight;weightedX+=(a[0]+c[0])/2*weight;weightedY+=(a[1]+c[1])/2*weight;
            });
            assert.ok(area>0 && area/(width*height)<0.3,'Strong highlight occupies less than 30% of screen');
            assert.ok(Math.abs(weightedX/area/width-(x+1)/2)<0.08,'Horizontal center follows pointer');
            assert.ok(Math.abs(weightedY/area/height-y)<0.08,'Vertical center follows pointer');
        }
    }
});
