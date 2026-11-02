# -*- coding: utf-8 -*-
"""
Демо-сайт своей панели: папка обычных файлов, которую можно выложить куда
угодно (артефакт Claude, GitHub Pages, любой веб-сервер). Сервер панели
работает в браузере посетителя (Pyodide), контроллер не нужен, в MQTT не
уходит ничего.

    python3 tools/demo-site/build.py <config.yaml> <папка> [панель] --pyodide <папка> [--theme cloud]

Из конфига берётся одна панель (по умолчанию all) с разделами, каналы
переименованы как у demo_panels (wbpanel/demo.py): имён контроллера,
адресов Sprut.hub, камер, календарей, паролей в сборке нет. Перед выдачей
то, что взято из конфига, проверяется шаблонами check-secrets.sh.

Pyodide 0.26.4 - из npm: `npm pack pyodide@0.26.4`, распаковать, путь к
папке package - в --pyodide. Архивов в сборке нет (не всякий хостинг их
отдаёт): исходники Python - текстом в py/files.json, библиотеку Python
страница сама складывает в zip в памяти (worker.js).
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

#: Чистый Python, который нужен серверу панели в браузере.
LIBS = ("flask", "werkzeug", "jinja2", "markupsafe", "itsdangerous", "click", "blinker",
        "yaml", "paho")
DISTS = ("flask", "werkzeug", "jinja2", "markupsafe", "itsdangerous", "click", "blinker",
         "pyyaml", "paho-mqtt")
PYODIDE = ("pyodide.js", "pyodide.asm.js", "pyodide.asm.wasm", "pyodide-lock.json")
#: Что сервер панели берёт из стандартной библиотеки (снято трассировкой
#: всех адресов в Pyodide; пакеты - целиком, с запасом).
STDLIB = """__future__ _compat_pickle _compression _markupbase _pyodide _strptime
_sysconfigdata__emscripten_wasm32-emscripten _threading_local _weakrefset ast asyncio base64
bdb bisect bz2 calendar cmd code codeop collections colorsys concurrent contextlib contextvars
copy copyreg csv dataclasses datetime decimal difflib dis email encodings enum fnmatch
fractions functools gettext glob gzip hashlib heapq hmac html http importlib inspect ipaddress
json keyword linecache locale logging mimetypes numbers opcode operator pathlib pdb pickle
pkgutil platform pprint pyodide queue quopri random re reprlib secrets selectors shlex shutil
signal socket socketserver statistics string stringprep struct subprocess sysconfig tempfile
textwrap threading token tokenize traceback types typing urllib uuid warnings weakref zipfile
zoneinfo""".split()
SECRETS = (r"wirenboard-[A-Za-z0-9]{6,}", r"wirenboard\.cloud", r"Sprut\.hub-[A-F0-9]{8,}",
           r"192\.168\.[0-9]{1,3}\.[0-9]{1,3}", r"(password|passwd|token|api[_-]?key|secret)\s*[:=]",
           r"rtsp://", r"\.ics\b", r"calendar\.google", r"https?://")


def demo_config(config_path, panel, theme=None):
    """Конфиг сборки: одна панель с каналами демо и ничего больше."""
    import yaml
    from wbpanel import demo
    from wbpanel.config import Config
    from wbpanel import yamlx
    # в демо (публичная страница) секретов нет: !secret - пустая строка (1.61)
    yamlx.use_secrets = False
    yamlx.extra_dir = os.path.dirname(os.path.abspath(config_path))
    src = open(config_path, encoding="utf-8").read()
    tmp = tempfile.mkdtemp(prefix="wbdemo-")
    path = os.path.join(tmp, "config.yaml")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("demo_panels: [%s]\n" % panel + src)
    cfg = Config(path)
    built = demo.build(cfg, demo.NAME_PREFIX + panel)
    shutil.rmtree(tmp, ignore_errors=True)
    if built is None:
        sys.exit("панели %r нет в конфиге" % panel)
    conf = built[0]
    conf["title"] = str(conf.get("title", "")).replace(" · демо", "")
    # чипы протечек смотрят в модуль воды - его в демо нет
    for holder in [conf] + list(conf.get("sections") or []):
        if holder.get("status"):
            holder["status"] = [c for c in holder["status"]
                                if not (isinstance(c, dict) and "leak" in c)]
    real = yaml.safe_load(src)
    out = {"theme": theme or real.get("theme", "light"), "interactive": True,
           "show_status": real.get("show_status", False),
           "live_controls": True, "tile_gestures": True,
           "panels": {"dom": conf}}
    return yaml.safe_dump(out, allow_unicode=True, sort_keys=False)


def tracked(*paths):
    out = subprocess.check_output(["git", "-c", "core.quotepath=off", "ls-files"] + list(paths),
                                  cwd=ROOT, text=True)
    return [p for p in out.split("\n") if p]


def app_files(config_text):
    """Сервер панели для браузера: путь -> текст (wbpanel, шаблоны, библиотеки)."""
    out = {}
    for f in tracked("wbpanel", "templates", "themes") + ["tests/fixtures/env.py",
                                                          "tests/fixtures/state.py"]:
        out[f] = open(os.path.join(ROOT, f), encoding="utf-8").read()
    out["boot.py"] = open(os.path.join(HERE, "boot.py"), encoding="utf-8").read()
    out["config.yaml"] = config_text
    out["stubs/sqlite3.py"] = "# в браузере базы истории нет\n"
    for name in LIBS:
        base = os.path.dirname(__import__(name).__file__)
        top = os.path.dirname(base)
        for dirpath, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for fn in files:
                if fn.endswith(".py"):
                    full = os.path.join(dirpath, fn)
                    out["lib/" + os.path.relpath(full, top)] = open(full, encoding="utf-8").read()
    # версии пакетов: Flask спрашивает версию werkzeug у метаданных
    import importlib.metadata as md
    for dist in DISTS:
        d = md.distribution(dist)
        out["lib/%s-%s.dist-info/METADATA" % (d.metadata["Name"].replace("-", "_"), d.version)] = \
            "Metadata-Version: 2.1\nName: %s\nVersion: %s\n" % (d.metadata["Name"], d.version)
    return out


def stdlib_files(pyodide):
    """Нужная часть стандартной библиотеки Pyodide: путь -> текст."""
    out = {}
    with zipfile.ZipFile(os.path.join(pyodide, "python_stdlib.zip")) as z:
        for n in z.namelist():
            top = n.split("/")[0]
            if n.endswith("/") or (top[:-3] if top.endswith(".py") else top) not in STDLIB:
                continue
            out[n] = z.read(n).decode("utf-8")
    return out


#: Шторка конструктора темы (lab.js): снизу, на полэкрана; кнопка над вкладками.
LAB_CSS = (
    '#lab-btn{position:fixed;left:max(12px,env(safe-area-inset-left,0px));'
    'bottom:calc(78px + env(safe-area-inset-bottom,0px));z-index:60;border:0;border-radius:18px;'
    'padding:9px 14px;font:600 13px system-ui,sans-serif;background:#1B2233;color:#fff;'
    'box-shadow:0 4px 14px rgba(27,34,51,.3)}'
    '#lab{display:none;position:fixed;left:0;right:0;bottom:0;height:48vh;z-index:59;overflow-y:auto;'
    'background:#fff;color:#1B2233;border-radius:20px 20px 0 0;box-shadow:0 -8px 30px rgba(27,34,51,.25);'
    'font:14px/1.35 system-ui,sans-serif;-webkit-overflow-scrolling:touch;touch-action:pan-y}'
    'html.lab-open #lab{display:block}html.lab-open #lab-btn{bottom:calc(48vh + 10px)}'
    '.lab-body{padding:6px max(16px,env(safe-area-inset-right,0px)) calc(24px + env(safe-area-inset-bottom,0px)) '
    'max(16px,env(safe-area-inset-left,0px));max-width:560px;margin:0 auto}'
    '#lab h4{margin:16px 0 8px;font-size:15px}'
    '#lab-curve{width:100%;height:auto;display:block;background:#F1F3FC;border-radius:12px}'
    '.lab-note{color:#7B8299;font-size:12px;margin:6px 0 8px}'
    '.lab-row{display:grid;grid-template-columns:118px 1fr auto;gap:10px;align-items:center;margin:6px 0}'
    '.lab-row span{color:#4A5068;font-size:13px}.lab-row b{font-weight:600;font-size:13px;min-width:40px;text-align:right}'
    '.lab-cols{display:flex;gap:10px;align-items:center;margin:8px 0}.lab-cols span{width:118px;color:#4A5068;font-size:13px}'
    '.lab-cols input[type=color]{width:44px;height:30px;border:0;padding:0;background:none}'
    '.lab-stop{display:grid;grid-template-columns:44px 1fr 1fr 28px;gap:8px;align-items:center;'
    'padding:6px 0;border-top:1px solid #EDF0F7}'
    '.lab-stop input[type=color]{width:40px;height:30px;border:0;padding:0;background:none}'
    '.lab-stop label{display:grid;grid-template-columns:1fr auto;align-items:center;font-size:11px;color:#7B8299}'
    '.lab-stop label input{grid-column:1/3;width:100%}.lab-stop b{font-size:12px;color:#1B2233}'
    '.lab-stop button{border:0;background:#F1F3FC;border-radius:50%;width:28px;height:28px;font-size:18px;color:#4A5068}'
    '#lab input[type=range]{width:100%;accent-color:#2F7CF6}'
    '.lab-seg{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0}'
    '.lab-seg i{font-style:normal;padding:6px 11px;border-radius:14px;background:#F1F3FC;color:#4A5068;font-size:13px}'
    '.lab-seg i.on{background:#2F7CF6;color:#fff}'
    '.lab-btn{border:0;border-radius:14px;padding:8px 13px;background:#F1F3FC;color:#1B2233;font:600 13px system-ui,sans-serif;margin:6px 6px 0 0}'
    '.lab-btn.main{background:#2F7CF6;color:#fff}.lab-acts{margin-top:14px}'
    '#lab-yaml{white-space:pre-wrap;word-break:break-word;background:#F1F3FC;border-radius:10px;padding:10px;font-size:11px;user-select:text}'
)


def page(files, title):
    """index.html: страница панели (wrapper.html) + shim.js первым скриптом."""
    sys.path.insert(0, HERE)
    import boot
    tmp = tempfile.mkdtemp(prefix="wbdemo-")
    try:
        root = os.path.join(tmp, "app")
        for path, text in files.items():
            full = os.path.join(root, path)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf-8") as fh:
                fh.write(text)
        name = boot.start(root)
        status, _headers, html = boot.handle("GET", "/%s.html" % name)
        assert status == 200, html[:300]
    finally:
        os.chdir(ROOT)
        shutil.rmtree(tmp, ignore_errors=True)
    body = html.split("</head><body>", 1)[1].replace("</body></html>", "")
    head = ('<title>__TITLE__</title>\n<meta name="theme-color" content="#0F0F11">\n'
            '<link rel="stylesheet" href="static/panel.css">\n'
            # «назад» к списку панелей и витрина тем ведут за пределы демо
            '<style>#back,#navmenu [data-go]{display:none!important}'
            '[data-nav="back"]{visibility:hidden;pointer-events:none}'
            '.demo-boot{display:flex;flex-direction:column;align-items:center;gap:10px;'
            'padding:22vh 24px 0;text-align:center;font:15px/1.4 system-ui,sans-serif;color:#8a8580}'
            '.demo-boot b{font-size:18px;color:#d9d3cc}.demo-boot span{max-width:320px}'
            '.demo-ring{width:34px;height:34px;border-radius:50%;border:3px solid #8a858055;'
            'border-top-color:#d9a65a;animation:demo-spin 1s linear infinite}'
            '@keyframes demo-spin{to{transform:rotate(360deg)}}'
            '@media (prefers-reduced-motion:reduce){.demo-ring{animation:none}}' + LAB_CSS + '</style>\n'
            '<script src="static/shim.js"></script>\n').replace("__TITLE__", title)
    body = re.sub(r'\?v=[^"]*"', '"', body)
    # конструктор темы - последним скриптом, после update.js
    body = body.replace('<script src="static/js/update.js"></script>',
                        '<script src="static/js/update.js"></script>\n<script src="static/lab.js"></script>')
    return head + body


def build(config_path, out, panel="all", pyodide=None, title="Демо умного дома", theme=None):
    if not pyodide:
        sys.exit("нужен --pyodide <папка package из npm pack pyodide@0.26.4>")
    config_text = demo_config(config_path, panel, theme)
    app = app_files(config_text)
    out = os.path.abspath(out)
    if os.path.exists(out):
        shutil.rmtree(out)
    os.makedirs(os.path.join(out, "static", "js"))
    os.makedirs(os.path.join(out, "py"))
    with open(os.path.join(out, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(page(app, title))
    shutil.copy(os.path.join(ROOT, "static", "panel.css"), os.path.join(out, "static"))
    for f in tracked("static/js"):
        shutil.copy(os.path.join(ROOT, f), os.path.join(out, "static", "js"))
    shutil.copy(os.path.join(HERE, "shim.js"), os.path.join(out, "static"))
    shutil.copy(os.path.join(HERE, "lab.js"), os.path.join(out, "static"))
    shutil.copy(os.path.join(HERE, "worker.js"), os.path.join(out, "py"))
    text = json.dumps({"stdlib": stdlib_files(pyodide), "app": app}, ensure_ascii=False,
                      separators=(",", ":"))
    # U+FFFD в исходниках (click) - escape-последовательностью: хостинг
    # принимает его за битую кодировку
    with open(os.path.join(out, "py", "files.json"), "w", encoding="utf-8") as fh:
        fh.write(text.replace("\ufffd", "\\ufffd"))
    for f in PYODIDE:
        shutil.copy(os.path.join(pyodide, f), os.path.join(out, "py"))
    check(out, config_text)
    return out


def check(out, config_text):
    """Личного в сборке быть не должно: те же шаблоны, что check-secrets.sh.
    Код из репозитория и так публичный - смотрим то, что взято из конфига:
    config.yaml сборки и страницу."""
    texts = [("config.yaml", config_text),
             ("index.html", open(os.path.join(out, "index.html"), encoding="utf-8").read())]
    bad = []
    for name, text in texts:
        for pat in SECRETS:
            for m in re.finditer(pat, text):
                bad.append("%s: %s" % (name, m.group(0)))
    if bad:
        shutil.rmtree(out, ignore_errors=True)
        sys.exit("в сборке личное - сборка удалена:\n  " + "\n  ".join(bad[:20]))


if __name__ == "__main__":
    args = list(sys.argv[1:])
    pyo = theme = None
    if "--pyodide" in args:
        i = args.index("--pyodide")
        pyo = args[i + 1]
        del args[i:i + 2]
    if "--theme" in args:
        i = args.index("--theme")
        theme = args[i + 1]
        del args[i:i + 2]
    if len(args) < 2:
        sys.exit(__doc__)
    print(build(args[0], args[1], args[2] if len(args) > 2 else "all", pyo, theme=theme))
