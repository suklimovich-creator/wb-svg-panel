#!/bin/sh
#
# Почему /panel/ не открывается снаружи.
#
#   1. запустите:  sh /mnt/data/wb-svg-panel/check-external.sh
#   2. пока он крутится - откройте панель ИЗВНЕ (через облако)
#   3. смотрите, какие запросы приходят и что nginx на них отвечает
#
# Ctrl+C для выхода.

echo "=============================================================="
echo "Статика: что настроено"
echo "=============================================================="
echo "  наш location:"
grep -n "location\|satisfy\|allow\|deny\|auth_basic\|proxy_pass" \
     /etc/nginx/includes/default.wb.d/panel.conf 2>/dev/null | sed 's/^/    /'
echo
echo "  локальные проверки:"
printf "  логин для проверки (Enter = panel): "; read U; [ -z "$U" ] && U=panel
printf "  пароль: "; stty -echo 2>/dev/null; read P; stty echo 2>/dev/null; echo
AUTH=""; [ -n "$P" ] && AUTH="-u $U:$P"
for u in /panel /panel/ /panel/healthz /panel/main.html; do
    code=$(curl -s $AUTH -o /dev/null -w '%{http_code}' --max-time 4 "http://127.0.0.1$u")
    loc=$(curl -s $AUTH -o /dev/null -w '%{redirect_url}' --max-time 4 "http://127.0.0.1$u")
    body=$(curl -s $AUTH --max-time 4 "http://127.0.0.1$u" | tr -d '\n' | head -c 40)
    # 401 отдаёт САМ nginx, и его страница ошибки - тоже html.
    # Раньше скрипт считал её вебмордой и вводил в заблуждение.
    case "$code$body" in
      401*)             v="просит пароль (это правильно)" ;;
      301*|302*)        v="редирект" ;;
      *mqtt*)           v="панель" ;;
      *"<svg"*)         v="картинка панели" ;;
      *"id=\"host\""*)  v="страница панели" ;;
      *DOCTYPE*|*"<html"*) v="ВЕБ-ИНТЕРФЕЙС" ;;
      *) v="?" ;;
    esac
    printf "    %-18s %s  %-18s %s\n" "$u" "$code" "$v" "$loc"
done

echo
echo "  агент облака:"
systemctl is-active wb-cloud-agent 2>/dev/null | sed 's/^/    /'

echo
echo "=============================================================="
echo "Живой лог. Откройте панель извне - строки появятся здесь."
echo "=============================================================="
echo "Смотрите на путь и код: 200 у /panel/ = дошло до нас,"
echo "301/302 = редирект, 401 = просит пароль, 200 у / = ушло в вебморду."
echo
tail -f /var/log/nginx/access.log 2>/dev/null | grep --line-buffered -E "panel|/ HTTP"
