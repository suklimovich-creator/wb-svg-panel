#!/bin/sh
#
# Подготовка файлов репозитория для загрузки в проект Claude.
#
#     sh tools/make-upload.sh                       папка ../wb-svg-panel-upload
#     sh tools/make-upload.sh /c/dev/upload         своя папка
#     sh tools/make-upload.sh --changed             только изменившиеся файлы
#     sh tools/make-upload.sh --fetch root@wirenboard.local
#
# Проект не принимает ни каталогов, ни части расширений, поэтому путь
# складывается в имя файла: разделители становятся дефисами, точки —
# подчёркиваниями, в конце .txt.
#
#     wbpanel/roles.py        ->  wbpanel-roles_py.txt
#     templates/tiles/ac.j2   ->  templates-tiles-ac_j2.txt
#     wb-svg-panel.service    ->  wb-svg-panel_service.txt
#
# Список файлов берётся у git, а не пишется здесь руками: список, который
# правят отдельно от репозитория, отстаёт молча. Ровно так однажды и вышло —
# новый шаблон плитки просто не попал в выгрузку, и разговор пошёл о коде,
# которого у собеседника не было.
#
# Берутся и файлы, ещё не добавленные в индекс: плитка, написанная час
# назад, нужна в разговоре именно сейчас, а не после коммита. Всё, что
# перечислено в .gitignore, при этом пропускается — там config.yaml с
# адресами и токенами, ему в переписку нельзя.
#
# Рядом кладётся MANIFEST.txt: версия, коммит и md5 каждого файла. По нему
# в следующем разговоре видно, что загружено, а что осталось от прошлого
# релиза. Имя файла в проекте от переименования не меняет содержимого,
# поэтому md5 сходится напрямую.
#
# С ключом --changed копируются только файлы, изменившиеся с прошлой
# выгрузки: их обычно два-три из полусотни. Манифест при этом по-прежнему
# перечисляет ВСЕ файлы, а не только скопированные, — иначе не отличить
# свежую копию в проекте от оставшейся с прошлого релиза. Скопированные
# помечены звёздочкой.
#
# Суммы прошлой выгрузки лежат в .git/make-upload-state: git туда не
# заглядывает, а папка выгрузки очищается при каждом запуске.
#
set -eu

# --------------------------------------------------------------------------
# Что живёт только на контроллере
# --------------------------------------------------------------------------
# В репозитории этих файлов нет и быть не должно: config.yaml в .gitignore,
# а мосты — соседние проекты. Формат строки: путь и приставка к имени,
# «-» вместо приставки означает «без неё».
#
# Правьте здесь. Скрипт эти файлы не скачивает сам, если не позвать --fetch,
# а только показывает список и готовые команды.
CONTROLLER_FILES='
/mnt/data/wb-svg-panel/config.yaml          -
/mnt/data/wb-tv/wb_tv.py                    wb-tv
/mnt/data/wb-tv/tv_drivers.py               wb-tv
/mnt/data/wb-tv/probe-tv.py                 wb-tv
/mnt/data/wb-tv/check-tv.sh                 wb-tv
/mnt/data/wb-tv/install.sh                  wb-tv
/mnt/data/wb-tv/wb-tv.service               wb-tv
/mnt/data/wb-tv/config.example.json         wb-tv
/mnt/data/wb-tv/STATE-tv.md                 wb-tv
/mnt/data/wb-tv/remote-example.svg          wb-tv
/mnt/data/wb-haier/wb_haier_bridge.py       wb-haier
/mnt/data/wb-haier/ha_stub.py               wb-haier
/mnt/data/wb-haier/patch_vendor.py          wb-haier
/mnt/data/wb-haier/dump_device.py           wb-haier
/mnt/data/wb-haier/check-ac.sh              wb-haier
/mnt/data/wb-haier/wb-haier.service         wb-haier
/mnt/data/wb-haier/STATE-haier.md           wb-haier
'

# Что из репозитория в проект не отправляем. Двоичное там всё равно не
# читается, а лицензия и служебные файлы git только занимают место в списке.
SKIP_GLOBS='*.png *.jpg *.jpeg *.gif *.ico *.pdf *.zip *.gz *.woff *.woff2
            *.pyc LICENSE .gitignore .gitattributes'

# --------------------------------------------------------------------------
# Разбор ключей
# --------------------------------------------------------------------------
OUT=""
HOST="${WB_HOST:-}"
FETCH=0
CHANGED_ONLY=0

while [ $# -gt 0 ]; do
    case "$1" in
        --changed) CHANGED_ONLY=1 ;;
        --fetch)
            FETCH=1
            case "${2:-}" in
                ""|-*) ;;
                *) HOST="$2"; shift ;;
            esac
            ;;
        -h|--help)
            sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        -*) echo "Неизвестный ключ: $1 (см. --help)" >&2; exit 2 ;;
        *)  OUT="$1" ;;
    esac
    shift
done

# --------------------------------------------------------------------------
# Где репозиторий и куда складывать
# --------------------------------------------------------------------------
ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"

if [ ! -f wbpanel/__init__.py ] && [ ! -f panel.py ]; then
    echo "ОШИБКА: $ROOT не похож на wb-svg-panel." >&2
    echo "  Скрипт лежит в tools/ и считает корнем каталог уровнем выше." >&2
    exit 1
fi

[ -n "$OUT" ] || OUT="$(dirname "$ROOT")/wb-svg-panel-upload"
mkdir -p "$OUT"

# Старое содержимое убираем: файл, переименованный в репозитории, иначе
# останется в папке под прежним именем и уедет в проект второй копией.
find "$OUT" -maxdepth 1 -type f -name '*.txt' -exec rm -f {} + 2>/dev/null || true

VERSION=$(sed -n 's/^__version__ *= *"\(.*\)"/\1/p' wbpanel/__init__.py 2>/dev/null || true)
[ -n "$VERSION" ] || VERSION="неизвестна"
if [ -d .git ] && command -v git >/dev/null 2>&1; then
    COMMIT=$(git describe --tags --always --dirty 2>/dev/null || echo "?")
    BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "?")
else
    COMMIT="без git"
    BRANCH="—"
fi

# --------------------------------------------------------------------------
# Имя файла в проекте
# --------------------------------------------------------------------------
upload_name() {
    printf '%s.txt' "$(printf '%s' "$1" | tr '/' '-' | sed 's/\./_/g')"
}

skip_it() {
    base=$(basename "$1")
    for pat in $SKIP_GLOBS; do
        # без кавычек справа: это сравнение с образцом, а не со строкой
        case "$base" in $pat) return 0 ;; esac
    done
    case "$1" in
        .git/*|*/__pycache__/*|__pycache__/*) return 0 ;;
    esac
    return 1
}

sum_of() {
    if command -v md5sum >/dev/null 2>&1; then
        md5sum "$1" | cut -d' ' -f1
    elif command -v openssl >/dev/null 2>&1; then
        openssl md5 -r "$1" | cut -d' ' -f1
    else
        echo "-"
    fi
}

# --------------------------------------------------------------------------
# Список файлов репозитория
# --------------------------------------------------------------------------
# --others добавляет ещё не закоммиченное, --exclude-standard отсекает то,
# что перечислено в .gitignore. Без первого в выгрузку не попадает работа
# последнего часа, без второго туда уезжает config.yaml.
if [ -d .git ] && command -v git >/dev/null 2>&1; then
    FILES=$(git ls-files --cached --others --exclude-standard | sort -u)
else
    echo "внимание: git не найден, беру всё подряд из каталога" >&2
    FILES=$(find . -type f -not -path './.git/*' | sed 's|^\./||' | sort)
fi

# Перебор идёт по словам, поэтому имя с пробелом развалилось бы на два и
# файл потерялся бы молча. В репозитории таких имён нет, но сказать об этом
# надо вслух, а не выяснять потом, почему плитки нет в разговоре.
case "$FILES" in
    *\ *) echo "внимание: в именах есть пробелы, такие файлы пропущены:" >&2
          printf '%s\n' "$FILES" | grep ' ' | sed 's/^/    /' >&2 ;;
esac

# --------------------------------------------------------------------------
# Что изменилось с прошлой выгрузки
# --------------------------------------------------------------------------
# Состояние лежит в .git: git туда не заглядывает, а папка выгрузки
# очищается при каждом запуске и хранить в ней ничего нельзя.
STATE="$ROOT/.git/make-upload-state"
[ -d "$ROOT/.git" ] || STATE="$OUT/../.make-upload-state"
[ -f "$STATE" ] || : > "$STATE"

was_sum() {
    grep -F "  $1" "$STATE" 2>/dev/null | head -1 | cut -d' ' -f1
}

# Файл был в прошлой выгрузке, а теперь его нет: в проекте он останется и
# будет выглядеть живым. Сказать об этом надо, стереть - руками.
GONE=""
while read -r line; do
    [ -n "${line:-}" ] || continue
    old_path=${line#*  }
    [ -f "$old_path" ] || GONE="$GONE $(upload_name "$old_path")"
done < "$STATE"

MANIFEST="$OUT/MANIFEST.txt"
{
    echo "wb-svg-panel $VERSION"
    echo "коммит:  $COMMIT (ветка $BRANCH)"
    echo "собрано: $(date '+%Y-%m-%d %H:%M')"
    echo
    if [ -n "$GONE" ]; then
        echo "удалено из репозитория, стереть в проекте:"
        for n in $GONE; do echo "    $n"; done
        echo
    fi
    if [ "$CHANGED_ONLY" = "1" ]; then
        echo "выгружены только изменившиеся файлы — они помечены звёздочкой."
        echo "Остальные строки нужны для сверки: если md5 не сходится с тем,"
        echo "что лежит в проекте, там копия от прошлого релиза."
        echo
    fi
    echo "  md5                               имя в проекте / путь в репозитории"
} > "$MANIFEST"

echo "wb-svg-panel $VERSION — $COMMIT (ветка $BRANCH)"
echo "==> файлы репозитория -> $OUT"

COUNT=0
SAME=0
SKIPPED=""
: > "$STATE.new"
for f in $FILES; do
    [ -f "$f" ] || continue
    if skip_it "$f"; then
        SKIPPED="$SKIPPED $f"
        continue
    fi
    name=$(upload_name "$f")
    sum=$(sum_of "$f")
    echo "$sum  $f" >> "$STATE.new"

    mark=" "
    if [ "$CHANGED_ONLY" = "0" ] || [ "$sum" != "$(was_sum "$f")" ]; then
        cp -- "$f" "$OUT/$name"
        COUNT=$((COUNT + 1))
        mark="*"
    else
        SAME=$((SAME + 1))
    fi
    # Манифест перечисляет все файлы, а не только скопированные: иначе не
    # отличить свежую копию в проекте от оставшейся с прошлого релиза.
    printf '%s %s  %-42s %s\n' "$mark" "$sum" "$name" "$f" >> "$MANIFEST"
done
mv -f "$STATE.new" "$STATE"

if [ "$CHANGED_ONLY" = "1" ]; then
    echo "    изменилось: $COUNT, без изменений: $SAME"
    [ "$COUNT" = "0" ] && echo "    выгружать нечего — в проекте всё свежее"
else
    echo "    подготовлено файлов: $COUNT"
fi
[ -n "$SKIPPED" ] && echo "    пропущено:$SKIPPED"
if [ -n "$GONE" ]; then
    echo "    УДАЛЕНО из репозитория, стереть в проекте вручную:"
    for n in $GONE; do echo "        $n"; done
fi
echo "    манифест: MANIFEST.txt (md5 всех файлов, скопированные со звёздочкой)"

# --------------------------------------------------------------------------
# Файлы с контроллера
# --------------------------------------------------------------------------
echo
echo "==> взять с контроллера — в репозитории их нет"
echo

printf '%s\n' "$CONTROLLER_FILES" | while read -r path prefix; do
    [ -n "${path:-}" ] || continue
    base=$(basename "$path")
    if [ "${prefix:--}" = "-" ]; then
        name=$(upload_name "$base")
    else
        name=$(upload_name "$prefix/$base")
    fi
    printf '    %-42s -> %s\n' "$path" "$name"
done

echo
if [ "$FETCH" = "1" ] && [ -n "$HOST" ]; then
    echo "==> забираю с $HOST"
    printf '%s\n' "$CONTROLLER_FILES" | while read -r path prefix; do
        [ -n "${path:-}" ] || continue
        base=$(basename "$path")
        if [ "${prefix:--}" = "-" ]; then
            name=$(upload_name "$base")
        else
            name=$(upload_name "$prefix/$base")
        fi
        if scp -q "$HOST:$path" "$OUT/$name" 2>/dev/null; then
            printf '    ok:  %s\n' "$name"
            printf '%s  %-42s %s\n' "$(sum_of "$OUT/$name")" "$name" \
                   "$HOST:$path" >> "$MANIFEST"
        else
            printf '    НЕТ: %s (%s)\n' "$name" "$path"
        fi
    done
else
    echo "    Скопировать всё разом:"
    echo
    printf '%s\n' "$CONTROLLER_FILES" | while read -r path prefix; do
        [ -n "${path:-}" ] || continue
        base=$(basename "$path")
        if [ "${prefix:--}" = "-" ]; then
            name=$(upload_name "$base")
        else
            name=$(upload_name "$prefix/$base")
        fi
        printf '      scp %s:%s "%s/%s"\n' "${HOST:-root@КОНТРОЛЛЕР}" "$path" \
               "$OUT" "$name"
    done
    echo
    echo "    или сразу: sh tools/make-upload.sh --fetch root@КОНТРОЛЛЕР"
fi

echo
if [ "$CHANGED_ONLY" = "1" ]; then
    echo "Загрузите содержимое папки в проект: файлы с теми же именами"
    echo "заменятся, остальные останутся с прошлого раза — так и задумано."
    echo "Сверить их поможет MANIFEST.txt."
else
    echo "Перед загрузкой удалите из проекта прежние файлы: имена совпадут не все,"
    echo "и рядом со свежими останутся копии от старого релиза."
fi
