#!/bin/sh
# Нагрузка на демон: N «экранов» держат ждущий запрос, как браузер.
#
#     sh tools/load-test.sh                 10 экранов на панели all, 120 с
#     sh tools/load-test.sh 20 main 60
#
# Запускать на контроллере. В соседнем окне смотрите
#     top -d 2 -p $(pgrep -f panel.py)
# и щёлкайте светом. Останавливается сам; в конце печатает, сколько
# ответов пришло и сколько из них были короткими 204 (мест для ждущих не
# хватило - это нормально, страница просто спросит позже).

N=${1:-10}
P=${2:-all}
SECS=${3:-120}
PORT=${PORT:-8088}
END=$(( $(date +%s) + SECS ))
TMP=$(mktemp -d)

i=0
while [ "$i" -lt "$N" ]; do
    (
        v=""
        got=0; short=0
        while [ "$(date +%s)" -lt "$END" ]; do
            t0=$(date +%s%N)
            q="t=$t0"
            [ -n "$v" ] && q="since=$v&$q"
            out=$(curl -s -D - -o /dev/null --max-time 20 \
                  -w 'CODE:%{http_code}\n' "http://127.0.0.1:$PORT/$P.svg?$q")
            code=$(printf '%s\n' "$out" | sed -n 's/^CODE://p')
            nv=$(printf '%s\n' "$out" | tr -d '\r' \
                 | awk -F': ' 'tolower($1)=="x-panel-version"{print $2}')
            [ -n "$nv" ] && v=$nv
            got=$((got + 1))
            if [ "$code" = "204" ]; then
                short=$((short + 1)); sleep 2; continue
            fi
            [ "$code" = "200" ] || { sleep 1; continue; }
            # Как страница: быстрый ответ - пауза секунда, долгий - сразу снова.
            dt=$(( ($(date +%s%N) - t0) / 1000000 ))
            [ "$dt" -lt 300 ] && sleep 1
        done
        echo "$got $short" > "$TMP/$i"
    ) &
    i=$((i + 1))
done
wait

cat "$TMP"/* | awk '{g += $1; s += $2} END {
    printf "ответов: %d, из них 204 (ждущих сверх лимита): %d\n", g, s }'
rm -rf "$TMP"
