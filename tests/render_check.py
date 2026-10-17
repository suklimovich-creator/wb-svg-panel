# -*- coding: utf-8 -*-
"""
Проверка рендера: каждая панель tests/fixtures/showcase.yaml во всех
встроенных темах, с подстановкой переменных и без, с кнопками шапки и без,
плюс пульты на весь экран. Без MQTT, без сети, без браузера - только
сервер в этом же процессе на выдуманных приборах (fixtures/state.py).

    python3 tests/render_check.py            # проверить
    python3 tests/render_check.py --update   # принять изменения вида
    python3 tests/render_check.py --out DIR  # заодно сложить SVG в DIR
    python3 tests/render_check.py --soft     # отличия от golden.json - не ошибка

Что проверяется:
  * панель собирается без исключений и разбирается как XML;
  * в <style> нет угловых скобок (иначе панель не открывается картинкой);
  * плоская подстановка (?flat=1) не оставила var(--…) и не наткнулась на
    переменную, которой нет в палитре (она стала бы inherit - чёрной);
  * отпечаток каждой картинки совпадает с tests/golden.json. Время
    заморожено, поэтому изменение отпечатка - это изменение вида или
    разметки. Ожидаемое - принять ключом --update и закоммитить
    golden.json вместе с правкой; неожиданное - смотреть картинки
    (tools/visual/README.md).

Нужны только зависимости самой панели (flask, jinja2, pyyaml, paho-mqtt).
"""

import hashlib
import json
import os
import re
import sys
import time as _time
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FIXTURES = os.path.join(HERE, "fixtures")
GOLDEN = os.path.join(HERE, "golden.json")

#: Пульты на весь экран: (панель, номер плитки, ширина, высота).
PADS = (("ac_light", 0, 390, 844), ("fcs", 0, 390, 844), ("gal", 2, 390, 844))


def setup():
    sys.path.insert(0, FIXTURES)
    import env  # noqa: E402  (fixtures/env.py)
    web, palette, cfg, _sent = env.setup(freeze_time=True)
    return web, palette, cfg


VAR_RE = re.compile(r"var\((--[\w-]+)\)")
STYLE_RE = re.compile(r"<style>(.*?)</style>", re.S)


def check_svg(case, svg, flat, known_vars):
    problems = []
    try:
        ET.fromstring(svg.encode("utf-8"))
    except ET.ParseError as exc:
        problems.append("не XML: %s" % exc)
    for style in STYLE_RE.findall(svg):
        if "<" in style or ">" in style:
            problems.append("угловая скобка в <style>")
            break
    if flat:
        left = sorted(set(VAR_RE.findall(svg)))
        if left:
            problems.append("после подстановки остались: %s" % ", ".join(left[:6]))
    else:
        unknown = sorted(set(VAR_RE.findall(svg)) - known_vars)
        if unknown:
            problems.append("переменных нет в палитре (при ?flat=1 станут inherit): %s"
                            % ", ".join(unknown[:6]))
    return ["%s: %s" % (case, p) for p in problems]


def fingerprint(svg):
    return hashlib.sha1(svg.encode("utf-8")).hexdigest()[:16]


def main(argv):
    update = "--update" in argv
    # В CI: другая версия Jinja2 может расставить пробелы иначе - отпечатки
    # там только для сведения, ошибкой считаются лишь поломки.
    soft = "--soft" in argv
    out = None
    if "--out" in argv:
        out = argv[argv.index("--out") + 1]
        os.makedirs(out, exist_ok=True)

    web, palette, cfg = setup()
    themes = palette.names()
    known = set()
    for name in themes:
        known.update(palette.css_vars(name))
    known.add("--fs")

    prints, problems = {}, []
    t0 = _time.perf_counter() if hasattr(_time, "perf_counter") else 0

    for panel in cfg.panels:
        for theme in themes:
            for flat in (False, True):
                for nav in ("", "1.4"):
                    if flat and nav:
                        continue        # кнопки шапки - только у страницы, она не плоская
                    q = "/%s.svg?theme=%s%s" % (panel, theme, "&nav=" + nav if nav else "")
                    case = "%s-%s%s%s" % (panel, theme, "-flat" if flat else "", "-nav" if nav else "")
                    try:
                        with web.app.test_request_context(q):
                            svg = web.render_panel(panel)
                            if flat:
                                svg = web.resolve_css_vars(svg, web.panel_theme(cfg.panels[panel]), 1.0)
                    except Exception as exc:  # noqa: BLE001 - отчёт, а не падение
                        problems.append("%s: исключение %s: %s" % (case, type(exc).__name__, exc))
                        continue
                    problems += check_svg(case, svg, flat, known)
                    prints[case] = fingerprint(svg)
                    if out:
                        with open(os.path.join(out, case + ".svg"), "w", encoding="utf-8") as fh:
                            fh.write(svg)

    for panel, idx, w, h in PADS:
        if panel not in cfg.panels:
            continue
        for theme in themes:
            case = "pad-%s%d-%s" % (panel, idx, theme)
            q = "/tile/%s/%d.svg?w=%d&h=%d&view=pad&theme=%s" % (panel, idx, w, h, theme)
            try:
                with web.app.test_request_context(q):
                    svg = web.tile_svg(panel, idx).get_data(as_text=True)
            except Exception as exc:  # noqa: BLE001
                problems.append("%s: исключение %s: %s" % (case, type(exc).__name__, exc))
                continue
            problems += check_svg(case, svg, False, known)
            prints[case] = fingerprint(svg)
            if out:
                with open(os.path.join(out, case + ".svg"), "w", encoding="utf-8") as fh:
                    fh.write(svg)

    took = (_time.perf_counter() - t0) if t0 else 0
    print("картинок: %d, тем: %d, %.1f с" % (len(prints), len(themes), took))

    if problems:
        print("\nОШИБКИ (%d):" % len(problems))
        for p in problems[:60]:
            print("  " + p)
        if len(problems) > 60:
            print("  … и ещё %d" % (len(problems) - 60))

    golden = {}
    if os.path.exists(GOLDEN):
        golden = json.load(open(GOLDEN, encoding="utf-8"))
    changed = sorted(k for k in prints if golden.get(k) not in (None, prints[k]))
    added = sorted(k for k in prints if k not in golden)
    gone = sorted(k for k in golden if k not in prints)

    if update:
        with open(GOLDEN, "w", encoding="utf-8") as fh:
            json.dump(prints, fh, ensure_ascii=False, indent=0, sort_keys=True)
            fh.write("\n")
        print("golden.json обновлён: изменилось %d, новых %d, ушло %d"
              % (len(changed), len(added), len(gone)))
    elif golden:
        if changed or added or gone:
            print("\nВид изменился относительно tests/golden.json:")
            for k in changed[:40]:
                print("  ~ " + k)
            if len(changed) > 40:
                print("  … и ещё %d" % (len(changed) - 40))
            for k in added[:10]:
                print("  + " + k)
            for k in gone[:10]:
                print("  - " + k)
            print("Если так и задумано: python3 tests/render_check.py --update")
        else:
            print("вид не изменился")
    strict = golden and not update and not soft
    return 1 if problems or (strict and (changed or added or gone)) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
