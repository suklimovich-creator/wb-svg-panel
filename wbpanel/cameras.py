# -*- coding: utf-8 -*-
"""
Камеры: снимок и поток, отданные через панель.

Почему через панель, а не напрямую из браузера. Камера требует
аутентификации, и адрес вида http://user:pass@ip/... в теге <img>
современный браузер молча отбрасывает - это давняя мера против фишинга.
Значит пароль всё равно пришлось бы положить в разметку, откуда его
читает кто угодно с доступом к панели.

Поэтому ходит демон: пароль остаётся в config.yaml на контроллере, а
странице достаётся безобидное `cam/nursery.jpg`. Заодно камеру можно
закрыть в сети так, чтобы её видел только контроллер.

Что здесь есть:

    снимок  - один кадр JPEG, с коротким кешем: несколько планшетов,
              обновляющих панель вразнобой, не должны будить камеру
              каждые полсекунды;
    поток   - MJPEG, проксируемый как есть для браузера; для ESP32 может
              собираться заново с уменьшением кадров и ограничением fps.
              Занимает поток waitress на всё время просмотра, поэтому число
              одновременных ограничено, а длительность одного соединения - тоже.

Пути по умолчанию - для Hikvision/HiWatch; Dahua и IMOU описаны рядом,
всё остальное задаётся вручную полями snapshot и stream.

Стандартная библиотека: на контроллере из HTTP-клиентов есть только
urllib, и тащить requests ради двух запросов незачем.
"""

import io
import json
import threading
import time
import urllib.error
import urllib.request
from email.message import Message

from .const import log


# Конфиг. Заполняется в web.main(), как assemble.config: камеры нужны и
# плитке при отрисовке, и маршрутам, а параметром туда не пробраться.
config = None


#: Пути, зависящие от марки. {channel} подставляется из поля channel -
#: у Hikvision это 101 главный поток, 102 суб-поток, 103 третий.
BRANDS = {
    "hikvision": {
        "snapshot": "/ISAPI/Streaming/channels/{channel}/picture",
        "stream": "/ISAPI/Streaming/channels/{channel}/httpPreview",
        "channel": "102",
    },
    # HiWatch - бюджетная марка Hikvision, протокол тот же.
    "hiwatch": {
        "snapshot": "/ISAPI/Streaming/channels/{channel}/picture",
        "stream": "/ISAPI/Streaming/channels/{channel}/httpPreview",
        "channel": "102",
    },
    "dahua": {
        "snapshot": "/cgi-bin/snapshot.cgi?channel={channel}",
        "stream": "/cgi-bin/mjpg/video.cgi?channel={channel}&subtype=1",
        "channel": "1",
    },
}


DEFAULT_BRAND = "hikvision"

#: Верхний предел одного JPEG из MJPEG. Без него оборванный или неверно
#: настроенный поток мог бы наращивать буфер до исчерпания памяти.
MJPEG_MAX_FRAME = 2 * 1024 * 1024
SCREEN_BOUNDARY = "wb-svg-panel"


class CameraError(Exception):
    """Камера не ответила или ответила не тем. Текст идёт человеку."""


def _boundary(content_type):
    """Boundary из Content-Type; email.message убирает кавычки и параметры."""
    msg = Message()
    msg["content-type"] = content_type or ""
    value = msg.get_param("boundary", header="content-type")
    if not value:
        raise CameraError("у MJPEG нет boundary в Content-Type")
    try:
        return b"--" + value.encode("latin-1")
    except (UnicodeEncodeError, AttributeError):
        raise CameraError("у MJPEG неверный boundary в Content-Type")


def _header_end(buf):
    """(конец заголовков, длина разделителя) для CRLF и простого LF."""
    crlf = buf.find(b"\r\n\r\n")
    lf = buf.find(b"\n\n")
    if crlf < 0 and lf < 0:
        return -1, 0
    if crlf >= 0 and (lf < 0 or crlf <= lf):
        return crlf, 4
    return lf, 2


def mjpeg_frames(read, content_type, max_frame=MJPEG_MAX_FRAME):
    """
    JPEG из потока multipart. read(size) блокируется как у urllib response.

    HTTP-чанки нарочно нигде не считаются кадрами: камера может разрезать
    заголовок или JPEG где угодно. Сначала ищем boundary и заголовки части,
    затем берём Content-Length; если его нет - конец JPEG FF D9.
    """
    marker = _boundary(content_type)
    buf = bytearray()
    eof = False

    def fill():
        nonlocal eof
        data = read(16384)
        if not data:
            eof = True
            return False
        buf.extend(data)
        return True

    while not eof or buf:
        # Начало следующей MIME-части.
        at = buf.find(marker)
        if at < 0:
            if len(buf) > max_frame + len(marker):
                raise CameraError("в MJPEG слишком долго нет boundary")
            if fill():
                continue
            return
        del buf[:at + len(marker)]
        if buf.startswith(b"--"):                 # завершающий boundary
            return

        # Заголовки части. Перед ними у boundary обычно стоит CRLF.
        end, sep = _header_end(buf)
        while end < 0:
            if len(buf) > 65536:
                raise CameraError("в MJPEG слишком длинные заголовки кадра")
            if not fill():
                raise CameraError("MJPEG оборвался в заголовках кадра")
            end, sep = _header_end(buf)
        headers = bytes(buf[:end]).decode("latin-1", "replace")
        del buf[:end + sep]
        length = None
        for line in headers.splitlines():
            key, _sep, value = line.partition(":")
            if key.strip().lower() == "content-length":
                try:
                    length = int(value.strip())
                except ValueError:
                    raise CameraError("у MJPEG неверный Content-Length")
                if length < 0:
                    raise CameraError("у MJPEG отрицательный Content-Length")

        if length is not None:
            if length > max_frame:
                raise CameraError("кадр MJPEG больше %d байт" % max_frame)
            while len(buf) < length:
                if not fill():
                    raise CameraError("MJPEG оборвался посреди кадра")
            frame = bytes(buf[:length])
            del buf[:length]
        else:
            start = buf.find(b"\xff\xd8")
            while start < 0:
                if len(buf) > max_frame:
                    raise CameraError("кадр MJPEG больше %d байт" % max_frame)
                if not fill():
                    raise CameraError("в части MJPEG нет JPEG")
                start = buf.find(b"\xff\xd8")
            end = buf.find(b"\xff\xd9", start + 2)
            while end < 0:
                if len(buf) > max_frame + 2:
                    raise CameraError("кадр MJPEG больше %d байт" % max_frame)
                if not fill():
                    raise CameraError("MJPEG оборвался посреди JPEG")
                end = buf.find(b"\xff\xd9", start + 2)
            frame = bytes(buf[start:end + 2])
            del buf[:end + 2]

        if not frame.startswith(b"\xff\xd8") or not frame.endswith(b"\xff\xd9"):
            raise CameraError("часть MJPEG не является законченным JPEG")
        yield frame


def screen_jpeg(data, width):
    """JPEG не шире width, пропорции сохраняются. Pillow уже есть у панели."""
    from PIL import Image
    img = Image.open(io.BytesIO(data))
    img.load()
    if img.width > width:
        height = max(1, round(img.height * width / float(img.width)))
        resampling = getattr(Image, "Resampling", Image)
        img = img.resize((width, height), resampling.LANCZOS)
    if img.mode != "RGB":
        img = img.convert("RGB")
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=82)
    return out.getvalue()


class Camera(object):
    """
    Одна камера: куда ходить, чем представляться, как часто беспокоить.

    Объект живёт между запросами ради opener'а: у digest-аутентификации
    первый запрос всегда получает 401 и повторяется с ответом на вызов,
    и переиспользуемый opener хотя бы помнит realm.
    """

    def __init__(self, name, conf):
        conf = dict(conf or {})
        brand = str(conf.get("brand", DEFAULT_BRAND)).strip().lower()
        base = BRANDS.get(brand)
        if base is None:
            log.warning("камера %r: марка %r не описана, беру %s",
                        name, brand, DEFAULT_BRAND)
            base = BRANDS[DEFAULT_BRAND]

        self.name = name
        self.brand = brand
        self.host = str(conf.get("host") or "").strip()
        self.port = int(conf.get("port", 443 if conf.get("https") else 80))
        self.https = bool(conf.get("https", False))
        self.user = str(conf.get("user") or conf.get("username") or "")
        self.password = str(conf.get("password") or "")
        self.channel = str(conf.get("channel", base["channel"]))

        self.snapshot_path = _fill(conf.get("snapshot", base["snapshot"]),
                                   self.channel)
        # stream: false - камера умеет только снимки. Тогда и полный экран
        # рисуется частыми снимками, а не потоком.
        stream = conf.get("stream", base["stream"])
        self.stream_path = ("" if stream in (False, None, "", "no")
                            else _fill(str(stream), self.channel))

        # Как часто плитка меняет адрес картинки. Ровно это и есть частота
        # обновления кадра на панели: в промежутке браузер берёт из кеша.
        self.refresh = max(float(conf.get("refresh", 10)), 1.0)
        # Сколько сервер держит уже забранный кадр. Защита не от браузера,
        # а от нескольких клиентов сразу.
        self.cache = max(float(conf.get("cache", 1.0)), 0.0)
        self.timeout = float(conf.get("timeout", 6.0))
        # Через сколько секунд соединение с потоком закрывается само.
        # Ноль - не закрывать; тогда подвисший планшет держит поток
        # waitress до тайм-аута TCP, что заметно хуже.
        self.stream_limit = float(conf.get("stream_limit", 600))
        self.title = conf.get("title") or name

        self._lock = threading.RLock()
        self._opener = None
        self._frame = None          # (ts, bytes, content-type)

    # -- адреса ------------------------------------------------------------

    @property
    def origin(self):
        scheme = "https" if self.https else "http"
        default = 443 if self.https else 80
        if self.port == default:
            return "%s://%s" % (scheme, self.host)
        return "%s://%s:%d" % (scheme, self.host, self.port)

    def url(self, path):
        if not path.startswith("/"):
            path = "/" + path
        return self.origin + path

    # -- HTTP --------------------------------------------------------------

    def _make_opener(self):
        mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
        mgr.add_password(None, self.origin + "/", self.user, self.password)
        # Оба обработчика нарочно: Hikvision по умолчанию требует digest,
        # но в настройках безопасности бывает выставлено digest/basic, и
        # тогда камера отвечает вызовом basic. Угадывать не нужно - пусть
        # ответит тот, чей вызов пришёл.
        return urllib.request.build_opener(
            urllib.request.HTTPDigestAuthHandler(mgr),
            urllib.request.HTTPBasicAuthHandler(mgr))

    def open(self, path, timeout=None):
        """Запрос к камере. Возвращает открытый ответ - закрыть обязан вызвавший."""
        if not self.host:
            raise CameraError("у камеры %r не задан host" % self.name)
        with self._lock:
            if self._opener is None:
                self._opener = self._make_opener()
            opener = self._opener

        # Пароль вперёд не суём: пусть камера сама скажет, какой способ
        # ей нужен. Стоит это одного лишнего круга - первый запрос уходит
        # безымянным, получает 401 с вызовом, и обработчик повторяет его
        # уже с ответом. В локальной сети это доли миллисекунды, зато
        # пароль не улетает туда, где его не спрашивали.
        request = urllib.request.Request(self.url(path))
        try:
            return opener.open(request, timeout=timeout or self.timeout)
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise CameraError("камера %r не пустила: %s. Проверьте "
                                  "логин, пароль и режим аутентификации "
                                  "(нужен digest/basic)" % (self.name, exc.code))
            raise CameraError("камера %r ответила %s" % (self.name, exc.code))
        except Exception as exc:                       # noqa: BLE001
            raise CameraError("камера %r недоступна: %s" % (self.name, exc))

    # -- кадр --------------------------------------------------------------

    def snapshot(self):
        """Один кадр: (байты, content-type). Свежесть - в пределах cache."""
        now = time.time()
        with self._lock:
            frame = self._frame
        if frame and self.cache and now - frame[0] < self.cache:
            return frame[1], frame[2]

        from . import stats
        t0 = time.time()
        resp = self.open(self.snapshot_path)
        try:
            data = resp.read()
            stats.took("camera", time.time() - t0)
            ctype = resp.headers.get("Content-Type") or "image/jpeg"
        finally:
            resp.close()
        if not data:
            raise CameraError("камера %r вернула пустой кадр" % self.name)
        # Камера, у которой не тот канал, отвечает 200 и XML с ошибкой.
        # Отличить можно по началу: JPEG всегда начинается с FF D8.
        if not data.startswith(b"\xff\xd8"):
            head = data[:120].decode("utf-8", "replace").strip()
            raise CameraError("камера %r прислала не JPEG: %s"
                              % (self.name, head))
        with self._lock:
            self._frame = (now, data, ctype)
        return data, ctype

    def stream(self, chunk=16384):
        """
        MJPEG как есть. Возвращает (генератор, content-type).

        Content-Type берём у камеры целиком: в нём граница multipart, без
        неё браузер поток не разберёт.
        """
        if not self.stream_path:
            raise CameraError("у камеры %r поток выключен" % self.name)
        resp = self.open(self.stream_path, timeout=self.timeout)
        ctype = (resp.headers.get("Content-Type")
                 or "multipart/x-mixed-replace; boundary=--boundary")

        def gen():
            deadline = (time.time() + self.stream_limit) if self.stream_limit else None
            try:
                while True:
                    if deadline and time.time() > deadline:
                        log.info("камера %r: поток закрыт по времени", self.name)
                        break
                    buf = resp.read(chunk)
                    if not buf:
                        break
                    yield buf
            except GeneratorExit:
                # Браузер закрыл окно - это норма, а не сбой.
                raise
            except Exception as exc:                   # noqa: BLE001
                log.warning("камера %r: поток оборвался: %s", self.name, exc)
            finally:
                try:
                    resp.close()
                except Exception:                      # noqa: BLE001
                    pass

        return gen(), ctype

    def screen_stream(self, width=480, fps=5):
        """
        MJPEG для ESP32-S3: не шире 480 px и не чаще 6 кадров/с.

        Сырой поток выше остаётся нетронутым для браузера. Здесь кадры
        собираются из multipart, лишние по частоте пропускаются до Pillow,
        а соединение с камерой закрывается при закрытии генератора клиентом.
        """
        if not self.stream_path:
            raise CameraError("у камеры %r поток выключен" % self.name)
        width = max(160, min(480, int(width)))
        fps = max(1.0, min(6.0, float(fps)))
        resp = self.open(self.stream_path, timeout=self.timeout)
        source_type = (resp.headers.get("Content-Type")
                       or "multipart/x-mixed-replace; boundary=--boundary")
        boundary = SCREEN_BOUNDARY.encode("ascii")

        def gen():
            deadline = (time.time() + self.stream_limit) if self.stream_limit else None
            next_frame = 0.0
            bad_frames = 0
            try:
                for raw in mjpeg_frames(resp.read, source_type):
                    if deadline and time.time() > deadline:
                        log.info("камера %r: поток закрыт по времени", self.name)
                        break
                    now = time.monotonic()
                    if now < next_frame:
                        continue
                    next_frame = now + 1.0 / fps
                    try:
                        frame = screen_jpeg(raw, width)
                    except Exception as exc:            # noqa: BLE001
                        bad_frames += 1
                        # Не каждый кадр: у постоянно битого потока журнал
                        # не должен расти быстрее самого потока.
                        if bad_frames == 1 or bad_frames % 30 == 0:
                            log.warning("камера %r: пропущен битый JPEG: %s",
                                        self.name, exc)
                        continue
                    head = (b"--" + boundary + b"\r\nContent-Type: image/jpeg\r\n"
                            + ("Content-Length: %d\r\n\r\n" % len(frame)).encode("ascii"))
                    yield head + frame + b"\r\n"
            except GeneratorExit:
                raise
            except Exception as exc:                   # noqa: BLE001
                log.warning("камера %r: поток оборвался: %s", self.name, exc)
            finally:
                try:
                    resp.close()
                except Exception:                      # noqa: BLE001
                    pass

        ctype = "multipart/x-mixed-replace; boundary=" + SCREEN_BOUNDARY
        return gen(), ctype


def _fill(path, channel):
    """
    Подстановка номера канала. Именно replace, а не format: в пути бывают
    фигурные скобки от чужих прошивок, и format на них падает.
    """
    return str(path).replace("{channel}", str(channel))


# ==========================================================================
#  Реестр камер из конфига
# ==========================================================================

_CAMS = {}                  # имя -> (слепок конфига, Camera)
_REG_LOCK = threading.RLock()

#: Сколько потоков смотрят одновременно. Каждый занимает поток waitress
#: на всё время просмотра, а их восемь: без ограничения два забытых
#: планшета способны подвесить панель целиком.
_STREAMS = threading.BoundedSemaphore(3)


def all_confs():
    return (config.get("cameras") or {}) if config else {}


def names():
    return sorted(all_confs())


def get(name):
    """
    Камера по имени из конфига. None, если такой нет.

    Объект пересоздаётся, когда изменился его кусок конфига: панель
    перечитывает YAML на лету, и правка адреса должна доходить без
    перезапуска демона.
    """
    conf = all_confs().get(name)
    if not isinstance(conf, dict):
        return None
    key = json.dumps(conf, sort_keys=True, default=str, ensure_ascii=False)
    with _REG_LOCK:
        cached = _CAMS.get(name)
        if cached and cached[0] == key:
            return cached[1]
        cam = Camera(name, conf)
        _CAMS[name] = (key, cam)
        log.info("камера %r: %s", name, cam.origin)
        return cam


def acquire_slot(timeout=0.2):
    """
    Занять место в очереди на просмотр потока.

    Не контекстный менеджер нарочно: генератор ответа переживает
    обработчик маршрута, и `with` освободил бы место сразу же - ровно
    тогда, когда просмотр только начинается. Освобождает вызывающий,
    в finally самого генератора.
    """
    return _STREAMS.acquire(timeout=timeout)


def release_slot():
    try:
        _STREAMS.release()
    except ValueError:
        # Освободили дважды - значит где-то ошибка в учёте, но ронять
        # из-за неё просмотр камеры незачем.
        log.warning("камеры: лишнее освобождение слота потока")
