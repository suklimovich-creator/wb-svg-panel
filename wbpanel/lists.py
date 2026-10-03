# -*- coding: utf-8 -*-
"""
Списки: покупки, дела по дому и всё, где нужны галочки.

Единственная сущность панели, которой нужна память. Источник правды -
файл data/lists.json рядом с config.yaml: retained-сообщения брокера
лежат на корневом разделе и переживают не всё, что переживает /mnt/data,
а в файле список не пропадёт ни при перезагрузке, ни при обновлении
прошивки. Брокеру достаётся копия: каждое изменение уходит retained-
сообщением в <prefix>/<имя>, так список видят сценарии и всё, что умеет
MQTT. Добавить пункт можно и оттуда - публикацией текста в
<prefix>/<имя>/add.

Пишет в список только демон. Страница шлёт действия - добавить,
отметить, вернуть, удалить, переставить - по номеру пункта, а не «вот
новый список целиком». Иначе два телефона, отметившие по пункту почти
одновременно, затирали бы друг другу правки.

Выполненные не удаляются сразу: неделю (keep_done) они лежат скрытыми,
и ошибочную галочку можно снять. Удаляет их не отдельное задание по
расписанию, а первое чтение списка после срока: панель перерисовывается
раз в десять секунд, так что по времени это то же самое, а лишнего
потока нет. Файл при этом переписывается, только если что-то
действительно ушло.
"""

import json
import os
import threading
import time

from .const import BASE_DIR, log
from .geometry import parse_duration

#: Длина пункта. Двести знаков - это пара строк на телефоне; длиннее -
#: уже заметка, а не пункт списка.
MAX_TEXT = 200

#: Пунктов в списке вместе со скрытыми выполненными. Предохранитель от
#: сценария, который по ошибке публикует в .../add в цикле.
MAX_ITEMS = 200

DEFAULT_KEEP = "7d"
DEFAULT_PREFIX = "/wb-svg-panel/lists"


class ListError(Exception):
    """Ошибка, которую стоит показать человеку как есть."""

    def __init__(self, message, status=400):
        Exception.__init__(self, message)
        self.status = status


def _clean(text):
    """Одна строка без управляющих символов: перевод строки в пункте
    разъехался бы и на плитке, и в окне."""
    text = "".join(ch if ch >= " " else " " for ch in str(text or ""))
    text = " ".join(text.split())
    return text[:MAX_TEXT]


class Lists(object):
    """Все списки демона. Один экземпляр на процесс."""

    def __init__(self, path=None):
        self.path = path or os.path.join(BASE_DIR, "data", "lists.json")
        self.lock = threading.RLock()
        self.data = {}
        self.config = None
        self.state = None
        # Чем публиковать копию в брокер. Задаётся после подключения:
        # без брокера списки работают как прежде, просто без зеркала.
        self.publish = None
        self._load()

    def configure(self, config, state):
        """Подключить конфиг и состояние. Зовётся из web.main() при старте."""
        self.config = config
        self.state = state
        path = config.get("lists_file") if config else None
        if path and path != self.path:
            with self.lock:
                self.path = str(path)
                self._load()
        self.purge_all()

    # ------------------------------------------------------------------
    #  конфиг
    # ------------------------------------------------------------------

    def defs(self):
        """Описания списков из config.yaml: {имя: {title, keep_done, …}}."""
        raw = (self.config.get("lists") if self.config else None) or {}
        out = {}
        if not isinstance(raw, dict):
            return out
        for name, conf in raw.items():
            name = str(name)
            if not name.replace("_", "").replace("-", "").isalnum():
                log.warning("список %r: в имени допустимы буквы, цифры, - и _",
                            name)
                continue
            out[name] = conf if isinstance(conf, dict) else {}
        return out

    def known(self, name):
        return name in self.defs()

    def prefix(self):
        conf = (self.config.get("lists_topic") if self.config else None)
        return str(conf or DEFAULT_PREFIX).rstrip("/")

    def keep(self, name):
        conf = self.defs().get(name) or {}
        return parse_duration(conf.get("keep_done", DEFAULT_KEEP))

    # ------------------------------------------------------------------
    #  файл
    # ------------------------------------------------------------------

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, IOError):
            data = {}
        except ValueError:
            # Битый файл не затираем молча: откладываем в сторону, чтобы
            # его можно было достать руками.
            spare = "%s.broken-%d" % (self.path, int(time.time()))
            try:
                os.rename(self.path, spare)
            except OSError:
                pass
            log.error("списки: файл повреждён, отложен как %s", spare)
            data = {}
        self.data = data if isinstance(data, dict) else {}

    def _save(self):
        """Атомарная запись: сначала во временный файл, потом подмена.
        Оборвись питание посреди записи - останется прежний файл, а не
        половина нового."""
        folder = os.path.dirname(self.path)
        if folder and not os.path.isdir(folder):
            os.makedirs(folder)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh, ensure_ascii=False, indent=1)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.path)

    # ------------------------------------------------------------------
    #  внутреннее
    # ------------------------------------------------------------------

    def _items(self, name):
        entry = self.data.setdefault(name, {})
        items = entry.get("items")
        if not isinstance(items, list):
            items = entry["items"] = []
        return items

    def _find(self, items, item_id):
        for n, item in enumerate(items):
            if item.get("id") == item_id:
                return n
        raise ListError("пункта уже нет", 404)

    def _purge(self, name, now=None):
        """Убрать выполненные, у которых вышел срок. True - что-то ушло."""
        now = now or time.time()
        keep = self.keep(name)
        items = self._items(name)
        alive = [i for i in items
                 if not i.get("done") or now - float(i["done"]) < keep]
        if len(alive) == len(items):
            return False
        self.data[name]["items"] = alive
        log.info("список %s: удалено выполненных %d", name,
                 len(items) - len(alive))
        return True

    def _changed(self, name):
        self._save()
        if self.state is not None:
            # Панель отдаётся с ETag по версии состояния: без этого браузер
            # получил бы 304 и не увидел нового пункта на плитке.
            with self.state.lock:
                self.state.version += 1
        self.mirror(name)

    def _check(self, name):
        if not self.known(name):
            raise ListError("список %r не описан в config.yaml" % name, 404)

    # ------------------------------------------------------------------
    #  чтение
    # ------------------------------------------------------------------

    def view(self, name):
        """
        Список для показа: открытые по порядку, выполненные отдельно,
        свежие сверху. Заодно чистка по сроку - отсюда и «удаление при
        очередном обновлении страницы».
        """
        with self.lock:
            if self._purge(name):
                self._changed(name)
            items = self._items(name)
            conf = self.defs().get(name) or {}
            opened = [dict(i) for i in items if not i.get("done")]
            done = sorted((dict(i) for i in items if i.get("done")),
                          key=lambda i: -float(i["done"]))
            return {"name": name, "title": conf.get("title") or name,
                    "open": opened, "done": done,
                    "keep_days": round(self.keep(name) / 86400.0, 1)}

    def purge_all(self):
        with self.lock:
            for name in list(self.data):
                if self.known(name) and self._purge(name):
                    self._changed(name)

    # ------------------------------------------------------------------
    #  действия
    # ------------------------------------------------------------------

    def add(self, name, text, top=None):
        self._check(name)
        text = _clean(text)
        if not text:
            raise ListError("пустой пункт")
        with self.lock:
            self._purge(name)
            items = self._items(name)
            # Тот же пункт ещё не куплен - второй не нужен. Чаще всего это
            # быстрая команда, которую Siri отправила дважды, или два
            # человека, вспомнившие про молоко одновременно.
            same = text.lower()
            for item in items:
                if not item.get("done") and item["text"].lower() == same:
                    return item
            if len(items) >= MAX_ITEMS:
                raise ListError("в списке уже %d пунктов" % MAX_ITEMS, 409)
            item = {"id": os.urandom(4).hex(), "text": text,
                    "added": int(time.time()), "done": None}
            conf = self.defs().get(name) or {}
            if top is None:
                top = str(conf.get("add_to", "bottom")) == "top"
            if top:
                items.insert(0, item)
            else:
                items.append(item)
            self._changed(name)
            return item

    def done(self, name, item_id, flag=True):
        self._check(name)
        with self.lock:
            items = self._items(name)
            item = items[self._find(items, item_id)]
            item["done"] = int(time.time()) if flag else None
            self._changed(name)

    def delete(self, name, item_id):
        self._check(name)
        with self.lock:
            items = self._items(name)
            del items[self._find(items, item_id)]
            self._changed(name)

    def move(self, name, item_id, where):
        """
        Выше, ниже, в начало. Двигаются только открытые: выполненные
        скрыты и в порядке не участвуют, поэтому меняемся местами с
        соседним открытым, перепрыгивая через скрытые.
        """
        self._check(name)
        with self.lock:
            items = self._items(name)
            n = self._find(items, item_id)
            if items[n].get("done"):
                raise ListError("выполненный пункт не двигается")
            opened = [k for k, i in enumerate(items) if not i.get("done")]
            pos = opened.index(n)
            if where == "top":
                items.insert(opened[0], items.pop(n))
            elif where in ("up", "down"):
                other = pos - 1 if where == "up" else pos + 1
                if not 0 <= other < len(opened):
                    return
                m = opened[other]
                items[n], items[m] = items[m], items[n]
            else:
                raise ListError("куда двигать: up, down или top")
            self._changed(name)

    def edit(self, name, item_id, text):
        self._check(name)
        text = _clean(text)
        if not text:
            raise ListError("пустой пункт")
        with self.lock:
            items = self._items(name)
            items[self._find(items, item_id)]["text"] = text
            self._changed(name)

    def clear_done(self, name):
        self._check(name)
        with self.lock:
            items = self._items(name)
            left = [i for i in items if not i.get("done")]
            if len(left) != len(items):
                self.data[name]["items"] = left
                self._changed(name)

    def apply(self, name, body):
        """Действие со страницы: {"op": …, "id": …, "text": …, "to": …}."""
        op = str(body.get("op") or "")
        item_id = str(body.get("id") or "")
        if op == "add":
            return self.add(name, body.get("text"),
                            top=bool(body.get("top")) if "top" in body else None)
        if op in ("done", "undo"):
            return self.done(name, item_id, op == "done")
        if op == "delete":
            return self.delete(name, item_id)
        if op == "move":
            return self.move(name, item_id, str(body.get("to") or ""))
        if op == "edit":
            return self.edit(name, item_id, body.get("text"))
        if op == "clear":
            return self.clear_done(name)
        raise ListError("неизвестное действие %r" % op)

    # ------------------------------------------------------------------
    #  MQTT
    # ------------------------------------------------------------------

    def mirror(self, name):
        """Копия списка в брокер, retained. Без брокера - тихо ничего."""
        if not self.publish or not self.known(name):
            return
        items = self._items(name)
        body = {"title": (self.defs().get(name) or {}).get("title") or name,
                "open": [{"id": i["id"], "text": i["text"]}
                         for i in items if not i.get("done")],
                "done": [{"id": i["id"], "text": i["text"], "at": i["done"]}
                         for i in items if i.get("done")]}
        body["count"] = len(body["open"])
        try:
            self.publish("%s/%s" % (self.prefix(), name),
                         json.dumps(body, ensure_ascii=False), retain=True)
        except Exception:                               # noqa: BLE001
            log.exception("список %s: не удалось опубликовать копию", name)

    def mirror_all(self):
        with self.lock:
            for name in self.defs():
                self.mirror(name)

    def add_topic(self):
        return "%s/+/add" % self.prefix()

    def on_message(self, topic, payload):
        """
        <prefix>/<имя>/add - добавить пункт текстом. True, если топик наш.

        Брокер на Wiren Board по умолчанию без пароля, так что добавить
        пункт может кто угодно в сети. Для списка покупок это приемлемо:
        хуже лишней строки ничего не случится, а длина и число пунктов
        ограничены.
        """
        prefix = self.prefix() + "/"
        if not topic.startswith(prefix) or not topic.endswith("/add"):
            return False
        name = topic[len(prefix):-len("/add")]
        if "/" in name:
            return False
        try:
            self.add(name, payload)
            log.info("список %s: пункт из MQTT", name)
        except ListError as exc:
            log.warning("список %s: %s", name, exc)
        return True


# Единственный на процесс экземпляр, как и state: плитке нужно читать
# списки при отрисовке, а передать параметром его неоткуда.
lists = Lists()
