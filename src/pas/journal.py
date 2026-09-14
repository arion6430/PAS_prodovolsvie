import logging
import platform
import subprocess
from collections import Counter
from contextlib import contextmanager

import psycopg
import requests
from psycopg.types.json import Jsonb

from .config import ROOT
from .http import attempts_of
from .storage import UpsertResult

log = logging.getLogger(__name__)


class StepStats:
    def __init__(self, load_id: int, params: dict):
        self.load_id = load_id
        self.params = params
        self.status = "success"
        self.note: str | None = None
        self.http_status: int | None = None
        self.attempts: int | None = None
        self.byte_size: int | None = None
        self.payload_id: int | None = None
        self.rows_parsed = 0
        self.rows_inserted = 0
        self.rows_updated = 0
        self.rows_unchanged = 0

    def record_response(self, response: requests.Response) -> None:
        self.http_status = response.status_code
        self.attempts = attempts_of(response)
        self.byte_size = len(response.content)

    def apply(self, result: UpsertResult) -> None:
        self.rows_parsed += result.parsed
        self.rows_inserted += result.inserted
        self.rows_updated += result.updated
        self.rows_unchanged += result.unchanged
        if result.duplicates:
            self.note = f"дубликатов ключа в ответе источника: {result.duplicates}"

    def skip(self, reason: str) -> None:
        self.status = "skipped"
        self.note = reason


def _git_commit() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                             capture_output=True, text=True, timeout=5)
        return out.stdout.strip() or None
    except OSError:
        return None


class Journal:
    def __init__(self, conn: psycopg.Connection, command: str, args: dict):
        self.conn = conn
        self.counts: Counter[str] = Counter()
        conn.execute(
            """update meta.load_log
               set status = 'aborted', finished_at = now(), error_message = 'процесс прерван до завершения шага'
               where status = 'running' and started_at < now() - interval '1 hour'"""
        )
        self.run_id = conn.execute(
            "insert into meta.etl_run (command, args, git_commit, host) values (%s, %s, %s, %s) returning run_id",
            (command, Jsonb(args), _git_commit(), platform.node()),
        ).fetchone()[0]

    @contextmanager
    def step(self, source: str, entity: str, params: dict | None = None):
        params = dict(params or {})
        load_id = self.conn.execute(
            "insert into meta.load_log (run_id, source, entity, params) values (%s, %s, %s, %s) returning load_id",
            (self.run_id, source, entity, Jsonb(params)),
        ).fetchone()[0]
        st = StepStats(load_id, params)
        error = None
        try:
            yield st
        except Exception as exc:
            st.status = "failed"
            error = f"{type(exc).__name__}: {exc}"
            log.error("%s/%s %s: %s", source, entity, params, error)
            log.debug("traceback", exc_info=True)
        self.counts[st.status] += 1
        self.conn.execute(
            """update meta.load_log
               set status = %s, finished_at = now(), params = %s, http_status = %s, attempts = %s, byte_size = %s,
                   payload_id = %s, rows_parsed = %s, rows_inserted = %s, rows_updated = %s, rows_unchanged = %s,
                   note = %s, error_message = %s
               where load_id = %s""",
            (st.status, Jsonb(st.params), st.http_status, st.attempts, st.byte_size, st.payload_id,
             st.rows_parsed, st.rows_inserted, st.rows_updated, st.rows_unchanged, st.note, error, load_id),
        )
        if st.status != "failed":
            log.info("%s/%s %s: %s, строк %d (новых %d, изменённых %d, без изменений %d)%s",
                     source, entity, st.params, st.status, st.rows_parsed, st.rows_inserted,
                     st.rows_updated, st.rows_unchanged, f" — {st.note}" if st.note else "")

    def finish(self) -> str:
        failed = self.counts["failed"]
        ok = self.counts["success"] + self.counts["skipped"]
        status = "success" if not failed else ("partial" if ok else "failed")
        self.conn.execute(
            """update meta.etl_run
               set finished_at = now(), status = %s, steps_success = %s, steps_skipped = %s, steps_failed = %s
               where run_id = %s""",
            (status, self.counts["success"], self.counts["skipped"], failed, self.run_id),
        )
        return status
