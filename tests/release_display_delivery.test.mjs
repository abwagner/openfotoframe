import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../release_web/static/display.js', import.meta.url), 'utf8')
    .replace(/^import .*;\n/, '');
async function player() {
    const elements = new Map();
    const node = id => {
        if (!elements.has(id)) elements.set(id, {dataset: {}, content:'csrf', textContent:'',
            addEventListener() {}, removeAttribute(name) { delete this[name]; },
            classList: {add() {}, remove() {}},
        });
        return elements.get(id);
    };
    const requests = [], replies = [];
    const playlist = {frame_id:'f', settings:{}, state:{index:0,paused:false},
        slides:[{id:'a',snapshot_url:'/frame/snapshots/a?revision=1'}]};
    replies.push({status:200,ok:true,headers:{get:()=> '"revision-one"'},json:async()=>playlist},
        {ok:true,blob:async()=>({})});
    const context = vm.createContext({document:{body:{dataset:{frameId:'f',physical:'false'}},
            getElementById:node,querySelector:node,addEventListener() {}},
        window:{addEventListener() {}},ArtworkHost:class {},AbortController,
        setInterval() { return 1; }, clearInterval() {},setTimeout,clearTimeout,
        URL:{createObjectURL:()=> 'blob:photo',revokeObjectURL() {}},
        fetch:async(url,options)=>{ requests.push({url,options});
            assert.ok(replies.length, 'unexpected fetch'); return replies.shift(); },
    });
    vm.runInContext(source + '\nglobalThis.tick=refresh;', context);
    await new Promise(resolve=>setImmediate(resolve));
    return {context,node,requests,replies};
}

test('unchanged authorized playlist reuses the displayed image without another download', async () => {
    const p = await player();
    assert.equal(p.node('current-slide').src, 'blob:photo');
    p.replies.push({status:304,json:()=>{throw new Error('304 has no JSON body');}});
    await p.context.tick();
    assert.equal(p.requests[2].options.headers['If-None-Match'], '"revision-one"');
    assert.equal(p.requests.length, 3);
    assert.equal(p.node('current-slide').src, 'blob:photo');
});

test('revocation clears displayed media and in-memory playlist validator', async () => {
    const p = await player();
    p.replies.push({status:401,ok:false,json:async()=>({error:{message:'Frame revoked.'}})});
    await p.context.tick();
    assert.equal(p.node('current-slide').src, undefined);
    assert.equal(p.node('display-message').textContent, 'Frame revoked.');
    p.replies.push({status:304});
    await p.context.tick();
    assert.equal(p.requests[3].options.headers['If-None-Match'], undefined);
    assert.equal(p.node('current-slide').src, undefined);
    assert.match(p.node('display-message').textContent, /reload its playlist/);
});
