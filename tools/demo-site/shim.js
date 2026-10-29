/* Демо-страница без контроллера: fetch страницы панели уходит не в сеть,
   а в сервер панели, который работает тут же, в браузере (py/worker.js).
   Подключается первым скриптом, до static/js/core.js.

   Ждущий запрос (since=) держим здесь: у сервера в браузере один поток,
   и ждать изменений, держа его, он не может. Отпускаем после любой
   команды или через EVERY - как отвечал бы контроллер. */
(function(){
  var realFetch = window.fetch ? window.fetch.bind(window) : null;
  var worker = new Worker('py/worker.js');
  var pending = {}, seq = 0, ready = false, queue = [], waiting = [];
  var started = Date.now(), failed = null;

  function loader(text){
    var host = document.getElementById('host');
    if (!host || host.querySelector('svg')) return;
    var s = Math.round((Date.now() - started) / 1000);
    host.innerHTML = '<div class="demo-boot"><div class="demo-ring"></div>'
      + '<b>' + text + '</b><span>Демо умного дома: сервер панели запускается '
      + 'прямо в браузере, первый раз - секунд 10–20.' + (s > 3 ? ' ' + s + ' с' : '')
      + '</span></div>';
  }
  var lastText = 'Загружаю…';
  var tick = setInterval(function(){ if (!ready && !failed) loader(lastText); }, 1000);
  document.addEventListener('DOMContentLoaded', function(){ loader(lastText); });

  worker.onmessage = function(e){
    var m = e.data;
    if (m.type === 'progress') { lastText = m.text; loader(m.text); return; }
    if (m.type === 'ready') {
      ready = true; clearInterval(tick);
      queue.forEach(function(x){ worker.postMessage(x); }); queue = [];
      return;
    }
    if (m.type === 'fail') {
      failed = m.text; clearInterval(tick);
      var host = document.getElementById('host');
      if (host) host.innerHTML = '<div class="demo-boot"><b>Не запустилось</b><span>'
        + String(m.text).replace(/[<&]/g, '') + '</span></div>';
      return;
    }
    if (m.type === 'resp') {
      var done = pending[m.id]; delete pending[m.id];
      if (done) done(new Response(m.status === 204 ? null : m.body,
                                  {status: m.status, headers: m.headers}));
    }
  };

  function send(msg){ if (ready) worker.postMessage(msg); else queue.push(msg); }

  function release(){
    var list = waiting; waiting = [];
    list.forEach(function(w){ clearTimeout(w.timer); w.go(); });
  }

  function local(url){
    return !/^[a-z]+:/i.test(url) && !/^\/\//.test(url) && !/^(static|py)\//.test(url);
  }

  window.fetch = function(input, init){
    var url = typeof input === 'string' ? input : (input && input.url) || '';
    if (!local(url)) return realFetch(input, init);
    init = init || {};
    var headers = {};
    if (init.headers) {
      if (init.headers.forEach) init.headers.forEach(function(v, k){ headers[k] = v; });
      else for (var k in init.headers) headers[k] = init.headers[k];
    }
    var method = (init.method || 'GET').toUpperCase();
    var body = typeof init.body === 'string' ? init.body : null;
    var signal = init.signal;
    return new Promise(function(resolve, reject){
      if (signal && signal.aborted) { reject(new DOMException('aborted', 'AbortError')); return; }
      var id = ++seq;
      function go(){
        if (signal && signal.aborted) return;
        pending[id] = resolve;
        send({type: 'req', id: id, method: method, url: url, body: body, headers: headers});
      }
      if (signal) signal.addEventListener('abort', function(){
        waiting = waiting.filter(function(w){ return w.id !== id; });
        if (pending[id]) delete pending[id];
        reject(new DOMException('aborted', 'AbortError'));
      });
      if (method === 'POST') { go(); setTimeout(release, 60); return; }
      if (/[?&]since=/.test(url) && ready) {
        var w = {id: id, go: go};
        w.timer = setTimeout(function(){
          waiting = waiting.filter(function(x){ return x !== w; }); go();
        }, typeof EVERY === 'number' ? EVERY : 10000);
        waiting.push(w);
        return;
      }
      go();
    });
  };
})();
