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

EN8080=/etc/nginx/sites-enabled/wb-svg-panel

# keep - не трогать текущее состояние порта 8080, yes - включить, no - выключить
WITH_8080=keep
for arg in "$@"; do
    case "$arg" in
        --with-8080) WITH_8080=yes ;;
        --no-8080)   WITH_8080=no ;;
        -h|--help)
            echo "Использование: sh install.sh [--with-8080 | --no-8080]"
            echo "  без ключей   состояние порта 8080 остаётся как есть"
            echo "  --with-8080  включить запасной вход на порту 8080"
            echo "  --no-8080    выключить его"
            exit 0
            ;;
        *) echo "Неизвестный ключ: $arg (см. --help)"; exit 2 ;;
    esac
done

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

# Запасной вход на порту 8080 - отдельный server nginx без пароля. Внутри дома
# он удобен (короткий адрес для планшета или ESP32), но сам себя не защищает,
# поэтому по умолчанию выключен.
#
# Правило простое: уже включённый порт остаётся включённым, выключенный сам не
# включается. Так повторный запуск после обновления прошивки возвращает ровно
# то состояние, которое было. Переопределяется ключами --with-8080 / --no-8080.
if [ "$WITH_8080" = "yes" ] || { [ "$WITH_8080" = "keep" ] && [ -L "$EN8080" ]; }; then
    ln -sf /etc/nginx/sites-available/wb-svg-panel "$EN8080"
    echo "    порт 8080 включён — он не закрыт паролем, только сеть"
    echo "    закрыть:  sh $SRC/close-8080.sh --off"
else
    if [ -L "$EN8080" ] || [ -e "$EN8080" ]; then
        rm -f "$EN8080"
        echo "    порт 8080 выключен (--no-8080)"
    else
        echo "    порт 8080 не включён — панель только через /panel/"
        echo "    включить:  sh $SRC/install.sh --with-8080"
    fi
fi

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
[ -L "$EN8080" ] && echo "         http://$(hostname).local:8080/          (запасной порт)"
echo "Если что-то не так: python3 $SRC/diag.py"
