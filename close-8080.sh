#!/bin/sh
#
# Порт 8080: проверка и закрытие.
#
# Панель доступна двумя путями. Основной - /panel/ внутри штатного
# веб-интерфейса: он закрыт списком адресов и паролем. Запасной - отдельный
# server nginx на порту 8080, и он не закрыт ничем. При interactive: true это
# не просмотр, а управление светом, шторами и реле с любого адреса в сети.
#
#     sh close-8080.sh            только показать, ничего не менять
#     sh close-8080.sh --lan      порт оставить, пускать только свою сеть
#     sh close-8080.sh --off      порт выключить совсем
#     sh close-8080.sh --open     вернуть как было (откат)
#
# --lan нужен, если на 8080 ходит планшет или ESP32: адрес без /panel/ короче
# и не требует пароля внутри дома. --off - если всё ходит через /panel/.
#
# Скрипт идемпотентный. Правит две копии конфига: рабочую в /etc/nginx и
# исходную на /mnt/data, иначе install.sh вернёт незакрытый вариант обратно.

SRC="$(cd "$(dirname "$0")" && pwd)"

AVAIL=/etc/nginx/sites-available/wb-svg-panel
ENABLED=/etc/nginx/sites-enabled/wb-svg-panel
ORIG="$SRC/nginx-wb-svg-panel.conf"
PASSWD=/etc/nginx/panel.passwd
MARK="wb-svg-panel-acl"

MODE="${1:-check}"
case "$MODE" in
    check|--lan|--off|--open) ;;
    *) echo "Не понимаю аргумент: $MODE"; echo "Ожидается: --lan | --off | --open"; exit 2 ;;
esac

# --------------------------------------------------------------------------
# Вспомогательное
# --------------------------------------------------------------------------

listens_8080() {
    if command -v ss >/dev/null 2>&1; then
        ss -ltn 2>/dev/null | grep -q ':8080 '
    else
        netstat -ltn 2>/dev/null | grep -q ':8080 '
    fi
}

http_code() {  # http_code <url>
    curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$1" 2>/dev/null || echo "---"
}

has_acl() { [ -f "$1" ] && grep -q "$MARK" "$1"; }

backup() {
    [ -f "$1" ] || return 0
    cp -a "$1" "$1.bak-$(date +%Y%m%d-%H%M%S)"
}

# Вставляет блок доступа первой строкой внутрь location / { ... }
add_acl() {
    f="$1"
    [ -f "$f" ] || return 0
    if has_acl "$f"; then
        echo "    уже закрыт: $f"
        return 0
    fi
    AUTH=""
    [ -f "$PASSWD" ] && AUTH=1
    backup "$f"
    tmp="$f.new.$$"
    awk -v mark="$MARK" -v auth="$AUTH" '
        BEGIN { done = 0 }
        {
            print
            if (!done && $0 ~ /^[[:space:]]*location \/ \{/) {
                print "        # --- " mark " (вставлено close-8080.sh) ---"
                print "        # satisfy any: пускаем ЛИБО по адресу, ЛИБО по паролю."
                print "        # Свою подсеть посмотрите командой: ip -4 addr show"
                print "        satisfy any;"
                print "        allow 127.0.0.1;"
                print "        allow ::1;"
                print "        allow 192.168.0.0/16;"
                print "        allow 10.0.0.0/8;"
                print "        allow 172.16.0.0/12;"
                print "        deny  all;"
                if (auth != "") {
                    print "        auth_basic \"Панель\";"
                    print "        auth_basic_user_file /etc/nginx/panel.passwd;"
                }
                print "        # --- конец " mark " ---"
                done = 1
            }
        }
        END { if (!done) exit 3 }
    ' "$f" > "$tmp"
    rc=$?
    if [ "$rc" != "0" ]; then
        rm -f "$tmp"
        echo "    НЕ НАШЁЛ 'location / {' в $f — правьте руками"
        return 1
    fi
    mv "$tmp" "$f"
    echo "    закрыт: $f"
}

del_acl() {
    f="$1"
    has_acl "$f" || return 0
    backup "$f"
    sed -i "/--- $MARK (вставлено/,/--- конец $MARK ---/d" "$f"
    echo "    список доступа убран: $f"
}

# Проверяет конфиг, при ошибке откатывает из последнего бэкапа
nginx_apply() {
    if nginx -t >/dev/null 2>&1; then
        systemctl reload nginx
        echo "    nginx перечитан"
        return 0
    fi
    echo
    echo "  ОШИБКА: nginx -t не проходит. Откатываю."
    nginx -t 2>&1 | sed 's/^/      /'
    for f in "$AVAIL" "$ORIG"; do
        last=$(ls -1t "$f".bak-* 2>/dev/null | head -1)
        [ -n "$last" ] && cp -a "$last" "$f" && echo "      возвращено из $last"
    done
    nginx -t >/dev/null 2>&1 && systemctl reload nginx
    return 1
}

# --------------------------------------------------------------------------
# 1. Что сейчас
# --------------------------------------------------------------------------

echo "=============================================================="
echo "Что сейчас"
echo "=============================================================="

DAEMON=$(http_code http://127.0.0.1:8088/healthz)
echo "  демон 127.0.0.1:8088 ......... $DAEMON"

PANEL=$(http_code http://127.0.0.1/panel/healthz)
echo "  основной вход /panel/ ........ $PANEL"

if listens_8080; then
    echo "  порт 8080 .................... слушает"
else
    echo "  порт 8080 .................... не слушает"
fi

if [ -L "$ENABLED" ] || [ -f "$ENABLED" ]; then
    echo "  sites-enabled ................ включён"
    LINKED=1
else
    echo "  sites-enabled ................ выключен"
    LINKED=0
fi

if has_acl "$AVAIL"; then
    echo "  список доступа на 8080 ....... есть"
    ACL=1
else
    echo "  список доступа на 8080 ....... НЕТ (пускает всех)"
    ACL=0
fi

if [ -f "$PASSWD" ]; then
    echo "  пароль $PASSWD ... есть"
else
    echo "  пароль $PASSWD ... нет"
    echo "      создать: printf \"panel:\$(openssl passwd -apr1)\\n\" > $PASSWD"
    echo "               chmod 640 $PASSWD && chown root:www-data $PASSWD"
fi

if grep -qE '^[[:space:]]*interactive:[[:space:]]*true' "$SRC/config.yaml" 2>/dev/null; then
    echo "  interactive .................. true — панель УПРАВЛЯЕТ, не только показывает"
    INTERACTIVE=1
else
    echo "  interactive .................. false — только просмотр"
    INTERACTIVE=0
fi

echo
if [ "$LINKED" = "1" ] && [ "$ACL" = "0" ]; then
    if [ "$INTERACTIVE" = "1" ]; then
        echo "  ВЫВОД: порт 8080 открыт всей сети и позволяет управлять домом."
    else
        echo "  ВЫВОД: порт 8080 открыт всей сети (просмотр)."
    fi
    echo "         Закрыть:  sh $0 --lan     (оставить для планшета)"
    echo "                   sh $0 --off     (выключить совсем)"
else
    echo "  ВЫВОД: запасной порт закрыт или выключен."
fi

[ "$MODE" = "check" ] && { echo; echo "Ничего не менял — это была проверка."; exit 0; }

# --------------------------------------------------------------------------
# 2. Не отрезать себе руки
# --------------------------------------------------------------------------

if [ "$MODE" != "--open" ] && [ "$PANEL" != "200" ]; then
    echo
    echo "СТОП: основной вход /panel/ отвечает $PANEL, а не 200."
    echo "      Закрывать запасной порт сейчас нельзя — останетесь без панели."
    echo "      Сначала: sh $SRC/check-nginx.sh"
    exit 1
fi

[ "$(id -u)" = "0" ] || { echo; echo "Нужны права root."; exit 1; }

# --------------------------------------------------------------------------
# 3. Действие
# --------------------------------------------------------------------------

echo
echo "=============================================================="
case "$MODE" in

--lan)
    echo "Закрываю 8080 списком адресов"
    echo "=============================================================="
    add_acl "$AVAIL" || exit 1
    add_acl "$ORIG"  || exit 1
    nginx_apply || exit 1
    echo
    echo "  проверка с самого контроллера (должно быть 200):"
    echo "      $(http_code http://127.0.0.1:8080/healthz)"
    echo "  снаружи подсети теперь 403."
    ;;

--off)
    echo "Выключаю 8080"
    echo "=============================================================="
    if [ "$LINKED" = "1" ]; then
        rm -f "$ENABLED"
        echo "    удалён $ENABLED"
    else
        echo "    уже выключен"
    fi
    nginx_apply || exit 1
    if listens_8080; then
        echo "    ВНИМАНИЕ: 8080 всё ещё слушает — его держит другой конфиг:"
        grep -rl "listen.*8080" /etc/nginx/sites-enabled/ /etc/nginx/conf.d/ 2>/dev/null | sed 's/^/        /'
    else
        echo "    порт 8080 больше не слушает"
    fi
    echo
    echo "  Файл остался в $AVAIL — вернуть можно через --open."
    ;;

--open)
    echo "Возвращаю как было"
    echo "=============================================================="
    del_acl "$AVAIL"
    del_acl "$ORIG"
    ln -sf "$AVAIL" "$ENABLED"
    echo "    включён $ENABLED"
    nginx_apply || exit 1
    echo
    echo "  ВНИМАНИЕ: 8080 снова открыт всем в сети."
    ;;
esac

echo
echo "Панель:  http://$(hostname).local/panel/"
[ "$MODE" != "--off" ] && echo "         http://$(hostname).local:8080/   (запасной)"
