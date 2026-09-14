import hashlib

import psycopg

from .config import ROOT, db_dsn

MIGRATIONS_DIR = ROOT / "sql" / "migrations"


def connect() -> psycopg.Connection:
    return psycopg.connect(db_dsn(), autocommit=True)


def migrate(conn: psycopg.Connection) -> list[str]:
    conn.execute("create schema if not exists meta")
    conn.execute(
        """create table if not exists meta.schema_migrations (
               version    text primary key,
               checksum   char(64) not null,
               applied_at timestamptz not null default now())"""
    )
    applied = dict(conn.execute("select version, checksum from meta.schema_migrations").fetchall())
    done = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        sql = path.read_text(encoding="utf-8")
        checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
        if path.name in applied:
            if applied[path.name] != checksum:
                raise RuntimeError(
                    f"Миграция {path.name} изменена после применения. "
                    "Изменения схемы оформляются новым файлом миграции."
                )
            continue
        with conn.transaction():
            conn.execute(sql)
            conn.execute(
                "insert into meta.schema_migrations (version, checksum) values (%s, %s)",
                (path.name, checksum),
            )
        done.append(path.name)
    return done
