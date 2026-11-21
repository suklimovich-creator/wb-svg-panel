// Real editor JavaScript + Flask API without browser sockets; no physical layout assertions.
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
  lamp: {type: switch, channel: dev/old}
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
readline.createInterface({input: python.stdout}).on('line', line => {
  const res = JSON.parse(line); const done = pending.get(res.id); pending.delete(res.id); done(res);
});
function rpc(method, path, data, headers) {
  return new Promise(resolve => {
    const id = ++serial; pending.set(id, resolve);
    python.stdin.write(JSON.stringify({id, method, path, data, headers}) + '\n');
  });
}
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(fn) {
  for (let n = 0; n < 250; n++) { if (fn()) return; await pause(20); }
  throw new Error('Timed out: ' + fn.toString());
}
(async () => {
  let dom;
  try {
    const page = await rpc('GET', '/editor');
    dom = new JSDOM(page.body, {url: 'http://localhost/editor', runScripts: 'outside-only'});
    const w = dom.window, d = w.document, el = id => d.getElementById(id);
    const prompts = [], confirmations = [];
    w.matchMedia = () => ({matches: false, addListener() {}});
    w.scrollTo = () => {};
    w.confirm = message => { confirmations.push(message); return true; };
    w.prompt = () => prompts.shift();
    w.XMLHttpRequest = class {
      constructor() { this.headers = {}; }
      open(method, url) { this.method = method; this.url = new URL(url, w.location).pathname + new URL(url, w.location).search; }
      setRequestHeader(name, value) { this.headers[name] = value; }
      getResponseHeader(name) { return Object.entries(this.responseHeaders).find(([key]) => key.toLowerCase() === name.toLowerCase())?.[1] || ''; }
      abort() { this.aborted = true; }
      send(data) {
        rpc(this.method, this.url, data, this.headers).then(res => {
          this.status = res.status; this.responseText = res.body; this.responseHeaders = res.headers;
          if (!this.aborted) this.onload();
        }).catch(err => { throw err; });
      }
    };
    w.eval(fs.readFileSync(root + '/static/js/editor.js', 'utf8'));
    await until(() => !el('workspace').hidden && !el('panels').disabled);
    function choose(id, value) { el(id).value = value; el(id).dispatchEvent(new w.Event('change')); }
    function submit(id) { el(id).dispatchEvent(new w.Event('submit', {cancelable: true})); }
    function selectedTile() { d.querySelector('#instances button').click(); }
    selectedTile();
    assert(el('binding-title').textContent.includes('Общая'));
    assert(el('binding-scope').textContent.includes('a, b, summary'));
    el('width').value = '2'; submit('placement');
    await until(() => !el('width').disabled);
    choose('panels', 'b'); selectedTile();
    assert.strictEqual(el('width').value, '');
    let channel = d.querySelector('[data-binding="channel"]');
    channel.value = 'dev/new'; submit('binding-form');
    await until(() => !el('save-binding').disabled && d.querySelector('[data-binding="channel"]').value === 'dev/new');
    choose('panels', 'a'); selectedTile();
    assert.strictEqual(d.querySelector('[data-binding="channel"]').value, 'dev/new');
    assert.strictEqual(el('width').value, '2');
    prompts.push('a-copy', 'A copy'); el('copy-panel').click();
    await until(() => el('panels').value === 'a-copy' && !el('panels').disabled);
    selectedTile(); el('width').value = '3'; submit('placement');
    await until(() => !el('width').disabled);
    choose('panels', 'a'); selectedTile(); assert.strictEqual(el('width').value, '2');
    choose('panels', 'b'); prompts.push('Lights copy'); el('copy-section').click();
    await until(() => el('sections').options.length === 2 && !el('sections').disabled);
    assert.strictEqual(el('sections').selectedIndex, 1);
    el('check').click();
    await until(() => !el('details').hidden && !el('check').disabled);
    assert(el('details').textContent.includes('Затронутые панели: a, a-copy, b, summary'));
    el('publish').click();
    await until(() => el('status').textContent.includes('Опубликовано. Можно продолжать'));
    assert(confirmations.some(text => text.includes('Затронутые панели: a, a-copy, b, summary')));
    const saved = JSON.parse(await (await rpc('GET', '/api/editor/state')).body);
    assert(saved.managed);
    console.log('editor DOM + Flask: local/shared edits, copies, affected panels, publication OK');
  } finally { if (dom) dom.window.close(); python.kill(); }
})().catch(err => { console.error(err); process.exitCode = 1; });
