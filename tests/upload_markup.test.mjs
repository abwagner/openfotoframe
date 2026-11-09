import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../static/js/upload.js', import.meta.url), 'utf8');
const helper = source.match(/function escAttr\(str\) \{[^\n]*\n[^\n]*\n\s*\}/)[0];

test('frame selectors escape administrator-controlled names and option attributes', () => {
    const expression = source.match(/const profileOptions = (.*);/)[1];
    const result = vm.runInNewContext(helper + '\n' + expression, {
        displayProfiles: [{active: true, id: 'f" onfocus="bad',
            name: '</option><img src=x onerror=bad>', width: 1920, height: 1080}],
        activeDisplayId: '',
    });
    assert.equal(result, '<option value="f&quot; onfocus=&quot;bad" >&lt;/option&gt;&lt;img src=x onerror=bad&gt; (1920×1080)</option>');
});

test('gallery attribution cannot inject markup through legacy imported usernames', () => {
    const expression = source.match(/<span>(\$\{[^\n]*uploaded_by[^\n]*\})<\/span>/)[1];
    const result = vm.runInNewContext(helper + '\n`' + expression + '`', {
        img: {uploaded_by: '<img src=x onerror=bad>'},
    });
    assert.equal(result, '&lt;img src=x onerror=bad&gt;');
});
