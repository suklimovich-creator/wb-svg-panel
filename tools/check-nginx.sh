#!/bin/sh
#
# Что уже настроено в nginx и можно ли переиспользовать вход вебморды.
#     sh /mnt/data/wb-svg-panel/check-nginx.sh
# Ничего не меняет.

echo "=============================================================="
echo "1. Пароли"
echo "=============================================================="
if [ -f /etc/nginx/panel.passwd ]; then
    echo "  ПАНЕЛЬ /etc/nginx/panel.passwd, пользователи:"
    cut -d: -f1 /etc/nginx/panel.passwd | sed 's/^/    /'
else
    echo "  файла /etc/nginx/panel.passwd НЕТ — панель снаружи будет отдавать 403."
    echo "  Создать:  printf \"panel:\$(openssl passwd -apr1)\\n\" > /etc/nginx/panel.passwd"
fi
echo
if [ -f /etc/nginx/passwd ]; then
    echo "  ВЕБ-ИНТЕРФЕЙС /etc/nginx/passwd, пользователи:"
    cut -d: -f1 /etc/nginx/passwd | sed 's/^/    /'
else
    echo "  файла /etc/nginx/passwd НЕТ — пароль на вебморду не настроен."
    echo "  Создать:  printf \"%s:\$(openssl passwd -apr1)\\n\" panel > /etc/nginx/passwd"
fi
echo
echo "  сниппет авторизации:"
[ -f /etc/nginx/sites-available/global_auth ] \
  && sed 's/^/    /' /etc/nginx/sites-available/global_auth \
  || echo "    global_auth отсутствует"

echo
echo "  включён ли он в веб-интерфейс (ищем include в default):"
grep -n "include\|auth_basic" /etc/nginx/sites-available/default 2>/dev/null | sed 's/^/    /'
echo "    ^ если строки с global_auth нет — вебморда сейчас БЕЗ пароля"

echo
echo "=============================================================="
echo "2. Структура блока server в default"
echo "=============================================================="
grep -n "^server\|listen\|root\|location\|}" /etc/nginx/sites-available/default 2>/dev/null \
  | head -30 | sed 's/^/    /'

echo
echo "=============================================================="
echo "3. Куда смонтирована панель"
echo "=============================================================="
WBD=/etc/nginx/includes/default.wb.d
echo "  официальная точка расширения $WBD:"
ls -l "$WBD" 2>/dev/null | sed 's/^/    /' || echo "    каталога нет"
[ -f "$WBD/panel.conf" ] \
  && echo "    panel.conf на месте -> http://<контроллер>/panel/" \
  || echo "    panel.conf ОТСУТСТВУЕТ — панель на 80-м порту не смонтирована"
[ -L /etc/nginx/sites-enabled/wb-svg-panel ] \
  && echo "    отдельный сервер на 8080 включён" \
  || echo "    отдельного сервера на 8080 нет"

echo
echo "=============================================================="
echo "3б. Может ли nginx прочитать файл паролей"
echo "=============================================================="
if [ -f /etc/nginx/panel.passwd ]; then
    ls -l /etc/nginx/panel.passwd | sed 's/^/    /'
    if sudo -u www-data test -r /etc/nginx/panel.passwd 2>/dev/null; then
        echo "    www-data читает файл — ok"
    else
        echo "    www-data НЕ ЧИТАЕТ файл — панель всегда будет отвечать 401!"
        echo "    Лечится:  chown root:www-data /etc/nginx/panel.passwd"
        echo "              chmod 640 /etc/nginx/panel.passwd"
    fi
    echo "    формат первой строки:"
    head -1 /etc/nginx/panel.passwd | cut -c1-24 | sed 's/^/      /'
    echo "      (должно быть  логин:\$apr1\$…  — если после двоеточия нет \$apr1\$,"
    echo "       пароль сохранён в неподдерживаемом формате)"
fi

echo
echo "=============================================================="
echo "4. Проверка ответа"
echo "=============================================================="
# Код 200 сам по себе ничего не говорит: веб-интерфейс - одностраничное
# приложение и отдаёт свой index.html на ЛЮБОЙ неизвестный путь. Поэтому
# смотрим не код, а начало ответа.
for u in http://127.0.0.1:8088/healthz \
         http://127.0.0.1/panel/healthz \
         http://127.0.0.1/panel/main.html \
         http://127.0.0.1/panel/main.svg \
         http://127.0.0.1:8080/healthz; do
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 4 "$u" 2>/dev/null)
    body=$(curl -s --max-time 4 "$u" 2>/dev/null | tr -d '\n' | head -c 50)
    case "$body" in
      *mqtt*)          verdict="панель: healthz" ;;
      *"<svg"*)        verdict="панель: картинка" ;;
      *"id=\"host\""*) verdict="панель: страница" ;;
      *DOCTYPE*|*"<html"*) verdict="ВЕБ-ИНТЕРФЕЙС, а не панель" ;;
      "")              verdict="пусто" ;;
      *)               verdict="?" ;;
    esac
    echo "    $code  $u"
    echo "         -> $verdict   $body"
done
echo
echo "    401 = просит пароль, 200 + «панель» = работает"
