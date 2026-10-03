# -*- coding: utf-8 -*-
"""
Вода: датчики протечки и краны.

Обе плитки - не выключатели, хотя каналы у них дискретные. Протечка - это
тревога, а не состояние прибора, и рисоваться должна так, чтобы её видели
с другого конца коридора. Кран - выключатель, промах по которому оставляет
квартиру без воды, поэтому короткое нажатие у него ничего не переключает,
а открывает окно с одной большой кнопкой.

Краны, датчики и модули описываются один раз в разделе water: config.yaml
(см. water.py), а плитки ссылаются на них по имени:

    - type: leak                 # все датчики раздела water
    - type: leak
      sensors: [bath, kitchen]   # или только эти
    - type: valve
      valve: cold

Старая запись - каналы прямо в плитке - работает по-прежнему, только о
том, какой датчик какую трубу перекрывает, панель тогда не знает.

Под WB-MWAC v2 (имена каналов из шаблона wb-mqtt-serial):

    Input F1 … Input F5, Input S6   входы датчиков, 1 - вода
    Leakage Mode                    общий флаг, держится до сброса
    Leakage Mode Reset              сброс (у прошивок ver2)
    Output K1, Output K2            краны

По умолчанию модуль при протечке выключает выходы, то есть 0 на выходе -
кран перекрыт. Подключили наоборот - invert: true у крана.
"""

from .const import CELL
from .geometry import text_width
from .registry import Tile, Zone, tile
from .state import is_on
from .water import command_of, rows_attr, water

WATER_COLOR = {"cold": "#3B8FD4", "hot": "#D9433C"}


def _sensors(conf):
    return water.pick_sensors(conf.get("sensors"), conf.get("channel"))


def _chips(rows, ctx):
    """
    Датчики на крупной плитке: точка и имя, в строку с переносом. Пустая
    плитка 2 x 1 с одной подписью внизу не говорила, какие датчики за ней
    стоят и какой из них намок. Сухой - пустой кружок, мокрый - залитый,
    без данных - бледный.

    Помещается, сколько помещается между значком и подписью; не влезло -
    последним идёт «+N».
    """
    w = float(ctx.opt("inner_w") or 0)
    h = float(ctx.opt("inner_h") or 0)
    if w < CELL * 2 or h < 117 or not rows:
        return []
    fs = float(ctx.opt("fs") or 1.0)
    size = round(14 * fs, 1)
    r = round(4.5 * fs, 1)
    gap = 16 * fs
    line = 24 * fs
    y = 72 * fs
    last = h - 62 * fs              # ниже - название плитки
    if y > last:
        return []
    left, right = 20.0, w - 20.0
    x = left
    out = []
    for n, row in enumerate(rows):
        text = row["title"]
        # Мокрый пишется полужирным - он шире.
        width = 2 * r + 7 + text_width(text, size, 0.68 if row["on"] else 0.6)
        if x > left and x + width > right:
            x, y = left, y + line
            if y > last:
                # Не влезло: последний показанный уступает место «+N».
                rest = len(rows) - n
                spot = out.pop()
                out.append({"x": spot["x"], "y": spot["y"], "size": size,
                            "r": r, "more": "+%d" % (rest + 1)})
                return out
        state = "off" if row["on"] is None else ("wet" if row["on"] else "dry")
        out.append({"x": round(x, 1), "y": round(y, 1), "text": text,
                    "state": state, "size": size, "r": r,
                    "tx": round(x + 2 * r + 7, 1),
                    "cy": round(y - size * 0.35, 1), "cx": round(x + r, 1)})
        x += width + gap
    return out


@tile("leak")
class Leak(Tile):
    """
    Датчики протечки одной плиткой.

        - type: leak
          title: "Протечки"
          sensors: [bath, kitchen]     # имена из water.sensors; без поля - все

    Старая запись тоже годится:

        - type: leak
          sensors:
            - {channel: "wb-mwac-v2_131/Input F1", title: "Ванная"}
          channel_alarm: "wb-mwac-v2_131/Leakage Mode"
          channel_reset: "wb-mwac-v2_131/Leakage Mode Reset"

    В покое плитка спокойная: «Сухо · 4 датчика». Сработал датчик - красная
    во всю плитку, и в подписи - какой. Модуль держит тревогу и после того,
    как вода высохла, пока её не сбросят: тогда плитка остаётся красной с
    подписью «сухо, сбросьте», иначе непонятно, почему перекрыта вода.

    Нажатие ничего не переключает: окно со списком датчиков, временем
    срабатывания, тем, что каждый перекрывает, и кнопкой сброса.
    """

    roles = ("alarm", "reset")
    # Датчик шлёт значение, только когда намок или высох: молчание - норма.
    quiet = True

    @staticmethod
    def channels(conf):
        # Краны - ради строки «Сухо · вода открыта»: без подписки на них
        # она отставала бы от крана до таймаута.
        return water.channels_with_valves(_sensors(conf))

    @staticmethod
    def writes(conf):
        return set(water.resets(_sensors(conf)))

    def check(self, conf, bound):
        if not _sensors(conf) and "alarm" not in bound:
            return ("не заданы датчики: sensors, channel или раздел water "
                    "с датчиками")
        return None

    def prepare(self, ctx):
        sensors = _sensors(ctx.conf)
        extra = ctx.flag("alarm") if ctx.has("alarm") else False
        view = water.leak(ctx.state, sensors, ctx.snaps, extra)
        status = view["status"]
        if not view["alarm"] and view["known"]:
            # В покое «Сухо · 3 датчика» ничего не говорило: датчики теперь
            # видны списком, а важнее - открыта ли вода.
            wst = water.water_status(ctx.state, sensors)
            if wst:
                status = "Сухо · " + wst
        return {
            "on": False,
            "alarm": view["alarm"],
            "icon": ctx.opt("icon", "drop"),
            "status": status,
            "status_s": view["status_s"],
            "chips": _chips(view["rows"], ctx),
            "always_status": True,
            "rows": view["rows"],
            "wet": len(view["wet"]),
            "count": view["count"],
            "_resets": water.resets(sensors),
        }

    def zones(self, ctx, data):
        return [Zone("open", "all", pad="leak")]

    def pad(self, ctx, data):
        resets = list(data.get("_resets") or [])
        b = ctx.bound.get("reset")
        if b and b.command and b.command not in resets:
            resets.append(b.command)
        out = {"leak_rows": rows_attr(data.get("rows") or []),
               "alarm": "1" if data.get("alarm") else "0"}
        if resets:
            out["reset"] = "|".join(resets)
        return out


@tile("valve")
class Valve(Tile):
    """
    Кран холодной или горячей воды.

        - type: valve
          valve: cold                  # имя из water.valves

    или по-старому:

        - type: valve
          title: "Холодная"
          channel: "wb-mwac-v2_131/Output K1"
          water: cold                  # cold | hot
          channel_alarm: "wb-mwac-v2_131/Leakage Mode"
          # invert: true - если у вас 1 на выходе значит «перекрыт»

    Открытый кран - норма, и плитка в покое спокойная. Перекрытый - красная
    рамка и «Перекрыт»; если его перекрыла протечка - кто именно: «Перекрыт:
    Ванная». Это знает только раздел water, где у датчика записано, какие
    краны он перекрывает.

    Короткое нажатие открывает окно с одной кнопкой - «Перекрыть воду» или
    «Открыть воду». Промахом по экрану воду не перекрыть.
    """

    roles = ("value", "alarm")
    # Выход крана меняется только по команде или при протечке.
    quiet = True

    @staticmethod
    def expand(conf):
        name = conf.get("valve")
        if not name:
            return conf
        known = (water.valves().get(str(name)) or {})
        merged = dict(known)
        merged.update(conf)          # поля плитки сильнее описания крана
        if not merged.get("channel_alarm"):
            mod = water.modules().get(str(merged.get("module") or "")) or {}
            if mod.get("alarm"):
                merged["channel_alarm"] = mod["alarm"]
        return merged

    @staticmethod
    def channels(conf):
        # Причина перекрытия - это датчики, а не сам кран: без подписки на
        # них подпись «Перекрыт: Ванная» отставала бы до таймаута.
        name = conf.get("valve")
        if not name:
            return set()
        closing = [s for s in water.sensors().values()
                   if str(name) in s.get("closes", [])]
        return water.channels(closing)

    def _open(self, ctx):
        raw = (ctx.raw("value") or "").strip()
        if raw == "":
            return None
        opened = is_on(raw)
        return (not opened) if ctx.opt("invert") else opened

    def prepare(self, ctx):
        opened = self._open(ctx)
        alarm = ctx.flag("alarm") if ctx.has("alarm") else False
        who = []
        if ctx.opt("valve"):
            who, latched = water.closers(ctx.state, str(ctx.opt("valve")))
            alarm = alarm or latched
        water_kind = str(ctx.opt("water", "cold"))
        if opened is None:
            status = "нет данных"
        elif opened:
            status = "Открыт"
        elif who:
            status = "Перекрыт: " + ", ".join(who)
        elif alarm:
            status = "Перекрыт: протечка"
        else:
            status = "Перекрыт"
        title = str(ctx.opt("title") or "")
        return {
            # Название может прийти из раздела water, а не из плитки: ядро
            # взяло его из конфига плитки раньше, чем тот был дополнен.
            "title": title,
            "title_lines": [t for t in title.split("\n") if t] or [""],
            "title_s": title if len(title) <= 10 else title[:9] + "…",
            "on": False,
            "open": opened,
            "closed": opened is False,
            "alarm": alarm or bool(who),
            "water": water_kind if water_kind in WATER_COLOR else "cold",
            "water_color": WATER_COLOR.get(water_kind, WATER_COLOR["cold"]),
            "icon": ctx.opt("icon", "tap"),
            "status": status,
            "always_status": True,
            "who": who,
        }

    def zones(self, ctx, data):
        # Записи по нажатию нет: только окно. Топик и значения уезжают
        # в атрибуты через pad - кнопке в окне есть что публиковать.
        return [Zone("open", "all", pad="valve")]

    def pad(self, ctx, data):
        b = ctx.bound.get("value")
        if not b or not b.command:
            return {}
        open_v = str(ctx.opt("command_on", "1"))
        close_v = str(ctx.opt("command_off", "0"))
        if ctx.opt("invert"):
            open_v, close_v = close_v, open_v
        return {"topic": b.command, "on": open_v, "off": close_v,
                "state": "1" if data.get("open") else "0",
                "water": data.get("water"),
                "alarm": "1" if data.get("alarm") else "0",
                "status": data.get("status") or ""}


__all__ = ["Leak", "Valve", "command_of"]
