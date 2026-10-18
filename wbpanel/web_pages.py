# -*- coding: utf-8 -*-
"""
Страницы демона: список панелей, обёртка панели (templates/wrapper.html),
витрина и редактор тем, браузер каналов, проверка конфига, статистика,
документация, манифест и значок. Выделено из web.py в 1.46.

Общее (app, конфиг, история, шаблоны) - в web.py: конфиг и историю
подменяют main() и проверки, поэтому они читаются как web.config,
web.history, а не копируются сюда при импорте.
"""

import html
import json
import os
import time
from flask import Response, request

from . import web
from . import __version__, checkup, palette, showcase, stats, themes
from .assemble import resolve_sections
from .const import BASE_DIR, TEMPLATE_DIRS
from .state import state
from .web import _json, app

def panel_order(panels):
    """
    Порядок панелей на главной.

    По умолчанию по алфавиту - но алфавит редко совпадает с тем, что нужно
    человеку: панель «all» оказывается первой просто потому, что с «a».
    Поле order задаёт вес: меньше - выше. Панели без веса идут следом,
    по алфавиту, чтобы не пришлось нумеровать все ради одной.

        panels:
          main:
            order: 1
    """
    def key(name):
        order = (panels.get(name) or {}).get("order")
        try:
            weight = float(order)
        except (TypeError, ValueError):
            weight = float("inf")
        return (weight, name)

    return sorted(panels, key=key)


@app.route("/")
def index():
    """Список всех панелей с живыми превью. Точка входа для человека."""
    web.config.reload()
    rows = []
    hidden_count = 0
    for name in panel_order(web.config.panels):
        panel_conf = web.config.panels[name] or {}
        tiles = []
        for _title, chunk, _status, _key in resolve_sections(panel_conf, web.config.panels):
            tiles.extend(chunk)
        kinds = {}
        for tile in tiles:
            kinds[tile.get("type", "value")] = kinds.get(tile.get("type", "value"), 0) + 1
        summary = ", ".join("%s × %d" % (k, v) for k, v in sorted(kinds.items()))
        is_hidden = bool(panel_conf.get("hidden"))
        if is_hidden:
            hidden_count += 1
        rows.append(
            '<a class="card%(cls)s" data-n="%(n)s" href="%(n)s.html">'
            '<div class="thumb">'
            '<img data-n="%(n)s" src="%(n)s.svg" loading="lazy" alt=""></div>'
            '<div class="meta"><b>%(t)s%(mark)s</b>'
            '<span class="slug">/%(n)s.svg</span>'
            '<span class="sum">%(s)s</span></div></a>' % {
                "n": html.escape(name),
                "t": html.escape(panel_conf.get("title") or name),
                "s": html.escape(summary or "пусто"),
                "cls": " is-hidden" if is_hidden else "",
                "mark": ' <span class="tagx">скрыта</span>' if is_hidden else "",
            })

    # Переключатель показываем, только когда есть что показывать: иначе он
    # просто мозолит глаза.
    toggle = ""
    if hidden_count:
        toggle = ('<label class="toggle"><input type="checkbox" id="sh">'
                  ' Показать скрытые <span class="cnt">%d</span></label>' % hidden_count)

    with state.lock:
        nchan = len(state.values)
        online = state.connected
    page = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Панели</title><style>
 body{font:14px/1.5 -apple-system,'Segoe UI',Inter,system-ui,sans-serif;
      margin:0;padding:22px;background:#F1F1F3;color:#3A3A3E}
 h1{font-size:22px;margin:0 0 4px}
 .sub{color:#8A8A90;margin-bottom:14px}
 .toggle{display:inline-flex;align-items:center;gap:7px;margin-bottom:18px;
         padding:7px 13px;background:#fff;border:1.5px solid rgba(60,60,67,.16);
         border-radius:10px;cursor:pointer;user-select:none;font-size:13.5px}
 .toggle:hover{border-color:#E09B2D}
 .toggle input{accent-color:#E09B2D;margin:0}
 .cnt{color:#8A8A90}
 .grid{display:grid;gap:16px;
       grid-template-columns:repeat(auto-fill,minmax(280px,1fr))}
 .card{display:block;text-decoration:none;color:inherit;background:#fff;
       border:1.5px solid rgba(60,60,67,.16);border-radius:16px;overflow:hidden}
 .card:hover{border-color:#E09B2D}
 .card.is-hidden{display:none;opacity:.72;border-style:dashed}
 .grid.show-hidden .card.is-hidden{display:block}
 .thumb{background:#E6E6E9;padding:10px;display:flex;justify-content:center}
 .thumb img{max-width:100%%;height:auto;border-radius:8px}
 .meta{padding:11px 14px}
 .meta b{display:block;font-size:15px}
 .tagx{font-size:11px;font-weight:600;color:#8A8A90;background:#EDEDF0;
       padding:1px 6px;border-radius:6px;vertical-align:1px}
 .slug{display:block;color:#8A8A90;font:12px ui-monospace,Menlo,monospace;
       margin-top:2px}
 .sum{display:block;color:#8A8A90;font-size:12.5px;margin-top:5px}
 .links{margin-top:22px;color:#8A8A90;font-size:13px}
 .links a{color:#3A3A3E}
 .off{color:#C0392B}
 /* тот же выбор колонок, что и на самой панели: значение общее */
 #cols{display:inline-block;background:#fff;border:1.5px solid rgba(60,60,67,.16);
       border-radius:11px;padding:3px;margin:0 0 14px 10px;vertical-align:middle}
 #cols b{display:inline-block;padding:6px 10px;border-radius:8px;cursor:pointer;
         font-size:13.5px;font-weight:600;color:#8A8A90}
 #cols b.on{background:#E09B2D;color:#fff}
</style></head><body>
<h1>Панели</h1>
<div class="sub">%(count)d шт. · каналов в памяти: %(chan)d · история: %(hist)s
 %(mqtt)s</div>
%(toggle)s<span id="cols"></span>
<div class="grid" id="g">%(rows)s</div>
<div class="links">
 <a href="docs"><b>Как собирать панели</b></a> ·
 <a href="channels">Список каналов</a> ·
 <a href="check">Проверка конфига</a> ·
 <a href="themes">Темы</a> ·
 <a href="healthz">healthz</a> ·
 плитка ведёт на <code>.html</code> с автообновлением,
 сама картинка — <code>&lt;имя&gt;.svg</code>
</div>
<script>
 var sh = document.getElementById('sh');
 if (sh) sh.onchange = function () {
   document.getElementById('g').classList.toggle('show-hidden', sh.checked);
 };

 /* Число колонок общее со страницами панелей - лежит в том же ключе.
    Меняем здесь: обновляются и превью, и ссылки, по которым вы уйдёте. */
 var STORE = 'wb-panel-cols', COLS = '';
 try { COLS = localStorage.getItem(STORE) || ''; } catch (e) {}

 function applyCols() {
   var q = COLS ? ('?cols=' + COLS) : '';
   var imgs = document.querySelectorAll('#g img[data-n]');
   for (var i = 0; i < imgs.length; i++) {
     imgs[i].src = imgs[i].getAttribute('data-n') + '.svg' + q;
   }
   var links = document.querySelectorAll('#g a[data-n]');
   for (var j = 0; j < links.length; j++) {
     links[j].href = links[j].getAttribute('data-n') + '.html' + q;
   }
 }

 function drawCols() {
   var box = document.getElementById('cols'),
       opts = ['', '2', '3', '4', '5', '6', '7', '8', '9'], h = '';
   for (var i = 0; i < opts.length; i++) {
     h += '<b data-v="' + opts[i] + '"' + (opts[i] === COLS ? ' class="on"' : '') + '>'
        + (opts[i] === '' ? 'авто' : opts[i]) + '</b>';
   }
   box.innerHTML = h;
   var btns = box.getElementsByTagName('b');
   for (var j = 0; j < btns.length; j++) {
     btns[j].onclick = function () {
       COLS = this.getAttribute('data-v');
       try { COLS ? localStorage.setItem(STORE, COLS) : localStorage.removeItem(STORE); }
       catch (e) {}
       drawCols(); applyCols();
     };
   }
 }
 drawCols(); applyCols();
</script>
</body></html>""" % {
        "count": len(rows) - hidden_count, "chan": nchan,
        "hist": html.escape(str(web.history.mode if web.history and web.history.mode else "—")),
        "mqtt": "" if online else '· <span class="off">нет связи с MQTT</span>',
        "toggle": toggle,
        "rows": "".join(rows) or "<i>в config.yaml нет ни одной панели</i>",
    }
    return Response(page, mimetype="text/html; charset=utf-8")


@app.route("/manifest.webmanifest")
def manifest():
    """
    Делает страницу устанавливаемой. После «Добавить на главный экран»
    она запускается без адресной строки и кнопок браузера - это и есть
    киоск без сторонних приложений.
    """
    body = {
        "name": "Панель дома",
        "short_name": "Дом",
        "start_url": "./",
        "scope": "./",
        "display": "standalone",
        "orientation": "any",
        "background_color": "#0F0F11",
        "theme_color": "#0F0F11",
        "icons": [{"src": "icon.svg", "sizes": "any", "type": "image/svg+xml"}],
    }
    return Response(json.dumps(body, ensure_ascii=False),
                    mimetype="application/manifest+json")


@app.route("/icon.svg")
def icon():
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 192 192">'
           '<rect width="192" height="192" rx="42" fill="#1B1B1F"/>'
           '<rect x="34" y="34" width="55" height="55" rx="14" fill="#E09B2D"/>'
           '<rect x="103" y="34" width="55" height="55" rx="14" fill="#4A4A50"/>'
           '<rect x="34" y="103" width="55" height="55" rx="14" fill="#4A4A50"/>'
           '<rect x="103" y="103" width="55" height="55" rx="14" fill="#3B8FD4"/>'
           '</svg>')
    return Response(svg, mimetype="image/svg+xml",
                    headers={"Cache-Control": "max-age=86400"})


@app.route("/docs")
def docs():
    """Справочник по сборке панелей. Лежит рядом с panel.py отдельным файлом."""
    for candidate in ("docs.html", "templates/docs.html"):
        path = os.path.join(BASE_DIR, candidate)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                return Response(fh.read(), mimetype="text/html; charset=utf-8")
    return Response("Файл docs.html не найден в %s" % BASE_DIR,
                    status=404, mimetype="text/plain; charset=utf-8")


# Обёртка страницы вынесена в templates/wrapper.html: восемьсот строк
# разметки со скриптами внутри .py мешали и читать, и править. Файл берём
# как обычный текст, а не через Jinja - в скриптах полно фигурных скобок,
# и шаблонизатор на них спотыкается. Подстановка нужна ровно в четырёх
# местах, для этого хватает replace.
_WRAPPER = {"mtime": 0.0, "text": ""}


def wrapper_html():
    """Читает обёртку с диска, перечитывая при изменении файла."""
    for base in TEMPLATE_DIRS:
        path = os.path.join(base, "wrapper.html")
        if not os.path.exists(path):
            continue
        mtime = os.path.getmtime(path)
        if mtime != _WRAPPER["mtime"]:
            with open(path, encoding="utf-8") as fh:
                _WRAPPER["text"] = fh.read()
            _WRAPPER["mtime"] = mtime
        return _WRAPPER["text"]
    raise RuntimeError("не найден templates/wrapper.html")


def _page_themes():
    """Темы для страницы панели. Черновик редактора в меню не попадает, но
    если его показывают (?theme=draft-…), странице нужны его цвета полей
    и пультов."""
    out = palette.menu()
    asked = request.args.get("theme") or ""
    if palette.is_draft(asked):
        out.append(palette.menu_entry(asked))
    return out


@app.route("/<name>.html")
def panel_html(name):
    """
    Страница панели для планшета.

    SVG не вставляется через <img>, а встраивается в разметку: только так
    по плиткам можно кликать. Скрипты внутри <img> браузер не выполняет,
    и обработчики на элементы повесить неоткуда.
    """
    web.config.reload()
    every = int(web.config.get("http", {}).get("refresh", 10))
    panel_conf = web.config.panels.get(name) or {}
    interactive = bool(panel_conf.get("interactive",
                       web.config.get("interactive", False))) or showcase.is_demo(name)

    html_page = wrapper_html()
    palette.refresh(0)
    for mark, value in (
            ("__NAME__", html.escape(name)),
            ("__NAME_JS__", json.dumps(name)),
            ("__EVERY__", str(every)),
            ("__INTERACTIVE__", "true" if interactive else "false"),
            # Ручки (1.51): отклик сразу и жесты на плитках - у панели или
            # общие; устройство может выбрать своё в меню шестерёнки.
            ("__CONTROLS__", json.dumps({
                "live": bool(panel_conf.get("live_controls", web.config.get("live_controls", False))),
                "drag": bool(panel_conf.get("tile_gestures", web.config.get("tile_gestures", False)))})),
            ("__APP_JS__", json.dumps(__version__)),
            ("__APP__", html.escape(__version__)),
            # Список тем для меню и цвета полей страницы - из данных тем,
            # а не из копии в скрипте страницы (с 1.24).
            ("__THEMES_JS__", json.dumps(_page_themes(), ensure_ascii=False))):
        html_page = html_page.replace(mark, value)

    # страница не кэшируется: после обновления панели должны прийти
    # новые скрипты, а не старые из кэша браузера
    return Response(html_page, mimetype="text/html; charset=utf-8",
                    headers={"Cache-Control": "no-cache, must-revalidate"})


@app.route("/themes")
def themes_page():
    """
    Витрина тем (с 1.28): все плитки и пульты на выдуманной панели в любой
    теме - сменить тему, ширину телефона или планшета, посмотреть все темы
    рядом. Основные цвета темы, наследование и ошибки файла - тут же.
    """
    web.config.reload()
    palette.refresh(0)
    menu = dict((m["name"], m) for m in palette.menu())
    data = {
        "themes": [dict(menu.get(n, {}), **palette.describe(n)) for n in palette.names()],
        "stray": palette.stray_problems(),
        "theme": themes.resolve(web.config.get("theme", "light"), web.config),
        "dir_name": web.config.get("themes_dir") or "themes",
    }
    page = ""
    for base in TEMPLATE_DIRS:
        path = os.path.join(base, "themes.html")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                page = fh.read()
            break
    # Названия тем пишет человек: «</script>» в названии не должно
    # закрыть скрипт страницы.
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return Response(page.replace("__DATA__", blob),
                    mimetype="text/html; charset=utf-8",
                    headers={"Cache-Control": "no-cache, must-revalidate"})


@app.route("/api/theme/draft", methods=["POST"])
def api_theme_draft():
    """
    Редактор тем (с 1.29): тема со страницы -> черновик в памяти. Ответ -
    имя черновика (его показывает витрина: ?theme=draft-…), сведения о
    нём (цвета, контраст, ошибки) и готовый файл темы для скачивания.
    На контроллер ничего не пишется: файл человек кладёт в themes/ сам.
    """
    if (request.content_length or 0) > 32 * 1024:
        return _json({"error": "слишком большой запрос"}, 413)
    try:
        body = json.loads(request.get_data(as_text=True) or "{}")
        palette.refresh()
        name, data = palette.draft(body.get("theme"))
    except (ValueError, TypeError, AttributeError) as exc:
        return _json({"error": str(exc)}, 400)
    info = dict(palette.menu_entry(name), **palette.describe(name))
    return _json({"draft": name, "info": info, "name": data["name"],
                  "filename": data["name"] + ".yaml",
                  "yaml": palette.to_yaml(data, time.strftime("%Y-%m-%d"))})


# какой тип плитки обычно подходит для типа канала Wiren Board
TILE_HINT = {
    "switch": "switch", "pushbutton": "switch", "alarm": "switch",
    "range": "light · curtain", "rgb": "—",
    "value": "chart · value", "temperature": "chart", "rel_humidity": "chart",
    "voltage": "chart", "current": "chart", "power": "chart",
    "power_consumption": "chart", "resistance": "chart", "pressure": "chart",
    "concentration": "chart", "lux": "chart", "sound_level": "chart",
    "text": "value",
}


@app.route("/channels")
def channels():
    """
    Браузер каналов. На контроллере их бывают сотни, поэтому:
      * сгруппированы по устройствам;
      * фильтр работает мгновенно, прямо в браузере;
      * отмечено, у каких каналов есть история — только по ним можно график;
      * имя копируется в буфер одной кнопкой, чтобы не набирать руками.

    curl -s localhost:8088/channels?format=text — то же самое для консоли.
    """
    query = request.args.get("q", "").strip().lower()
    with state.lock:
        values = dict(state.values)
        metas = dict(state.meta)
        stamps = dict(state.stamps)
    logged = web.history.channels_with_history() if web.history else {}
    used = web.config.used_channels()
    now = time.time()

    keys = sorted(values)
    if query:
        keys = [k for k in keys if query in k.lower()]

    if request.args.get("format") == "text":
        lines = ["%-54s %-14s %-16s %s" % ("КАНАЛ", "ЗНАЧЕНИЕ", "ТИП", "ИСТОРИЯ")]
        for key in keys:
            meta = metas.get(key, {})
            lines.append("%-54s %-14s %-16s %s" % (
                key, str(values.get(key, ""))[:14], meta.get("type", ""),
                "да" if key in logged else "—"))
        body = "\n".join(lines) + "\n\nвсего: %d\n" % len(keys)
        return Response(body, mimetype="text/plain; charset=utf-8")

    # --- группировка по устройству ---
    groups = {}
    for key in keys:
        device = key.split("/", 1)[0]
        groups.setdefault(device, []).append(key)

    esc = html.escape
    rows = []
    for device in sorted(groups):
        rows.append('<tr class="dev"><td colspan="5">%s <span class="n">%d</span></td></tr>'
                    % (esc(device), len(groups[device])))
        for key in groups[device]:
            meta = metas.get(key, {})
            ctype = str(meta.get("type", ""))
            units = str(meta.get("units", ""))
            value = str(values.get(key, ""))
            age = now - stamps.get(key, 0)
            hist = logged.get(key)
            rows.append(
                '<tr data-k="%s">'
                '<td class="k"><button onclick="cp(this)" data-c="%s">⧉</button>'
                '<code>%s</code>%s</td>'
                '<td class="v">%s <span class="u">%s</span></td>'
                '<td class="t">%s</td>'
                '<td class="h">%s</td>'
                '<td class="g">%s</td></tr>' % (
                    esc(key.lower()), esc(key), esc(key.split("/", 1)[1]),
                    ' <span class="used">в панели</span>' if key in used else "",
                    esc(value[:20]), esc(units),
                    esc(ctype),
                    ('<span class="ok">история%s</span>' %
                     ((" · %s" % hist) if hist else "")) if key in logged else "",
                    esc(TILE_HINT.get(ctype, "")),
                ))

    page = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Каналы контроллера</title><style>
 body{font:14px/1.45 -apple-system,'Segoe UI',Inter,system-ui,sans-serif;
      margin:0;padding:16px;background:#F1F1F3;color:#3A3A3E}
 h1{font-size:19px;margin:0 0 4px}
 .sub{color:#8A8A90;margin-bottom:14px}
 #q{width:100%%;max-width:520px;padding:10px 13px;font-size:15px;
    border:1.5px solid rgba(60,60,67,.2);border-radius:11px;background:#fff;
    box-sizing:border-box}
 table{border-collapse:collapse;width:100%%;margin-top:14px;background:#fff;
       border-radius:12px;overflow:hidden}
 td{padding:6px 12px;border-bottom:1px solid #EDEDF0;vertical-align:top}
 tr.dev td{background:#E6E6E9;font-weight:600;padding:9px 12px;
           position:sticky;top:0}
 tr.dev .n{color:#8A8A90;font-weight:400;margin-left:6px}
 code{font:13px ui-monospace,Menlo,Consolas,monospace}
 button{border:none;background:none;cursor:pointer;color:#8A8A90;
        font-size:14px;margin-right:7px;padding:0}
 button:hover{color:#E09B2D}
 .v{font-weight:500;white-space:nowrap}
 .u,.t,.g{color:#8A8A90;font-size:13px}
 .ok{color:#1F8F4E;font-size:12.5px}
 .used{background:#E09B2D;color:#fff;font-size:11px;padding:1px 6px;
       border-radius:6px;margin-left:7px}
 .hide{display:none}
</style></head><body>
<h1>Каналы контроллера</h1>
<div class="sub">Всего %(total)d. Скопируйте имя кнопкой ⧉ и вставьте в
<code>config.yaml</code>. График можно построить только по каналам с пометкой
«история» — остальные не пишутся в <code>wb-mqtt-db</code>.</div>
<input id="q" placeholder="фильтр: temp, curtain, wb-mr6c…" autofocus>
<table id="t">%(rows)s</table>
<script>
 var q=document.getElementById('q'), rs=document.querySelectorAll('#t tr');
 q.oninput=function(){var s=q.value.toLowerCase();
   rs.forEach(function(r){
     if(r.className==='dev'){r.classList.toggle('hide',s!=='');return;}
     r.classList.toggle('hide', s && r.dataset.k.indexOf(s)<0);});};
 function cp(b){navigator.clipboard.writeText(b.dataset.c);
   var o=b.textContent;b.textContent='✓';setTimeout(function(){b.textContent=o},900);}
</script></body></html>""" % {"total": len(keys), "rows": "".join(rows)}
    return Response(page, mimetype="text/html; charset=utf-8")


@app.route("/check")
def check_page():
    """
    Проверка конфига: что панель поняла, а что нет.

    Описана в 0.10.0, но маршрут потерялся при одной из сборок файлов -
    сама проверка в checkup.py была цела, а открыть её было негде.

    curl -s localhost:8088/check?format=text - то же для консоли.
    """
    web.config.reload()
    problems = checkup.check_config(web.config, state, web.history)
    if request.args.get("format") == "text":
        return Response(checkup.as_text(problems),
                        mimetype="text/plain; charset=utf-8")
    esc = html.escape
    total = checkup.summary(problems)
    rows = []
    for p in problems:
        rows.append(
            '<tr class="%s"><td class="lv">%s</td><td>%s</td>'
            '<td><b>%s</b> <span class="k">%s</span></td>'
            '<td>%s%s</td></tr>' % (
                "err" if p["level"] == checkup.ERROR else "warn",
                esc(p["level"]), esc(str(p["panel"])), esc(str(p["tile"])),
                esc(str(p["kind"])), esc(p["text"]),
                ('<div class="h">%s</div>' % esc(p["hint"])) if p["hint"] else ""))
    page = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Проверка конфига</title><style>
 body{font:14px/1.45 -apple-system,'Segoe UI',Inter,system-ui,sans-serif;
      margin:0;padding:16px;background:#F1F1F3;color:#3A3A3E}
 h1{font-size:19px;margin:0 0 4px}
 .sub{color:#8A8A90;margin-bottom:14px}
 table{border-collapse:collapse;width:100%%;background:#fff;border-radius:12px;
       overflow:hidden}
 td{padding:8px 12px;border-bottom:1px solid #EDEDF0;vertical-align:top}
 .lv{font-weight:600;white-space:nowrap}
 .err .lv{color:#C0392B} .warn .lv{color:#B8791A}
 .k{color:#8A8A90;font-size:12.5px}
 .h{color:#8A8A90;font-size:13px;margin-top:3px}
 .ok{background:#fff;border-radius:12px;padding:16px;color:#1F8F4E}
</style></head><body>
<h1>Проверка конфига</h1>
<div class="sub">Ошибок %(e)d, предупреждений %(w)d ·
<a href="check?format=text">текстом</a> · <a href="./">к панелям</a></div>
%(body)s
</body></html>""" % {
        "e": total["errors"], "w": total["warnings"],
        "body": ("<table>%s</table>" % "".join(rows)) if rows
                else '<div class="ok">Проблем не найдено.</div>'}
    return Response(page, mimetype="text/html; charset=utf-8")


@app.route("/stats")
def stats_page():
    """
    Счётчики демона для tools/watch-load.py: сколько отрисовок, ответов,
    запросов к истории и сколько процессора съел каждый поток. Значения
    накопительные с запуска - смотреть надо разницу между двумя чтениями.
    """
    body = stats.snapshot()
    body["version"] = state.version
    body["channels"] = len(state.values)
    return Response(json.dumps(body, ensure_ascii=False),
                    mimetype="application/json",
                    headers={"Cache-Control": "no-store"})


@app.route("/healthz")
def healthz():
    with state.lock:
        body = {"app": __version__,
                "mqtt": state.connected, "channels": len(state.values),
                "version": state.version, "panels": sorted(web.config.panels),
                "hidden": sorted(n for n, p in web.config.panels.items()
                                 if (p or {}).get("hidden")),
                "history": web.history.mode if web.history else None,
                "template_dirs": TEMPLATE_DIRS}
    return Response(json.dumps(body, ensure_ascii=False),
                    mimetype="application/json")
