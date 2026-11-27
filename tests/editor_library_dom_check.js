// Library editor JavaScript + real Flask API, including shared-draft navigation.
const assert = require('assert');
const fs = require('fs');
const {spawn} = require('child_process');
const readline = require('readline');
const {JSDOM} = require('jsdom');
const root = require('path').resolve(__dirname, '..');
const python = spawn('python3', ['-u', '-c', `
import sys,json
from pathlib import Path
sys.path.insert(0, 'tests')
from fixtures import env
web,_,cfg,_ = env.setup(Path('tests/fixtures/editor/managed.yaml'), freeze_time=False)
Path(cfg.path).write_text('''schema: 2
interactive: true
elements:
  lamp: {type: dimmer, title: Свет, channel: dev/old, channel_brightness: dev/level}
  graph: {type: chart, title: График}
panels:
  a: {title: A, tiles: [{element: lamp}]}
  b: {title: B, sections: [{title: Lights, tiles: [{element: lamp}]}]}
  summary: {sections: [{from: [a, b]}]}
''')
cfg.reload()
client = web.app.test_client()
for line in sys.stdin:
    req=json.loads(line)
    res=client.open(req['path'], method=req['method'], data=req.get('data'), headers=req.get('headers'))
    print(json.dumps({'id':req['id'],'status':res.status_code,'body':res.get_data(as_text=True),'headers':dict(res.headers)}), flush=True)
`], {cwd: root, stdio: ['pipe', 'pipe', 'inherit']});
let serial = 0;
const pending = new Map();
const requests = [];
const doms = [];
readline.createInterface({input: python.stdout}).on('line', line => {
  const res = JSON.parse(line); const done = pending.get(res.id); pending.delete(res.id); done(res);
});
function rpc(method, path, data, headers) {
  return new Promise(resolve => {
    const id = ++serial; pending.set(id, resolve); requests.push({method, path, data});
    python.stdin.write(JSON.stringify({id, method, path, data, headers}) + '\n');
  });
}
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(fn) {
  for (let n = 0; n < 250; n++) { if (fn()) return; await pause(20); }
  throw new Error('Timed out: ' + fn.toString());
}
async function makePage(path, script, saved) {
  const page = await rpc('GET', path);
  assert.strictEqual(page.status, 200);
  const dom = new JSDOM(page.body, {url: 'http://localhost' + path, runScripts: 'outside-only'});
  doms.push(dom);
  const w = dom.window;
  w.matchMedia = () => ({matches: false, addListener() {}});
  w.scrollTo = () => {};
  w.confirm = () => true;
  w.prompt = () => null;
  if (saved) w.sessionStorage.setItem('wb-editor:shared', JSON.stringify(saved));
  w.XMLHttpRequest = class {
    constructor() { this.headers = {}; }
    open(method, url) {
      this.method = method;
      const target = new URL(url, w.location);
      this.url = target.pathname + target.search;
    }
    setRequestHeader(name, value) { this.headers[name] = value; }
    getResponseHeader(name) {
      return Object.entries(this.responseHeaders).find(([key]) => key.toLowerCase() === name.toLowerCase())?.[1] || '';
    }
    abort() { this.aborted = true; }
    send(data) {
      rpc(this.method, this.url, data, this.headers).then(res => {
        this.status = res.status; this.responseText = res.body; this.responseHeaders = res.headers;
        if (!this.aborted) this.onload();
      }).catch(err => { throw err; });
    }
  };
  w.eval(fs.readFileSync(root + '/static/js/' + script + '.js', 'utf8'));
  await until(() => !w.document.getElementById('workspace').hidden);
  return dom;
}
function stored(w) { return JSON.parse(w.sessionStorage.getItem('wb-editor:shared')); }
function submit(w, id) { w.document.getElementById(id).dispatchEvent(new w.Event('submit', {cancelable: true})); }
function search(w, value) {
  const input = w.document.getElementById('library-search');
  input.value = value; input.dispatchEvent(new w.Event('input', {bubbles: true}));
}
function choosePanel(w, id) {
  const select = w.document.getElementById('panels');
  select.value = id; select.dispatchEvent(new w.Event('change'));
}
function preventNavigation(w, id) {
  w.document.getElementById(id).addEventListener('click', event => event.preventDefault());
}
function brightness(w) {
  return w.document.querySelector('#property-fields [name="channel_brightness"]').value;
}
(async () => {
  try {
    // A placement edit gives the shared draft both a version and panel context.
    let dom = await makePage('/editor', 'editor');
    let w = dom.window, d = w.document;
    choosePanel(w, 'b');
    d.querySelector('#instances button').click();
    d.getElementById('width').value = '2';
    submit(w, 'placement');
    await until(() => d.getElementById('edit-status').textContent.includes('Сохранено'));
    preventNavigation(w, 'library-link');
    d.getElementById('library-link').click();
    const first = stored(w);
    assert.strictEqual(first.panel, 'b');
    assert.strictEqual(first.version, 1);
    assert(first.tile);

    // The library resumes that draft instead of creating another one.
    dom = await makePage('/editor/library', 'library', first);
    w = dom.window; d = w.document;
    assert.strictEqual(stored(w).id, first.id);
    assert.strictEqual(requests.filter(r => r.method === 'POST' && r.path === '/api/editor/drafts').length, 1);
    assert.strictEqual(d.getElementById('property-id').textContent, 'lamp');
    assert.deepStrictEqual(Array.from(d.querySelectorAll('#element-usage li')).map(item => item.textContent),
      ['a', 'b', 'summary']);

    search(w, 'Свет');
    assert.strictEqual(d.querySelectorAll('#library-list [data-element]').length, 1);
    assert(d.querySelector('#library-list [data-element="lamp"]'));
    search(w, 'graph');
    assert(d.querySelector('#library-list [data-element="graph"]'));
    search(w, 'dimmer');
    assert(d.querySelector('#library-list [data-element="lamp"]'));
    search(w, 'ничего-такого-нет');
    assert.strictEqual(d.querySelectorAll('#library-list [data-element]').length, 0);
    assert(!d.getElementById('library-empty').hidden);
    search(w, '');
    d.querySelector('#library-list [data-element="lamp"]').click();
    const preview = d.getElementById('property-preview');
    assert(preview.src.includes('/element.svg?element=lamp&draft_version=1'));
    preview.dispatchEvent(new w.Event('load'));
    assert(!preview.hidden && d.getElementById('property-preview-status').hidden);

    const advanced = d.getElementById('advanced-properties');
    assert(!advanced.open && advanced.querySelector('[name="command_topic_brightness"]'));
    assert(!advanced.contains(d.querySelector('[name="channel_brightness"]')));
    assert(d.getElementById('guide-example').textContent.includes('wb-mdm3_119/Channel 1'));
    advanced.open = true;
    advanced.querySelector('[name="command_topic_brightness"]').value = '/demo/brightness/set';
    advanced.open = false;
    d.querySelector('#property-fields [name="channel_brightness"]').value = 'dev/new-level';
    submit(w, 'property-form');
    await until(() => d.getElementById('edit-status').textContent.includes('Сохранено'));
    assert.strictEqual(d.getElementById('advanced-properties').querySelector('[name="command_topic_brightness"]').value, '/demo/brightness/set');
    assert.strictEqual(brightness(w), 'dev/new-level');
    assert.strictEqual(stored(w).version, 2);
    assert.strictEqual(stored(w).panel, 'b');
    assert.strictEqual(stored(w).tile, first.tile);
    const edit = requests.filter(r => r.path.endsWith('/operation')).pop();
    assert(edit && edit.data.includes('"op":"update_element"'));
    assert(edit.data.includes('"channel_brightness":"dev/new-level"'));

    d.getElementById('check').click();
    await until(() => !d.getElementById('details').hidden);
    assert(d.getElementById('details').textContent.includes('Затронутые панели: a, b, summary'));
    assert(d.getElementById('details').textContent.includes('Изменены свойства элемента'));

    // Returning to Panels keeps the draft, panel, section and tile selection.
    preventNavigation(w, 'editor-link');
    d.getElementById('editor-link').click();
    const edited = stored(w);
    dom = await makePage('/editor', 'editor', edited);
    w = dom.window; d = w.document;
    assert.strictEqual(stored(w).id, first.id);
    assert.strictEqual(d.getElementById('panels').value, 'b');
    assert.strictEqual(d.getElementById('width').value, '2');
    assert(d.querySelector('#instances [aria-pressed="true"]'));
    assert.strictEqual(requests.filter(r => r.method === 'POST' && r.path === '/api/editor/drafts').length, 1);

    // A second visit resumes the same draft and can publish the library edit.
    preventNavigation(w, 'library-link');
    d.getElementById('library-link').click();
    const beforePublish = stored(w);
    dom = await makePage('/editor/library', 'library', beforePublish);
    w = dom.window; d = w.document;
    assert.strictEqual(brightness(w), 'dev/new-level');
    d.getElementById('publish').click();
    await until(() => d.getElementById('status').textContent.includes('Опубликовано. Можно продолжать'));
    assert.notStrictEqual(stored(w).id, first.id);
    assert.strictEqual(brightness(w), 'dev/new-level');
    assert.strictEqual(requests.filter(r => r.path === '/api/editor/drafts/' + first.id + '/publish').length, 1);
    d.getElementById('history').click();
    await until(() => d.getElementById('details').textContent.includes('История ревизий'));
    assert(d.getElementById('details').textContent.includes('publish'));
    console.log('library DOM + Flask: search, preview, shared draft, affected panels, publish OK');
  } finally {
    doms.forEach(dom => { try { dom.window.close(); } catch (ignore) {} });
    python.kill();
  }
})().catch(err => { console.error(err); process.exitCode = 1; });
