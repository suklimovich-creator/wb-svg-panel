#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Разведка каналов шторы.

    python3 probe-curtain.py                       что вообще есть в MQTT
    python3 probe-curtain.py --from-config         каналы штор из config.yaml
    python3 probe-curtain.py --hk <префикс>        следить за одной шторой
    python3 probe-curtain.py --hk <префикс> --move проверить управление

где префикс - всё до номера характеристики, например
    Sprut.hub-XXXXXXXX_1/accessories/108/13

Понимает два соглашения об именах топиков:

  Wiren Board   /devices/<устройство>/controls/<канал>
  сырые топики  всё остальное - так публикуют мосты вроде Sprut.hub

Без --move ничего не пишет, только слушает.

Характеристики HomeKit у службы WindowCovering (13):
    14  HoldPosition      остановить (запись)
    15  TargetPosition    куда ехать (запись)
    16  CurrentPosition   где сейчас (чтение)
    17  PositionState     0 закрывается, 1 открывается, 2 стоит
    18  PositionState     то же самое у части прошивок

Что нужно выяснить перед тем, как делать управление:
  * в какой характеристике цель, а в какой факт;
  * сыплет ли факт промежуточные значения по ходу или только конечное;
  * разворачивается ли привод, если цель поменять на ходу;
  * сколько занимает полный ход.
"""

import argparse
import json
import sys
import time

try:
    import paho.mqtt.client as mqtt
except ImportError:
    sys.exit("Нет paho-mqtt:  apt install python3-paho-mqtt")

WB_PREFIX = "/devices/"

# Мост Sprut.hub описывает каждую характеристику соседними подтопиками:
#   .../13/15        значение
#   .../13/15/type   TargetPosition
#   .../13/15/write  true
# Угадывать по номерам не нужно - имена и права публикует сам мост.
RAW_META = frozenset((
    "type", "read", "write", "events", "format", "hidden", "maxLen",
    "maxValue", "minValue", "minStep", "validValues", "unit", "units",
    "timedWrite", "writeResponse", "additionalAuthorization",
))

# Расшифровка значений там, где число само по себе ничего не говорит
VALUE_WORDS = {
    "PositionState": {"0": "закрывается", "1": "открывается", "2": "стоит"},
    "C_TargetPositionState": {"0": "закрыть", "1": "открыть", "2": "стоп"},
}


def parse_topic(topic):
    """
    -> (ключ, ключ_меты | None) либо None

    Ключ - то, чем канал называется в config.yaml. Для Wiren Board это
    'устройство/канал', для сырых топиков - сам топик как есть.
    """
    if topic.startswith(WB_PREFIX):
        rest = topic[len(WB_PREFIX):]
        if "/controls/" not in rest:
            return None
        dev, ctl = rest.split("/controls/", 1)
        if ctl.endswith("/meta"):
            return "%s/%s" % (dev, ctl[:-5]), ""
        if "/meta/" in ctl:
            c, k = ctl.rsplit("/meta/", 1)
            if "/" not in k:
                return "%s/%s" % (dev, c), k
        return "%s/%s" % (dev, ctl), None

    # Сырой топик моста: последний сегмент может быть описанием соседа
    head, _, tail = topic.rpartition("/")
    if head and tail in RAW_META:
        return head, tail
    return topic, None


class Bus(object):
    def __init__(self, host="localhost", port=1883):
        self.vals = {}
        self.meta = {}
        self.log = []        # (время, ключ, значение)
        self.count = 0
        self.connected = False

        self.cli = mqtt.Client()
        self.cli.on_message = self._on_msg
        self.cli.on_connect = self._on_connect
        self.cli.connect(host, port, 30)
        self.cli.loop_start()

        # Подписка действует только после CONNACK. Если оформить её сразу
        # после connect(), пакет уходит раньше подтверждения и пропадает:
        # скрипт работает, а сообщений нет ни одного.
        deadline = time.time() + 10
        while not self.connected and time.time() < deadline:
            time.sleep(0.1)
        if not self.connected:
            sys.exit("Не удалось подключиться к брокеру %s:%d" % (host, port))

    def _on_connect(self, cli, ud, flags, rc):
        if rc != 0:
            sys.exit("Брокер отказал в подключении, код %s" % rc)
        # Мосты публикуют мимо /devices/, поэтому слушаем всё.
        cli.subscribe("#")
        self.connected = True

    def _on_msg(self, cli, ud, msg):
        parsed = parse_topic(msg.topic)
        if not parsed:
            return
        key, meta_key = parsed
        self.count += 1
        payload = msg.payload.decode("utf-8", "replace")
        if meta_key is not None:
            if meta_key:
                self.meta.setdefault(key, {})[meta_key] = payload
            else:
                try:
                    self.meta.setdefault(key, {}).update(json.loads(payload))
                except ValueError:
                    pass
            return
        old = self.vals.get(key)
        self.vals[key] = payload
        if old is not None and old != payload:
            self.log.append((time.time(), key, payload))

    def write(self, key, value):
        """
        Куда писать команду.

        Wiren Board: тот же топик с суффиксом /on.
        Мост Sprut.hub: подтопик /set - писать в топик состояния бесполезно,
        мост его не слушает, а брокер просто перетрёт значение обратно.
        """
        if key.count("/") == 1:
            dev, ctl = key.split("/", 1)
            topic = "/devices/%s/controls/%s/on" % (dev, ctl)
        else:
            topic = key + "/set"
        self.cli.publish(topic, str(value))
        return topic

    def settle(self, seconds=4):
        time.sleep(seconds)
        if self.count == 0:
            print("\nНи одного сообщения из MQTT за %d с." % seconds)
            print("Проверьте:  mosquitto_sub -v -C 5 -t '#'")
            sys.exit(1)


def label(bus, key):
    """Имя характеристики и права - из того, что публикует сам мост."""
    meta = bus.meta.get(key, {})
    name = meta.get("type", "")
    if not name:
        return ""
    rights = []
    if str(meta.get("write", "")).lower() == "true":
        rights.append("запись")
    if str(meta.get("read", "")).lower() == "true":
        rights.append("чтение")
    lim = ""
    if meta.get("minValue") is not None and meta.get("maxValue") is not None:
        lim = " %s..%s" % (meta["minValue"], meta["maxValue"])
    elif meta.get("validValues"):
        lim = " {%s}" % meta["validValues"]
    return "%s%s%s" % (name, lim, (" [" + ", ".join(rights) + "]") if rights else "")


def show(bus, key, val):
    name = bus.meta.get(key, {}).get("type", "")
    words = VALUE_WORDS.get(name, {})
    return "%s (%s)" % (val, words[val]) if val in words else val


def writable(bus, key):
    return str(bus.meta.get(key, {}).get("write", "")).lower() == "true"


# --------------------------------------------------------------------------


def cmd_overview(bus):
    print("Слушаю MQTT…")
    bus.settle(5)
    wb, raw = {}, {}
    for key in bus.vals:
        bucket = wb if key.count("/") == 1 else raw
        bucket.setdefault(key.split("/")[0], []).append(key)

    print("\nWiren Board — устройств: %d" % len(wb))
    for d in sorted(wb):
        print("    %-36s каналов: %d" % (d, len(wb[d])))

    print("\nСырые топики (мосты) — корней: %d" % len(raw))
    for d in sorted(raw):
        print("    %-36s топиков: %d" % (d, len(raw[d])))

    print("\nДальше:  python3 probe-curtain.py --from-config")


def cmd_from_config(bus, path):
    try:
        import yaml
    except ImportError:
        sys.exit("Нет PyYAML:  apt install python3-yaml")
    try:
        cfg = yaml.safe_load(open(path, encoding="utf-8")) or {}
    except IOError as exc:
        sys.exit("Не читается %s: %s" % (path, exc))

    found = []
    for pname, panel in (cfg.get("panels") or {}).items():
        for t in (panel.get("tiles") or []):
            if t.get("type") != "curtain":
                continue
            ch = dict((f, t[f]) for f in
                      ("channel", "channel_left", "channel_right", "channel_state")
                      if t.get(f))
            found.append((pname, t.get("title", "без названия"), ch))
    if not found:
        sys.exit("В %s нет плиток type: curtain" % path)

    print("Плиток штор в конфиге: %d\n" % len(found))
    bus.settle()

    last_prefix = None
    for pname, title, chans in found:
        print("=" * 66)
        print("%s  (панель %s)" % (title, pname))
        prefix = None
        for field, key in sorted(chans.items()):
            val = bus.vals.get(key)
            print("    %-14s %s" % (field + ":", key))
            print("    %-14s = %-24s %s"
                  % ("", "НЕТ ДАННЫХ" if val is None else show(bus, key, val),
                     label(bus, key)))
            if key.count("/") > 1:
                prefix = key.rsplit("/", 1)[0]

        # Соседние характеристики того же прибора: там и найдётся то,
        # что в конфиге не описано, - цель, стоп, состояние.
        if prefix:
            last_prefix = prefix
            sibs = sorted(k for k in bus.vals
                          if k.startswith(prefix + "/")
                          and k not in chans.values())
            if sibs:
                print("\n    Рядом под тем же префиксом:")
                for k in sibs:
                    print("      %-6s = %-24s %s"
                          % (k.rsplit("/", 1)[-1], show(bus, k, bus.vals[k]),
                             label(bus, k)))
        print()

    if last_prefix:
        print("Следить за движением:")
        print("    python3 probe-curtain.py --hk '%s'" % last_prefix)


def cmd_hk(bus, prefix, do_move):
    prefix = prefix.rstrip("/")
    print("Штора: %s" % prefix)
    bus.settle()

    keys = sorted(k for k in bus.vals if k.startswith(prefix + "/"))
    if not keys:
        sys.exit("Под этим префиксом ничего не публикуется. Проверьте написание.")

    # Частая ошибка: передали имя устройства целиком вместо пути до службы.
    # Тогда сюда попадают все аксессуары моста, а не одна штора.
    services = sorted(set(k.rsplit("/", 1)[0] for k in keys))
    if len(services) > 1:
        print("\nПод этим префиксом %d служб — это слишком широко."
              % len(services))
        covering = [sv for sv in services
                    if any(bus.meta.get(k, {}).get("type") == "CurrentPosition"
                           for k in keys if k.startswith(sv + "/"))]
        if covering:
            print("Похоже на шторы:")
            for sv in covering:
                name = ""
                for k in keys:
                    if k.startswith(sv + "/") and \
                       bus.meta.get(k, {}).get("type") == "Name":
                        name = bus.vals[k]
                print("    %-52s %s" % (sv, name))
            print("\nПовторите с полным путём до службы:")
            print("    python3 probe-curtain.py --hk '%s'" % covering[0])
        else:
            print("Служб: %s" % ", ".join(services[:10]))
        sys.exit(0)

    print("\nСейчас:")
    for k in keys:
        print("    %-6s = %-24s %s"
              % (k.rsplit("/", 1)[-1], show(bus, k, bus.vals[k]), label(bus, k)))

    if not do_move:
        print("\nПодвигайте штору любым способом — пультом, из Apple Home,")
        print("из Sprut.hub. Ctrl+C — выход.\n")
        watch(bus, prefix)
        return

    # Цель ищем по типу характеристики, а не по номеру: у разных приводов
    # нумерация своя, а TargetPosition называется одинаково.
    target = None
    for k in keys:
        if bus.meta.get(k, {}).get("type") == "TargetPosition" and writable(bus, k):
            target = k
            break
    if not target:
        print("\nЗаписываемой TargetPosition не нашлось. Что доступно:")
        for k in keys:
            if writable(bus, k):
                print("    %-6s %s" % (k.rsplit("/", 1)[-1], label(bus, k)))
        num = input("В какую писать? (номер, Enter — отмена): ").strip()
        if not num:
            sys.exit("Отменено.")
        target = "%s/%s" % (prefix, num)

    print("\nБуду писать в %s" % target)
    print("Штора поедет. Убедитесь, что ей ничего не мешает.")
    if input("Продолжить? (да/нет): ").strip().lower() not in ("да", "yes", "y"):
        sys.exit("Отменено.")

    for title, value in (("открыть (100)", 100), ("закрыть (0)", 0)):
        print("\n--- %s ---" % title)
        run(bus, target, value, 30)

    print("\n--- разворот на ходу: открыть, через 5 с попросить закрыть ---")
    t0 = time.time()
    n = len(bus.log)
    print("    пишу 100 в %s" % bus.write(target, 100))
    time.sleep(5)
    print("    --- не дожидаясь конца хода, пишу 0 ---")
    bus.write(target, 0)
    drain(bus, n, t0, 30, prefix)

    print("\nЕсли факт (16) сразу пошёл в обратную сторону — привод")
    print("разворачивается на лету, и второе нажатие можно делать разворотом.")
    print("Если сперва доехал до 100 и только потом вниз — панели придётся")
    print("блокировать нажатие на время хода.")


def run(bus, key, value, seconds):
    t0 = time.time()
    n = len(bus.log)
    print("    пишу %s в %s" % (value, bus.write(key, value)))
    drain(bus, n, t0, seconds, key.rsplit("/", 1)[0])


def drain(bus, n, t0, seconds, prefix):
    """Печатать события по этой шторе, пока не истечёт время."""
    last = None
    while time.time() - t0 < seconds:
        time.sleep(0.3)
        while n < len(bus.log):
            ts, k, v = bus.log[n]
            n += 1
            if not k.startswith(prefix + "/"):
                continue
            print("    +%5.1f с  %-6s %-24s %s"
                  % (ts - t0, k.rsplit("/", 1)[-1], show(bus, k, v), label(bus, k)))
            last = ts
    if last:
        print("    последнее изменение через %.1f с после команды" % (last - t0))
    else:
        print("    ни одного изменения — команда не дошла")
        print("    у моста Sprut.hub управление идёт в подтопик /set;")
        print("    проверьте вручную:")
        print("      mosquitto_pub -t '<топик>/set' -m 100")


def watch(bus, prefix):
    n = len(bus.log)
    t0 = time.time()
    try:
        while True:
            time.sleep(0.3)
            while n < len(bus.log):
                ts, k, v = bus.log[n]
                n += 1
                if not k.startswith(prefix + "/"):
                    continue
                print("    %s (+%6.1f)  %-6s %-24s %s"
                      % (time.strftime("%H:%M:%S", time.localtime(ts)),
                         ts - t0, k.rsplit("/", 1)[-1], show(bus, k, v),
                         label(bus, k)))
    except KeyboardInterrupt:
        print("\nГотово.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-config", metavar="ПУТЬ", nargs="?",
                    const="config.yaml", help="взять каналы из config.yaml")
    ap.add_argument("--hk", metavar="ПРЕФИКС",
                    help="следить за одной шторой (путь до номера характеристики)")
    ap.add_argument("--move", action="store_true",
                    help="проверить управление (штора поедет)")
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=1883)
    args = ap.parse_args()

    bus = Bus(args.host, args.port)
    if args.hk:
        cmd_hk(bus, args.hk, args.move)
    elif args.from_config:
        cmd_from_config(bus, args.from_config)
    else:
        cmd_overview(bus)


if __name__ == "__main__":
    main()
