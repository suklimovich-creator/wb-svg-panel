# -*- coding: utf-8 -*-
"""Контракт камеры с esp32-panel и разбор MJPEG без сети и камеры."""
import io
import os
import sys
import tempfile
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(HERE, "fixtures"))

from PIL import Image

from wbpanel import cameras


def jpeg(width, height, color):
    out = io.BytesIO()
    Image.new("RGB", (width, height), color).save(out, format="JPEG")
    return out.getvalue()


def chunked(data, step):
    pos = 0

    def read(_size):
        nonlocal pos
        if pos >= len(data):
            return b""
        part = data[pos:pos + step]
        pos += len(part)
        return part

    return read


def parser_checks():
    first = jpeg(640, 360, (200, 40, 30))
    second = jpeg(320, 240, (20, 160, 70))
    body = (b"--cam\r\nContent-Type: image/jpeg\r\nContent-Length: "
            + str(len(first)).encode("ascii") + b"\r\n\r\n" + first
            + b"\r\n--cam\r\nContent-Type: image/jpeg\r\n\r\n" + second
            + b"\r\n--cam--\r\n")
    # Каждые 7 байт: boundary, заголовки и JPEG не совпадают с чанками.
    frames = list(cameras.mjpeg_frames(
        chunked(body, 7), 'multipart/x-mixed-replace; boundary="cam"'))
    assert frames == [first, second]

    resized = cameras.screen_jpeg(first, 480)
    with Image.open(io.BytesIO(resized)) as img:
        assert img.size == (480, 270)
    # Маленький кадр не увеличиваем.
    same = cameras.screen_jpeg(second, 480)
    with Image.open(io.BytesIO(same)) as img:
        assert img.size == (320, 240)

    try:
        list(cameras.mjpeg_frames(chunked(body, 11),
                                  "multipart/x-mixed-replace; boundary=cam",
                                  max_frame=100))
    except cameras.CameraError:
        pass
    else:
        raise AssertionError("кадр больше лимита не отклонён")

    class Response(object):
        def __init__(self):
            self.headers = {"Content-Type": 'multipart/x-mixed-replace; boundary="cam"'}
            self.read = chunked(body, 13)
            self.closed = False

        def close(self):
            self.closed = True

    cam = cameras.Camera("test", {"host": "192.0.2.10", "stream_limit": 0})
    response = Response()
    with patch.object(cam, "open", return_value=response):
        stream, content_type = cam.screen_stream(width=480, fps=6)
        part = next(stream)
        assert part.startswith(b"--wb-svg-panel\r\nContent-Type: image/jpeg\r\n")
        assert first not in part, "кадр должен быть перекодирован до 480 px"
        assert "boundary=wb-svg-panel" in content_type
        stream.close()
    assert response.closed, "закрытие клиента должно закрывать ответ камеры"


GATE_PASSWORD = "test-only-gate"
STILL_PASSWORD = "test-only-still"

# Значения подставляются после разбора шаблона, чтобы сканер секретов не
# принимал синтетические пароли теста за реальные учётные данные.
CONFIG = """
cameras:
  gate:
    host: 192.0.2.1
    user: viewer
    password: %(gate)s
  still:
    host: 192.0.2.2
    user: viewer
    password: %(still)s
    stream: false
panels:
  wall:
    cols: 4
    tiles:
      - {type: camera, camera: gate, title: Ворота, w: 3, h: 2}
      - {type: camera, camera: still}
      - {type: camera, camera: missing, title: Нет камеры}
      - {type: camera, camera: gate, screen: false}
""" % {"gate": GATE_PASSWORD, "still": STILL_PASSWORD}


def web_checks():
    import env
    fd, path = tempfile.mkstemp(prefix="wb-camera-", suffix=".yaml")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(CONFIG)
    try:
        web, _palette, _cfg, _sent = env.setup(config=path, freeze_time=True)
        client = web.app.test_client()
        response = client.get("/api/screen/wall")
        assert response.status_code == 200
        raw = response.get_data(as_text=True)
        assert GATE_PASSWORD not in raw and STILL_PASSWORD not in raw
        assert "192.0.2." not in raw and "viewer" not in raw
        tiles = response.get_json()["pages"][0]["tiles"]
        assert len(tiles) == 3, "screen:false не должен попадать на экран"
        gate, still, missing = tiles
        assert gate == {
            "type": "camera", "title": "Ворота", "w": 3, "h": 2,
            "snapshot": "/cam/gate.jpg",
            "stream": "/cam/gate.mjpeg?width=480&fps=5",
        }
        assert still["type"] == "camera" and still["w"] == 1 and still["h"] == 1
        assert still["snapshot"] == "/cam/still.jpg" and "stream" not in still
        assert missing["type"] == "info" and missing["note"] == "не настроена"

        for url in ("/cam/gate.mjpeg?width=159&fps=5",
                    "/cam/gate.mjpeg?width=481&fps=5",
                    "/cam/gate.mjpeg?width=480&fps=0",
                    "/cam/gate.mjpeg?width=480&fps=7",
                    "/cam/gate.mjpeg?width=abc",
                    "/cam/gate.mjpeg?fps=nan"):
            assert client.get(url).status_code == 400, url

        calls = []

        class FakeCamera(object):
            def stream(self):
                calls.append("raw")
                return iter([b"raw-body"]), "multipart/x-mixed-replace; boundary=raw"

            def screen_stream(self, width, fps):
                calls.append((width, fps))
                return iter([b"screen-body"]), "multipart/x-mixed-replace; boundary=screen"

        with patch.object(cameras, "get", return_value=FakeCamera()):
            raw_response = client.get("/cam/gate.mjpeg")
            assert raw_response.status_code == 200 and raw_response.data == b"raw-body"
            scaled_response = client.get("/cam/gate.mjpeg?width=480&fps=5")
            assert scaled_response.status_code == 200
            assert scaled_response.data == b"screen-body"
            assert scaled_response.headers["X-Accel-Buffering"] == "no"
        assert calls == ["raw", (480, 5)]
    finally:
        os.unlink(path)


def main():
    parser_checks()
    web_checks()
    print("OK: camera в раскладке, секреты скрыты, MJPEG по чанкам, лимиты и ужатие")


if __name__ == "__main__":
    main()
