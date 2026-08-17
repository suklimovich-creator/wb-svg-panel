#!/bin/sh
#
# Проверка перед коммитом: не уехало ли в репозиторий что-то личное.
#
#     sh check-secrets.sh
#
# Ищет имена контроллеров, адреса Wiren Board Cloud, идентификаторы Sprut.hub,
# пароли и приватные IP. Ничего не меняет — только показывает и объясняет.
#
# Возвращает 0, если чисто; 1, если есть находки.

set -e
cd "$(dirname "$0")"

FOUND=0

# Файлы под контролем git; если git ещё нет — просто всё, кроме .git.
# Сам этот скрипт пропускаем: в нём хранятся образцы того, что мы ищем.
list_files() {
    if [ -d .git ] && command -v git >/dev/null 2>&1; then
        git ls-files
    else
        find . -type f -not -path './.git/*' | sed 's|^\./||'
    fi | grep -v '^check-secrets\.sh$'
}

# scan <заголовок> <регулярка> <что делать> [что считать заглушкой]
scan() {
    title="$1"
    pattern="$2"
    advice="$3"
    ignore="${4:-}"

    hits=$(list_files | while read -r f; do
        [ -f "$f" ] || continue
        grep -InE "$pattern" "$f" 2>/dev/null | sed "s|^|$f:|"
    done)
    # заглушки вида wirenboard-xxxxxxxx — это документация, а не утечка
    if [ -n "$ignore" ] && [ -n "$hits" ]; then
        hits=$(printf '%s\n' "$hits" | grep -Ev "$ignore" || true)
    fi

    if [ -n "$hits" ]; then
        FOUND=1
        echo
        echo "!! $title"
        echo "$hits" | head -20 | sed 's/^/     /'
        n=$(echo "$hits" | wc -l)
        [ "$n" -gt 20 ] && echo "     ... и ещё $((n - 20))"
        echo "   -> $advice"
    else
        echo "ok   $title — чисто"
    fi
}

echo "Проверяю файлы репозитория на личные данные"
echo "==========================================="

scan "имя контроллера (wirenboard-XXXXXXXX)" \
     'wirenboard-[A-Za-z0-9]{6,}' \
     "замените на wirenboard-xxxxxxxx.local" \
     'wirenboard-[xX]+([^A-Za-z0-9]|$)'

scan "адрес Wiren Board Cloud" \
     '[A-Za-z0-9]{6,}\.(http|wb)\.wirenboard\.cloud' \
     "уберите или замените на <ваш-id>.http.wirenboard.cloud"

scan "идентификатор Sprut.hub" \
     'Sprut\.hub-[A-F0-9]{8,}' \
     "замените на Sprut.hub-XXXXXXXX — это идентификатор вашей установки"

scan "конкретные адреса в домашней сети" \
     '(192\.168\.[0-9]{1,3}\.[0-9]{1,3}|10\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3})(/32)?([^0-9/]|$)' \
     "в примерах оставляйте только подсети вида 192.168.0.0/16"

scan "похоже на пароль или токен" \
     '(password|passwd|token|api[_-]?key|secret)[[:space:]]*[:=][[:space:]]*["'"'"']?[A-Za-z0-9._@!$%-]{6,}' \
     "вынесите в config.yaml (он в .gitignore) или в переменные окружения"

scan "адрес электронной почты" \
     '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}' \
     "уберите, если это не намеренный контакт для связи" \
     '(\.local|@[A-Za-z0-9.-]*example|root@|user@)'

# config.yaml не должен попасть в индекс вообще
if [ -d .git ] && command -v git >/dev/null 2>&1; then
    if git ls-files --error-unmatch config.yaml >/dev/null 2>&1; then
        FOUND=1
        echo
        echo "!! config.yaml под контролем git"
        echo "   -> git rm --cached config.yaml    (он в .gitignore, но был добавлен раньше)"
    else
        echo "ok   config.yaml не в индексе"
    fi
fi

echo
echo "==========================================="
if [ "$FOUND" = "0" ]; then
    echo "Чисто. Можно публиковать."
    exit 0
fi

cat <<'MSG'
Есть находки — посмотрите список выше.

Часть из них может быть безобидной: подсети 192.168.0.0/16 в nginx-конфиге и
слово password в комментарии — это нормально. Скрипт намеренно шумный, лучше
лишний раз посмотреть глазами.

Помните: если что-то уже было запушено, удаления в новом коммите мало —
значение остаётся в истории. Меняйте пароль, а не только файл.
MSG
exit 1
