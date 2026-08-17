#!/bin/sh
#
# Установка wb-svg-panel — и она же восстановление после обновления прошивки.
#
# Обновление прошивки Wiren Board сохраняет только раздел /mnt/data, а всё
# стороннее ПО удаляет: пропадут apt-пакеты, systemd-юнит и конфиг nginx.
# Поэтому код живёт на /mnt/data, а этот скрипт заново раскладывает остальное:
#
#     sh /mnt/data/wb-svg-panel/install.sh
#
set -e

SRC="$(cd "$(dirname "$0")" && pwd)"

case "$SRC" in
  /mnt/data/*) ;;
  *)
    echo "ОШИБКА: каталог $SRC лежит не на /mnt/data — обновление прошивки его сотрёт."
    echo "  mkdir -p /mnt/data/wb-svg-panel && cp -r $SRC/* /mnt/data/wb-svg-panel/"
    exit 1
    ;;
esac

echo "==> проверка файлов"
MISSING=""
for f in panel.py config.yaml wb-svg-panel.service nginx-wb-svg-panel.conf; do
    if [ ! -f "$SRC/$f" ]; then
        MISSING="$MISSING $f"
        echo "    НЕТ: $f"
    else
        echo "    ok:  $f"
    fi
done

# Шаблон годится в любой раскладке: templates/panel.svg.j2 или рядом с panel.py.
if [ -f "$SRC/templates/panel.svg.j2" ]; then
    echo "    ok:  templates/panel.svg.j2"
elif [ -f "$SRC/panel.svg.j2" ]; then
    echo "    ok:  panel.svg.j2 (плоская раскладка)"
else
    MISSING="$MISSING panel.svg.j2"
    echo "    НЕТ: panel.svg.j2"
fi
if [ -n "$MISSING" ]; then
    cat <<MSG

ОШИБКА: не хватает файлов:$MISSING

Положите недостающие файлы в $SRC и запустите скрипт снова.
Подкаталог templates/ не обязателен: panel.svg.j2 может лежать прямо рядом
с panel.py — демон найдёт его в обеих раскладках.
MSG
    exit 1
fi

if [ -f "$SRC/docs.html" ]; then
    echo "    ok:  docs.html"
else
    echo "    --   docs.html нет — страница /docs работать не будет"
fi

echo "==> зависимости"
apt-get update -qq
apt-get install -y --no-install-recommends \
    python3-paho-mqtt python3-jinja2 python3-yaml python3-flask python3-waitress

echo "==> systemd"
install -m 644 "$SRC/wb-svg-panel.service" /etc/systemd/system/wb-svg-panel.service
systemctl daemon-reload
systemctl enable wb-svg-panel
systemctl restart wb-svg-panel

echo "==> nginx"
install -m 644 "$SRC/nginx-wb-svg-panel.conf" /etc/nginx/sites-available/wb-svg-panel
ln -sf /etc/nginx/sites-available/wb-svg-panel /etc/nginx/sites-enabled/wb-svg-panel

# Панель внутри штатного веб-интерфейса.
# Кладём в официальную точку расширения - каталог не принадлежит пакету
# wb-mqtt-homeui, значит обновление прошивки его не затрёт, и сам default
# править не нужно.
WBD=/etc/nginx/includes/default.wb.d
if [ -f "$SRC/nginx-panel-location.conf" ] && [ -d "$WBD" ]; then
    install -m 644 "$SRC/nginx-panel-location.conf" "$WBD/panel.conf"
    # свой пароль для панели, отдельно от пароля вебморды
    if [ ! -f /etc/nginx/panel.passwd ]; then
        echo "    файла /etc/nginx/panel.passwd нет — снаружи панель будет закрыта."
        echo "    Создайте:  printf \"panel:\$(openssl passwd -apr1)\\n\" > /etc/nginx/panel.passwd"
    fi
    echo "    панель смонтирована в $WBD/panel.conf -> /panel/"
elif [ -f "$SRC/nginx-panel-location.conf" ]; then
    echo "    каталога $WBD нет — пропускаю /panel/, остаётся порт 8080"
fi

nginx -t
systemctl reload nginx

echo
echo "==> проверка"
sleep 2
systemctl --no-pager --lines=0 status wb-svg-panel || true
echo
curl -fsS localhost:8088/healthz && echo
echo
echo "Проверка, что /panel/ отдаёт именно панель, а не веб-интерфейс:"
BODY=$(curl -s --max-time 5 http://127.0.0.1/panel/healthz)
case "$BODY" in
  *mqtt*) echo "    OK: http://$(hostname).local/panel/" ;;
  *)      echo "    /panel/ отвечает не панелью — проверьте $WBD/panel.conf"
          echo "    ответ начинается так: $(echo "$BODY" | head -c 60)" ;;
esac
echo
echo "Панель:  http://$(hostname).local/panel/"
echo "         http://$(hostname).local:8080/          (запасной порт)"
echo "Если что-то не так: python3 $SRC/diag.py"
