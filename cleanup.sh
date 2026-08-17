#!/bin/sh
#
# Ревизия: что осталось от прежних, неверных вариантов установки.
#
#     sh /mnt/data/wb-svg-panel/cleanup.sh          только показать
#     sh /mnt/data/wb-svg-panel/cleanup.sh --fix    удалить лишнее
#
# Ничего нужного скрипт не трогает: список закрыт и перечислен явно.

FIX=0
[ "$1" = "--fix" ] && FIX=1
FOUND=0

note() {  # note <путь> <описание> <опасно ли>
    [ -e "$1" ] || return 0
    FOUND=$((FOUND+1))
    echo "  ЛИШНЕЕ  $1"
    echo "          $2"
    if [ "$FIX" = "1" ]; then
        rm -rf "$1" && echo "          удалено"
    fi
}

echo "=============================================================="
echo "1. Следы прежних вариантов размещения"
echo "=============================================================="
note /opt/wb-svg-panel \
     "первый, неверный вариант: на rootfs, стирается при обновлении прошивки"
note /etc/wb-svg-panel.yaml \
     "конфиг из ранней инструкции; сейчас используется /mnt/data/wb-svg-panel/config.yaml"
note /etc/nginx/panel_location.conf \
     "промежуточный вариант; рабочий лежит в /etc/nginx/includes/default.wb.d/panel.conf"

echo
echo "=============================================================="
echo "2. Подкаталог templates - может подменять шаблон"
echo "=============================================================="
if [ -f /mnt/data/wb-svg-panel/templates/panel.svg.j2 ]; then
    FOUND=$((FOUND+1))
    echo "  ОПАСНО  /mnt/data/wb-svg-panel/templates/panel.svg.j2"
    echo "          демон ищет шаблон СНАЧАЛА в templates/, и берёт этот файл,"
    echo "          а не тот, что вы кладёте рядом с panel.py. Правки не видны."
    ls -l /mnt/data/wb-svg-panel/templates/panel.svg.j2 /mnt/data/wb-svg-panel/panel.svg.j2 \
        2>/dev/null | sed 's/^/          /'
    [ "$FIX" = "1" ] && rm -rf /mnt/data/wb-svg-panel/templates && echo "          удалено"
else
    echo "  чисто: подкаталога нет, используется panel.svg.j2 рядом с panel.py"
fi

echo
echo "=============================================================="
echo "3. Мусор в каталоге проекта"
echo "=============================================================="
for p in /mnt/data/wb-svg-panel/__pycache__ /mnt/data/wb-svg-panel/preview-main.svg \
         /mnt/data/wb-svg-panel/preview-gostinaya.svg; do
    note "$p" "рабочие остатки, на работу не влияют"
done
ls /mnt/data/wb-svg-panel/preview-*.svg 2>/dev/null | head -3 | sed 's/^/  превью: /'

echo
echo "=============================================================="
echo "4. Лишний открытый порт 8080"
echo "=============================================================="
if [ -L /etc/nginx/sites-enabled/wb-svg-panel ]; then
    BODY=$(curl -s --max-time 4 http://127.0.0.1/panel/healthz 2>/dev/null)
    case "$BODY" in
      *mqtt*)
        FOUND=$((FOUND+1))
        echo "  ЛИШНЕЕ  /etc/nginx/sites-enabled/wb-svg-panel"
        echo "          /panel/ на 80-м порту работает, отдельный сервер на 8080"
        echo "          больше не нужен - и на него ругается встроенная проверка"
        echo "          безопасности контроллера."
        if [ "$FIX" = "1" ]; then
            rm -f /etc/nginx/sites-enabled/wb-svg-panel
            nginx -t >/dev/null 2>&1 && systemctl reload nginx && echo "          отключено"
        fi
        ;;
      *) echo "  оставляем: /panel/ ещё не отвечает панелью, 8080 нужен как запасной" ;;
    esac
else
    echo "  чисто: отдельного сервера на 8080 нет"
fi

echo
echo "=============================================================="
echo "5. Что должно остаться"
echo "=============================================================="
for p in /mnt/data/wb-svg-panel/panel.py \
         /mnt/data/wb-svg-panel/panel.svg.j2 \
         /mnt/data/wb-svg-panel/config.yaml \
         /mnt/data/wb-svg-panel/docs.html \
         /etc/systemd/system/wb-svg-panel.service \
         /etc/nginx/includes/default.wb.d/panel.conf \
         /etc/nginx/panel.passwd; do
    [ -e "$p" ] && echo "  есть    $p" || echo "  НЕТ     $p"
done

echo
if [ "$FOUND" = "0" ]; then
    echo "Лишнего не найдено."
elif [ "$FIX" = "1" ]; then
    echo "Удалено пунктов: $FOUND"
else
    echo "Найдено лишнего: $FOUND. Удалить:  sh $0 --fix"
fi
