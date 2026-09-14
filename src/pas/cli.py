import argparse
import logging

import psycopg

from . import cbr, db, fedstat
from .config import load_config
from .http import make_session
from .journal import Journal


def _print_table(conn: psycopg.Connection, title: str, sql: str) -> None:
    cur = conn.execute(sql)
    cols = [d.name for d in cur.description]
    rows = [["" if v is None else str(v) for v in r] for r in cur.fetchall()]
    widths = [min(60, max([len(c)] + [len(r[i]) for r in rows])) for i, c in enumerate(cols)]
    print(f"\n== {title}")
    print("  ".join(c.ljust(w) for c, w in zip(cols, widths)))
    print("  ".join("-" * w for w in widths))
    for r in rows:
        print("  ".join(v[:w].ljust(w) for v, w in zip(r, widths)))


def print_status(conn: psycopg.Connection) -> None:
    _print_table(conn, "Последние запуски", "select * from meta.v_run order by run_id desc limit 5")
    _print_table(conn, "Журнал загрузок (последние 15 шагов)",
                 """select load_id, run_id, source, entity, status, started, sec, http_status as http, attempts,
                           rows_parsed as parsed, rows_inserted as ins, rows_updated as upd,
                           rows_unchanged as same, message
                    from meta.v_load_log order by load_id desc limit 15""")
    _print_table(conn, "Свежесть данных в raw-слое", "select * from meta.v_freshness order by source, dataset")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pas", description="ПАС: цены на плодоовощную продукцию")
    parser.add_argument("--config", help="путь к YAML-конфигурации (по умолчанию config/config.yaml)")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db", help="применить миграции схемы БД")
    load = sub.add_parser("load", help="загрузить данные из источников")
    load.add_argument("--source", choices=["all", "cbr", "fedstat"], default="all")
    load.add_argument("--force", action="store_true", help="ЦБ: игнорировать ограничение 1 запрос в сутки")
    load.add_argument("--full", action="store_true", help="fedstat: перезагрузить все годы с start_year")
    load.add_argument("--from-year", type=int, help="fedstat: перезагрузить годы начиная с указанного")
    load.add_argument("--max-requests", type=int, help="fedstat: не более N запросов выгрузки за запуск")
    sub.add_parser("status", help="журнал загрузок и свежесть данных")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("urllib3").setLevel(logging.INFO if args.verbose else logging.WARNING)
    cfg = load_config(args.config)

    with db.connect() as conn:
        if args.command == "init-db":
            applied = db.migrate(conn)
            print("Применены миграции: " + (", ".join(applied) if applied else "нет новых"))
            return 0
        if args.command == "status":
            print_status(conn)
            return 0

        journal = Journal(conn, "load", {k: v for k, v in vars(args).items() if k != "command"})
        session = make_session(cfg["http"])
        if args.source in ("all", "cbr"):
            cbr.run(conn, journal, session, cfg, force=args.force)
        if args.source in ("all", "fedstat"):
            fedstat.run(conn, journal, session, cfg, full=args.full, from_year=args.from_year,
                        max_requests=args.max_requests)
        status = journal.finish()
        print(f"Запуск {journal.run_id} завершён со статусом {status}: {dict(journal.counts)}")
        return 0 if status == "success" else 1
