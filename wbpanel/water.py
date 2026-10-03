# -*- coding: utf-8 -*-
"""
Водоснабжение квартиры: краны, датчики протечки и модули, которые ими
управляют.

Описывается один раз, в разделе water: config.yaml, а плитки и чипы
ссылаются на него по имени. Иначе одно и то же - какой датчик какую трубу
перекрывает - пришлось бы повторять в каждой плитке, и при первой же
правке одно место отстало бы от другого.

    water:
      modules:                    # необязательно: общий флаг и сброс
        mwac:
          alarm: "wb-mwac-v2_131/Leakage Mode"
          reset: "wb-mwac-v2_131/Leakage Mode Reset"
      valves:
        cold: {title: "Холодная", channel: "wb-mwac-v2_131/Output K1", water: cold, module: mwac}
        hot:  {title: "Горячая",  channel: "wb-mwac-v2_131/Output K2", water: hot,  module: mwac}
      sensors:
        bath:    {title: "Ванная", channel: "wb-mwac-v2_131/Input F1", closes: [cold, hot], module: mwac}
        kitchen: {title: "Кухня",  channel: "wb-mwac-v2_131/Input F2", closes: [cold, hot], module: mwac}

closes - какие краны перекрывает этот датчик. Сам перекрывает модуль, по
своей матрице действий; панель только знает об этом, чтобы показать, кто
и что перекрыл. В квартире со своими стояками у кухни и у ванной краны
разные, и тогда «Перекрыт: протечка — Кухня» на кране кухни и спокойный
кран ванной говорят больше, чем общая тревога на всю квартиру.

Модулей сколько угодно: у каждого свой флаг тревоги и свой сброс.
"""

from .state import is_on


def _plural(n, one, few, many):
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def command_of(channel):
    """Канал Wiren Board -> топик команды. Чужой топик - /set, как у моста."""
    if not channel:
        return None
    if channel.count("/") == 1:
        device, control = channel.split("/", 1)
        return "/devices/%s/controls/%s/on" % (device, control)
    return None


class Water(object):
    def __init__(self):
        self.config = None

    def configure(self, config):
        self.config = config

    # ------------------------------------------------------------------
    #  описание
    # ------------------------------------------------------------------

    def _section(self, key):
        raw = ((self.config.get("water") if self.config else None) or {})
        part = raw.get(key) if isinstance(raw, dict) else None
        if not isinstance(part, dict):
            return {}
        return {str(k): (v if isinstance(v, dict) else {}) for k, v in part.items()}

    def modules(self):
        return self._section("modules")

    def valves(self):
        return self._section("valves")

    def sensors(self):
        out = {}
        for name, conf in self._section("sensors").items():
            item = dict(conf)
            closes = item.get("closes") or []
            item["closes"] = [str(c) for c in (closes if isinstance(closes, list)
                                               else [closes])]
            item["name"] = name
            out[name] = item
        return out

    def defined(self):
        return bool(self.sensors() or self.valves())

    def pick_sensors(self, spec, inline_channel=None):
        """
        Датчики для плитки или чипа.

        spec - all (или не задано), имя, список имён. Для совместимости со
        старой записью элемент списка может быть каналом (в нём есть «/»)
        или словарём {channel, title}: такие датчики ничего не перекрывают
        - о трубах знает только раздел water.
        """
        known = self.sensors()
        if spec in (None, "", "all"):
            if inline_channel:
                return [{"name": "", "title": "", "channel": str(inline_channel),
                         "closes": [], "module": None}]
            return list(known.values())
        items = spec if isinstance(spec, list) else [spec]
        out = []
        for item in items:
            if isinstance(item, dict) and item.get("channel"):
                out.append({"name": "", "title": str(item.get("title") or ""),
                            "channel": str(item["channel"]), "closes": [],
                            "module": item.get("module")})
            elif isinstance(item, str) and item in known:
                out.append(known[item])
            elif isinstance(item, str) and "/" in item:
                out.append({"name": "", "title": "", "channel": item,
                            "closes": [], "module": None})
        return out

    # ------------------------------------------------------------------
    #  состояние
    # ------------------------------------------------------------------

    def channels(self, sensors):
        """Что читать ради этих датчиков: сами датчики и флаги их модулей."""
        out = set(s["channel"] for s in sensors if s.get("channel"))
        mods = self.modules()
        for s in sensors:
            m = mods.get(str(s.get("module") or ""))
            if m and m.get("alarm"):
                out.add(str(m["alarm"]))
        return out

    def resets(self, sensors):
        """Топики сброса тревоги модулей этих датчиков, без повторов."""
        mods, out = self.modules(), []
        for s in sensors:
            m = mods.get(str(s.get("module") or ""))
            cmd = command_of(str(m.get("reset"))) if m and m.get("reset") else None
            if cmd and cmd not in out:
                out.append(cmd)
        return out

    def all_resets(self):
        out = []
        for m in self.modules().values():
            cmd = command_of(str(m.get("reset") or ""))
            if cmd and cmd not in out:
                out.append(cmd)
        return out

    def leak(self, state, sensors, snaps=None, extra_alarm=False):
        """
        Сводка по датчикам: что мокрое, держит ли модуль тревогу, что
        написать. snaps - куда сложить снимки каналов (для отметки «нет
        данных» у плитки).
        """
        valves = self.valves()
        rows, wet, known = [], [], 0
        for n, s in enumerate(sensors):
            snap = state.snapshot(s["channel"])
            if snaps is not None:
                snaps.append(snap)
            title = s.get("title") or ("Датчик %d" % (n + 1))
            if snap["known"]:
                known += 1
                on = is_on(snap["raw"])
            else:
                on = None
            if on:
                wet.append(title)
            closes = [str((valves.get(v) or {}).get("title") or v)
                      for v in s.get("closes") or []]
            rows.append({"title": title, "on": on, "ts": snap["ts"],
                         "closes": closes})

        latched = bool(extra_alarm)
        mods = self.modules()
        seen = set()
        for s in sensors:
            name = str(s.get("module") or "")
            if name in seen or name not in mods:
                continue
            seen.add(name)
            ch = mods[name].get("alarm")
            if ch:
                snap = state.snapshot(str(ch))
                if snaps is not None:
                    snaps.append(snap)
                latched = latched or is_on(snap["raw"])

        count = len(rows)
        if wet and count == 1:
            status = "Протечка"
        elif wet:
            status = "Протечка: " + ", ".join(wet)
        elif latched:
            status = "Сухо · сбросьте"
        elif rows and not known:
            status = "нет данных"
        elif count > 1:
            status = "Сухо · %d %s" % (count, _plural(count, "датчик",
                                                      "датчика", "датчиков"))
        else:
            status = "Сухо"
        # Мелкой плитке (0.5 x 0.5) подписи с именами датчиков не влезают:
        # там одно слово вместо названия - значок капли и так говорит, что
        # это протечка.
        if wet:
            short = "Протечка"
        elif latched:
            short = "Сбросьте"
        elif rows and not known:
            short = "Нет данных"
        else:
            short = "Сухо"
        return {"rows": rows, "wet": wet, "latched": latched,
                "alarm": bool(wet) or latched, "status": status,
                "status_s": short,
                "count": count, "known": known}

    def valve_open(self, state, name):
        """Открыт ли кран: True / False / None, если выход ещё не известен.
        invert - как у плитки крана."""
        conf = self.valves().get(name) or {}
        ch = conf.get("channel")
        if not ch:
            return None
        raw = state.snapshot(str(ch))["raw"]
        if raw is None or str(raw).strip() == "":
            return None
        opened = is_on(raw)
        return (not opened) if conf.get("invert") else opened

    def valves_of(self, sensors):
        """Краны, которые перекрывают эти датчики, в порядке описания."""
        names = set()
        for s in sensors:
            names.update(s.get("closes") or [])
        return [n for n in self.valves() if n in names]

    def water_status(self, state, sensors):
        """
        Что с водой, одной фразой для спокойной плитки протечки: «вода
        открыта», «вода перекрыта», «перекрыта: Горячая». None - кранов
        нет или их состояние неизвестно.
        """
        names = self.valves_of(sensors)
        known = [(n, self.valve_open(state, n)) for n in names]
        known = [(n, o) for n, o in known if o is not None]
        if not known:
            return None
        closed = [str((self.valves().get(n) or {}).get("title") or n)
                  for n, o in known if not o]
        if not closed:
            return "вода открыта"
        if len(closed) == len(known):
            return "вода перекрыта"
        return "перекрыта: " + ", ".join(closed)

    def channels_with_valves(self, sensors):
        """Каналы датчиков, флагов модулей и кранов, которые они перекрывают."""
        out = self.channels(sensors)
        valves = self.valves()
        for n in self.valves_of(sensors):
            ch = (valves.get(n) or {}).get("channel")
            if ch:
                out.add(str(ch))
        return out

    def closers(self, state, valve_name):
        """
        Кто держит кран перекрытым: мокрые датчики, которые его перекрывают,
        и флаг тревоги его модуля. -> (имена датчиков, тревога модуля)
        """
        wet = []
        for s in self.sensors().values():
            if valve_name not in s.get("closes", []):
                continue
            if is_on(state.snapshot(s["channel"])["raw"]):
                wet.append(s.get("title") or s["name"])
        valve = self.valves().get(valve_name) or {}
        mod = self.modules().get(str(valve.get("module") or "")) or {}
        latched = bool(mod.get("alarm")) and is_on(
            state.snapshot(str(mod["alarm"]))["raw"])
        return wet, latched


def rows_attr(rows):
    """Строки окна протечки в один атрибут: имя|состояние|время|перекрывает."""
    def flat(text):
        return str(text).replace("|", " ").replace(";", ",")
    return ";".join(
        "%s|%s|%d|%s" % (flat(r["title"]),
                         "?" if r["on"] is None else ("1" if r["on"] else "0"),
                         int(r["ts"] or 0),
                         flat(", ".join(r.get("closes") or [])))
        for r in rows)


water = Water()
