/* Сервер панели в браузере: Pyodide + boot.py (tools/demo-site/).
   Страница шлёт {type:'req', id, method, url, body, headers}, получает
   {type:'resp', id, status, headers, body}. Пока сервер поднимается -
   {type:'progress', text}, затем {type:'ready'} или {type:'fail', text}.

   Исходники Python приходят текстом (files.json): стандартную библиотеку
   Pyodide ждёт одним zip - складываем его здесь, в памяти, без сжатия. */
var base = self.location.href.replace(/[^\/]*$/, '');
importScripts(base + 'pyodide.js');

var handle = null;
function say(text){ postMessage({type: 'progress', text: text}); }

var CRC = (function(){
  var t = new Uint32Array(256);
  for (var n = 0; n < 256; n++) {
    var c = n;
    for (var k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1;
    t[n] = c >>> 0;
  }
  return t;
})();
function crc32(b){
  var c = 0xFFFFFFFF;
  for (var i = 0; i < b.length; i++) c = CRC[(c ^ b[i]) & 0xFF] ^ (c >>> 8);
  return (c ^ 0xFFFFFFFF) >>> 0;
}
/* Zip без сжатия (store): локальные заголовки, файлы, оглавление. */
function storeZip(files){
  var enc = new TextEncoder(), parts = [], dir = [], off = 0;
  Object.keys(files).forEach(function(path){
    var name = enc.encode(path), data = enc.encode(files[path]), crc = crc32(data);
    var h = new DataView(new ArrayBuffer(30));
    h.setUint32(0, 0x04034b50, true); h.setUint16(4, 20, true); h.setUint16(6, 0x0800, true);
    h.setUint32(14, crc, true); h.setUint32(18, data.length, true); h.setUint32(22, data.length, true);
    h.setUint16(26, name.length, true);
    parts.push(h.buffer, name, data);
    var d = new DataView(new ArrayBuffer(46));
    d.setUint32(0, 0x02014b50, true); d.setUint16(4, 20, true); d.setUint16(6, 20, true);
    d.setUint16(8, 0x0800, true); d.setUint32(16, crc, true); d.setUint32(20, data.length, true);
    d.setUint32(24, data.length, true); d.setUint16(28, name.length, true); d.setUint32(42, off, true);
    dir.push(d.buffer, name);
    off += 30 + name.length + data.length;
  });
  var size = dir.reduce(function(s, x){ return s + x.byteLength; }, 0);
  var e = new DataView(new ArrayBuffer(22));
  e.setUint32(0, 0x06054b50, true); e.setUint16(8, dir.length / 2, true);
  e.setUint16(10, dir.length / 2, true); e.setUint32(12, size, true); e.setUint32(16, off, true);
  return new Blob(parts.concat(dir, [e.buffer]), {type: 'application/zip'});
}

var ready = (async function(){
  say('Загружаю панель…');
  var files = await (await fetch(base + 'files.json')).json();
  say('Запускаю Python…');
  var stdlib = URL.createObjectURL(storeZip(files.stdlib));
  var py = await loadPyodide({indexURL: base, stdLibURL: stdlib,
                              stdout: function(){}, stderr: function(){}});
  URL.revokeObjectURL(stdlib);
  say('Собираю дом…');
  Object.keys(files.app).forEach(function(path){
    var full = '/app/' + path, dir = full.slice(0, full.lastIndexOf('/'));
    py.FS.mkdirTree(dir);
    py.FS.writeFile(full, files.app[path]);
  });
  py.runPython("import sys; sys.path.insert(0, '/app'); import boot; boot.start('/app')");
  handle = py.pyimport('boot').handle_json;
  postMessage({type: 'ready'});
})().catch(function(e){
  postMessage({type: 'fail', text: String(e && e.message || e)});
  throw e;
});

self.onmessage = function(e){
  var m = e.data;
  if (!m || m.type !== 'req') return;
  ready.then(function(){
    var out;
    try { out = JSON.parse(handle(m.method, m.url, m.body, JSON.stringify(m.headers || {}))); }
    catch (err) { out = [500, {'Content-Type': 'text/plain'}, String(err)]; }
    postMessage({type: 'resp', id: m.id, status: out[0], headers: out[1], body: out[2]});
  });
};
