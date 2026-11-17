"""Local entry point for explicitly migrating or restoring the configuration."""

import argparse
from dataclasses import asdict
import json
import sys

from .const import CONFIG_PATH
from .editor_documents import DocumentError
from .editor_revisions import RevisionStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=CONFIG_PATH)
    commands = parser.add_subparsers(dest="command", required=True)
    migration = commands.add_parser("migrate", help="проверить миграцию")
    migration.add_argument("--apply", action="store_true", help="опубликовать миграцию")
    commands.add_parser("history", help="показать историю и восстановить прерванную запись")
    rollback = commands.add_parser("rollback", help="опубликовать ревизию из истории")
    rollback.add_argument("revision")
    args = parser.parse_args()
    try:
        store = RevisionStore(args.config)
        if args.command == "history":
            out = [asdict(revision) for revision in store.history()]
        elif args.command == "rollback":
            out = asdict(store.rollback(args.revision))
        else:
            draft = store.migration()
            if args.apply:
                out = asdict(store.publish(draft, migrate=True))
            else:
                out = store.inspect(draft, migrate=True)
    except DocumentError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except OSError:
        print("Ошибка доступа или записи файлов конфигурации", file=sys.stderr)
        return 1
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
