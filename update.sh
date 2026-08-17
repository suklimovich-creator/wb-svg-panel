#!/bin/sh
#
# Обновление wb-svg-panel до последнего опубликованного релиза.
#
#     sh /mnt/data/wb-svg-panel/update.sh              последний релиз
#     sh /mnt/data/wb-svg-panel/update.sh v0.3.0       конкретная версия
#     sh /mnt/data/wb-svg-panel/update.sh --check      только посмотреть, что нового
#
# Промежуточные коммиты из main сюда не попадают: скрипт переключается
# только на теги вида vX.Y.Z. Если после обновления демон не поднялся —
# автоматически возвращает предыдущую версию.
#
# config.yaml не трогается никогда: он в .gitignore и принадлежит контроллеру.

set -e
cd "$(dirname "$0")"

CHECK_ONLY=0
WANT=""
case "$1" in
    --check) CHECK_ONLY=1 ;;
    "")      ;;
    -*)      echo "Неизвестный ключ: $1"; exit 2 ;;
    *)       WANT="$1" ;;
esac

if [ ! -d .git ]; then
    cat <<'MSG'
ОШИБКА: это не рабочая копия git.

Похоже, файлы просто скопированы. Чтобы обновляться командой, разверните
проект заново — свой config.yaml при этом сохраните:

    cd /mnt/data
    mv wb-svg-panel wb-svg-panel.old
    git clone https://github.com/USER/wb-svg-panel.git
    cp wb-svg-panel.old/config.yaml wb-svg-panel/
    sh wb-svg-panel/install.sh
MSG
    exit 1
fi

# --------------------------------------------------------------------------
# Что стоит сейчас и что доступно
# --------------------------------------------------------------------------
NOW=$(git describe --tags --exact-match 2>/dev/null || git rev-parse --short HEAD)

echo "==> смотрю, что опубликовано"
git fetch --tags --prune --quiet

# Сортировка по версии, а не по алфавиту: иначе v0.10.0 окажется старше v0.9.0
LATEST=$(git tag -l 'v*' --sort=-v:refname | head -1)
if [ -z "$LATEST" ]; then
    echo "    релизов пока нет — в репозитории ни одного тега vX.Y.Z"
    exit 1
fi

TARGET="${WANT:-$LATEST}"
if ! git rev-parse -q --verify "refs/tags/$TARGET" >/dev/null; then
    echo "    нет такой версии: $TARGET"
    echo "    доступны: $(git tag -l 'v*' --sort=-v:refname | head -5 | tr '\n' ' ')"
    exit 1
fi

echo "    установлено: $NOW"
echo "    последний релиз: $LATEST"

if [ "$NOW" = "$TARGET" ]; then
    echo
    echo "Уже стоит $TARGET — делать нечего."
    exit 0
fi

# --------------------------------------------------------------------------
# Что изменится
# --------------------------------------------------------------------------
echo
echo "==> что нового в $TARGET"
if git rev-parse -q --verify "refs/tags/$NOW" >/dev/null 2>&1; then
    git log --oneline --no-decorate "$NOW..$TARGET" | sed 's/^/    /' || true
    CHANGED=$(git diff --name-only "$NOW" "$TARGET")
else
    git log --oneline --no-decorate -10 "$TARGET" | sed 's/^/    /'
    CHANGED=""
fi

if [ "$CHECK_ONLY" = "1" ]; then
    echo
    echo "Это была только проверка. Обновиться:  sh $0"
    exit 0
fi

# Местные правки — обычно кто-то чинил файл прямо на контроллере и забыл.
# Молча затирать такое нельзя.
if ! git diff --quiet HEAD -- 2>/dev/null; then
    echo
    echo "ОШИБКА: в рабочей копии есть несохранённые правки:"
    git diff --name-only HEAD | sed 's/^/    /'
    cat <<'MSG'

    Либо верните файлы к исходному виду:   git checkout -- <файл>
    либо сохраните правки:                 git stash
    и запустите скрипт снова.

    (config.yaml в этом списке быть не может — он в .gitignore.)
MSG
    exit 1
fi

# --------------------------------------------------------------------------
# Переключение
# --------------------------------------------------------------------------
echo
echo "==> ставлю $TARGET"
git checkout --quiet "$TARGET"

# Полная переустановка нужна, только если поменялось окружение: юнит,
# конфиги nginx или сам установщик. В остальных случаях хватает рестарта.
NEED_INSTALL=0
for f in install.sh wb-svg-panel.service nginx-panel-location.conf nginx-wb-svg-panel.conf; do
    case "$CHANGED" in *"$f"*) NEED_INSTALL=1 ;; esac
done
[ -z "$CHANGED" ] && NEED_INSTALL=1

if [ "$NEED_INSTALL" = "1" ]; then
    echo "    менялось окружение — полная установка"
    sh install.sh >/dev/null
else
    echo "    рестарт демона"
    systemctl restart wb-svg-panel
fi

# --------------------------------------------------------------------------
# Проверка и откат
# --------------------------------------------------------------------------
echo
echo "==> проверяю"
sleep 3
i=0
OK=0
while [ "$i" -lt 5 ]; do
    if curl -fsS --max-time 3 http://127.0.0.1:8088/healthz >/dev/null 2>&1; then
        OK=1
        break
    fi
    i=$((i + 1))
    sleep 2
done

if [ "$OK" = "1" ]; then
    echo "    демон отвечает"
    echo
    echo "Готово: $NOW -> $TARGET"
    echo "Откатиться, если что-то не так:  sh $0 $NOW"
    exit 0
fi

echo "    демон не отвечает — возвращаю $NOW"
git checkout --quiet "$NOW"
if [ "$NEED_INSTALL" = "1" ]; then
    sh install.sh >/dev/null
else
    systemctl restart wb-svg-panel
fi
sleep 3
if curl -fsS --max-time 3 http://127.0.0.1:8088/healthz >/dev/null 2>&1; then
    echo "    откат удался, работает $NOW"
else
    echo "    откат не помог — смотрите:  journalctl -u wb-svg-panel -n 50"
fi
echo
echo "Версия $TARGET не поднялась. Логи неудачной попытки:"
echo "    journalctl -u wb-svg-panel --since '2 minutes ago'"
exit 1
