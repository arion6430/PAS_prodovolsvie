import gzip
import hashlib
from dataclasses import dataclass

import psycopg
import requests
from psycopg.types.json import Jsonb


@dataclass
class UpsertResult:
    parsed: int
    inserted: int
    updated: int
    unchanged: int
    duplicates: int


def save_payload(conn: psycopg.Connection, load_id: int, source: str, entity: str,
                 url: str, params: dict | None, response: requests.Response) -> int:
    body = response.content
    return conn.execute(
        """insert into raw.payload (load_id, source, entity, request_url, request_params, http_status,
                                    content_type, byte_size, sha256, body_gz)
           values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           returning payload_id""",
        (load_id, source, entity, url, Jsonb(params) if params else None, response.status_code,
         response.headers.get("Content-Type"), len(body), hashlib.sha256(body).hexdigest(), gzip.compress(body)),
    ).fetchone()[0]


def _pairs(alias: str, cols: list[str]) -> str:
    return ", ".join(f"'{c}', {alias}.{c}" for c in cols)


def upsert_rows(conn: psycopg.Connection, table: str, key_cols: list[str], val_cols: list[str],
                rows: list[tuple], payload_id: int) -> UpsertResult:
    cols = key_cols + val_cols
    col_list = ", ".join(cols)
    key_match = " and ".join(f"t.{k} = i.{k}" for k in key_cols)
    changed = f"({', '.join('t.' + v for v in val_cols)}) is distinct from ({', '.join('i.' + v for v in val_cols)})"

    with conn.transaction():
        conn.execute(f"create temp table _incoming on commit drop as select {col_list} from {table} with no data")
        with conn.cursor() as cur, cur.copy(f"copy _incoming ({col_list}) from stdin") as copy:
            for row in rows:
                copy.write_row(row)
        duplicates = conn.execute(
            "delete from _incoming a using _incoming b where a.ctid < b.ctid and "
            + " and ".join(f"a.{k} = b.{k}" for k in key_cols)
        ).rowcount
        conn.execute(
            f"""insert into raw.revision (table_name, record_key, old_values, new_values, payload_id)
                select %s, jsonb_build_object({_pairs('i', key_cols)}), jsonb_build_object({_pairs('t', val_cols)}),
                       jsonb_build_object({_pairs('i', val_cols)}), %s
                from _incoming i join {table} t on {key_match}
                where {changed}""",
            (table, payload_id),
        )
        updated = conn.execute(
            f"""update {table} t
                set {', '.join(f'{v} = i.{v}' for v in val_cols)}, last_payload_id = %s, updated_at = now()
                from _incoming i
                where {key_match} and {changed}""",
            (payload_id,),
        ).rowcount
        inserted = conn.execute(
            f"""insert into {table} ({col_list}, first_payload_id, last_payload_id)
                select {col_list}, %s, %s from _incoming i
                where not exists (select 1 from {table} t where {key_match})""",
            (payload_id, payload_id),
        ).rowcount
    unchanged = len(rows) - duplicates - inserted - updated
    return UpsertResult(len(rows), inserted, updated, unchanged, duplicates)
