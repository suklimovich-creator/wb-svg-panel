#!/bin/sh
#
# Диагностика истории: почему нет графиков температуры и влажности.
# Чистый shell, никаких зависимостей — работает, даже если panel.py не стартует.
#
#     sh /mnt/data/wb-svg-panel/check-history.sh
#
# Ничего не меняет, только читает и печатает.

CHANNELS="wb-msw-v4_149/Temperature wb-msw-v4_149/Humidity
wb-msw-v4_232/Temperature wb-msw-v4_232/Humidity
wb-msw-v4_174/Temperature wb-msw-v4_174/Humidity"

echo "=============================================================="
echo "1. Служба wb-mqtt-db"
echo "=============================================================="
systemctl is-enabled wb-mqtt-db 2>&1 | sed 's/^/  автозапуск: /'
systemctl is-active  wb-mqtt-db 2>&1 | sed 's/^/  состояние:  /'
echo "  последние строки лога:"
journalctl -u wb-mqtt-db -n 8 --no-pager 2>/dev/null | sed 's/^/    /'

echo
echo "=============================================================="
echo "2. Где лежит база"
echo "=============================================================="
DB=""
for p in /var/lib/wirenboard/db/data.db \
         /mnt/data/var/lib/wirenboard/db/data.db \
         /var/lib/wb-mqtt-db/data.db \
         /mnt/data/var/lib/wb-mqtt-db/data.db; do
    if [ -f "$p" ]; then
        echo "  НАЙДЕНА: $p"
        ls -lh "$p" | sed 's/^/    /'
        [ -z "$DB" ] && DB="$p"
    fi
done
if [ -z "$DB" ]; then
    echo "  файла нет ни в одном из типовых мест — ищу по диску:"
    find /var /mnt/data -name 'data.db' -size +1k 2>/dev/null | head -5 | sed 's/^/    /'
    echo
    echo "  Если ничего не найдено — wb-mqtt-db ни разу не писал."
    echo "  Дальше смотреть нечего, переходите к шагу 3."
fi

echo
echo "=============================================================="
echo "3. Конфиг /etc/wb-mqtt-db.conf"
echo "=============================================================="
if [ -f /etc/wb-mqtt-db.conf ]; then
    echo "  какие каналы перечислены:"
    grep -o '"[^"]*/[^"]*"' /etc/wb-mqtt-db.conf 2>/dev/null \
        | tr -d '"' | sort -u | head -40 | sed 's/^/    /'
    echo
    echo "  лимиты по группам (values = записей на канал):"
    grep -E '"(name|min_interval|values|values_total)"' /etc/wb-mqtt-db.conf \
        | sed 's/^[ \t]*/    /'
    echo
    echo "  ЕСТЬ ЛИ ТАМ НАШИ ДАТЧИКИ:"
    for ch in $CHANNELS; do
        dev=$(echo "$ch" | cut -d/ -f1)
        if grep -q "$ch" /etc/wb-mqtt-db.conf 2>/dev/null; then
            echo "    ДА  (точно)   $ch"
        elif grep -q "\"$dev/+\"" /etc/wb-mqtt-db.conf 2>/dev/null; then
            echo "    ДА  (по маске $dev/+)  $ch"
        elif grep -q '"+/+"' /etc/wb-mqtt-db.conf 2>/dev/null; then
            echo "    ДА  (по маске +/+)     $ch"
        else
            echo "    НЕТ           $ch   <-- вот почему нет графика"
        fi
    done
else
    echo "  файла /etc/wb-mqtt-db.conf нет"
fi

echo
echo "=============================================================="
echo "4. Что реально лежит в базе"
echo "=============================================================="
if [ -z "$DB" ]; then
    echo "  пропускаю — база не найдена"
elif ! command -v sqlite3 >/dev/null 2>&1; then
    echo "  нет утилиты sqlite3. Поставьте:  apt install -y sqlite3"
    echo "  и запустите скрипт снова."
else
    echo "  таблицы:"
    sqlite3 "$DB" ".tables" 2>&1 | sed 's/^/    /'
    echo
    echo "  схема:"
    sqlite3 "$DB" ".schema" 2>&1 | head -30 | sed 's/^/    /'
    echo
    # Схема бывает двух видов: плоская (channels.device - строка) и со
    # справочником devices. Пробуем плоскую, при неудаче - вторую.
    echo "  сколько записей по каналам (топ-20):"
    OUT=$(sqlite3 -header -column "$DB" "
        SELECT c.device || '/' || c.control AS channel,
               COUNT(*) AS records,
               datetime(MAX(v.timestamp), 'unixepoch', 'localtime') AS last_local
        FROM data v JOIN channels c ON c.int_id = v.channel
        GROUP BY channel ORDER BY records DESC LIMIT 20;" 2>&1)
    case "$OUT" in
      *Error*) OUT=$(sqlite3 -header -column "$DB" "
        SELECT d.device || '/' || c.control AS channel,
               COUNT(*) AS records,
               datetime(MAX(v.timestamp), 'unixepoch', 'localtime') AS last_local
        FROM data v
        JOIN channels c ON c.int_id = v.channel
        JOIN devices  d ON d.int_id = c.device_id
        GROUP BY channel ORDER BY records DESC LIMIT 20;" 2>&1) ;;
    esac
    echo "$OUT" | sed 's/^/    /'

    echo
    echo "  НАШИ ДАТЧИКИ в базе:"
    for ch in $CHANNELS; do
        dev=$(echo "$ch" | cut -d/ -f1)
        ctl=$(echo "$ch" | cut -d/ -f2)
        N=$(sqlite3 "$DB" "
            SELECT COUNT(*) FROM data v JOIN channels c ON c.int_id = v.channel
            WHERE c.device='$dev' AND c.control='$ctl';" 2>/dev/null)
        [ -z "$N" ] && N=$(sqlite3 "$DB" "
            SELECT COUNT(*) FROM data v
            JOIN channels c ON c.int_id = v.channel
            JOIN devices d ON d.int_id = c.device_id
            WHERE d.device='$dev' AND c.control='$ctl';" 2>/dev/null)
        LAST=$(sqlite3 "$DB" "
            SELECT datetime(MAX(v.timestamp),'unixepoch','localtime')
            FROM data v JOIN channels c ON c.int_id = v.channel
            WHERE c.device='$dev' AND c.control='$ctl';" 2>/dev/null)
        if [ "${N:-0}" -gt 0 ] 2>/dev/null; then
            echo "    $N записей, последняя $LAST   $ch"
        else
            echo "    ПУСТО   $ch"
        fi
    done

    echo
    echo "  ЗА КАКОЙ ПЕРИОД ЕСТЬ ДАННЫЕ:"
    sqlite3 "$DB" "
        SELECT '    вся база: с ' ||
               datetime(MIN(timestamp),'unixepoch','localtime') ||
               ' по ' || datetime(MAX(timestamp),'unixepoch','localtime') ||
               '  (' || CAST((MAX(timestamp)-MIN(timestamp))/3600.0 AS INT) || ' ч)'
        FROM data;" 2>&1

    for ch in $CHANNELS; do
        dev=$(echo "$ch" | cut -d/ -f1)
        ctl=$(echo "$ch" | cut -d/ -f2)
        sqlite3 "$DB" "
            SELECT CASE WHEN COUNT(*)=0 THEN '    нет данных                          $ch'
                   ELSE '    ' ||
                        CAST((MAX(v.timestamp)-MIN(v.timestamp))/3600.0 AS INT) || ' ч глубина, ' ||
                        COUNT(*) || ' зап., шаг ~' ||
                        CAST((MAX(v.timestamp)-MIN(v.timestamp))/(CASE WHEN COUNT(*)>1
                             THEN COUNT(*)-1 ELSE 1 END)/60.0 AS INT) || ' мин, свежесть ' ||
                        CAST((strftime('%s','now')-MAX(v.timestamp))/60.0 AS INT) || ' мин   $ch'
                   END
            FROM data v JOIN channels c ON c.int_id = v.channel
            WHERE c.device='$dev' AND c.control='$ctl';" 2>/dev/null
    done

    echo
    echo "  Хватает ли глубины на график за 12 часов:"
    sqlite3 "$DB" "
        SELECT '    ' || c.device || '/' || c.control || ' — ' ||
               COUNT(*) || ' точек за последние 12 ч' ||
               CASE WHEN COUNT(*) >= 30 THEN '   ГОДИТСЯ'
                    WHEN COUNT(*) > 0   THEN '   мало, график будет рваный'
                    ELSE '   ПУСТО' END
        FROM data v JOIN channels c ON c.int_id = v.channel
        WHERE v.timestamp > strftime('%s','now') - 43200
          AND c.control IN ('Temperature','Humidity')
        GROUP BY c.device, c.control ORDER BY 1;" 2>/dev/null

    echo
    echo "  общий объём:"
    sqlite3 "$DB" "SELECT '    записей всего: ' || COUNT(*) FROM data;" 2>&1
    sqlite3 "$DB" "SELECT '    каналов всего: ' || COUNT(*) FROM channels;" 2>&1
    sqlite3 "$DB" "
        SELECT '    записей у самого полного канала: ' || MAX(n) ||
               ',  у самого бедного: ' || MIN(n)
        FROM (SELECT COUNT(*) AS n FROM data GROUP BY channel);" 2>&1
fi

echo
echo "=============================================================="
echo "5. Приходят ли значения по MQTT прямо сейчас"
echo "=============================================================="
if command -v mosquitto_sub >/dev/null 2>&1; then
    for ch in wb-msw-v4_149/Temperature wb-msw-v4_232/Temperature wb-msw-v4_174/Temperature; do
        dev=$(echo "$ch" | cut -d/ -f1)
        ctl=$(echo "$ch" | cut -d/ -f2)
        val=$(timeout 3 mosquitto_sub -C 1 -t "/devices/$dev/controls/$ctl" 2>/dev/null)
        if [ -n "$val" ]; then
            echo "  $ch = $val"
        else
            echo "  $ch — НЕТ ЗНАЧЕНИЯ (устройство молчит или имя другое)"
        fi
    done
else
    echo "  нет mosquitto_sub (apt install -y mosquitto-clients)"
fi

echo
echo "=============================================================="
echo "ЧТО ДЕЛАТЬ"
echo "=============================================================="
cat <<'MSG'
  Случай А. В шаге 3 у датчиков написано НЕТ — канал не логируется.
  Случай Б. В шаге 3 написано ДА (по маске +/+), но в шаге 4 ПУСТО или
  записей мало — общий лимит группы values_total делится на все каналы,
  а их на контроллере сотни. Нужна отдельная группа для датчиков.

  В обоих случаях добавьте группу в /etc/wb-mqtt-db.conf, ПЕРВОЙ в массиве
  "groups" — до общей маски "+/+":

    {
      "name": "panel",
      "channels": [
        "wb-msw-v4_149/Temperature", "wb-msw-v4_149/Humidity",
        "wb-msw-v4_232/Temperature", "wb-msw-v4_232/Humidity",
        "wb-msw-v4_174/Temperature", "wb-msw-v4_174/Humidity"
      ],
      "min_interval": 60,
      "values": 2000,
      "values_total": 20000
    }

  затем:  systemctl restart wb-mqtt-db

  Проверить, что пошла запись (через пару минут):
    sqlite3 <путь к базе> "SELECT COUNT(*) FROM data;"
  и повторить этот скрипт — в шаге 4 каналы должны появиться.

  Полный график за 12 часов наберётся через 12 часов; первые точки
  появятся уже через несколько минут.
MSG
