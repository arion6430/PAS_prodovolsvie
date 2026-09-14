from datetime import date, datetime, timedelta

import psycopg
import requests
from lxml import etree

from .common import SourceSchemaError, to_decimal
from .http import fetch, with_retries
from .journal import Journal
from .storage import save_payload, upsert_rows

CURRENCY_COLS = ["name", "eng_name", "nominal", "parent_code", "iso_num_code", "iso_char_code"]
RATE_COLS = ["nominal", "value", "vunit_rate"]


def _root(body: bytes, expected: str) -> etree._Element:
    try:
        root = etree.fromstring(body)
    except etree.XMLSyntaxError as exc:
        raise SourceSchemaError(f"ответ ЦБ не является XML: {body[:120]!r}") from exc
    if root.tag != expected:
        raise SourceSchemaError(f"ожидался корневой элемент {expected}, получен {root.tag}")
    return root


def _text(el: etree._Element, tag: str) -> str | None:
    value = el.findtext(tag)
    if value is None:
        return None
    return value.strip() or None


def parse_currencies(body: bytes) -> list[tuple]:
    rows = []
    for item in _root(body, "Valuta").iter("Item"):
        nominal = _text(item, "Nominal")
        rows.append((item.get("ID").strip(), _text(item, "Name"), _text(item, "EngName"),
                     int(nominal) if nominal else None, _text(item, "ParentCode"),
                     _text(item, "ISO_Num_Code"), _text(item, "ISO_Char_Code")))
    return rows


def parse_dynamic(body: bytes) -> list[tuple]:
    rows = []
    for rec in _root(body, "ValCurs").iter("Record"):
        nominal = _text(rec, "Nominal")
        rows.append((rec.get("Id"), datetime.strptime(rec.get("Date"), "%d.%m.%Y").date(),
                     int(nominal) if nominal else None,
                     to_decimal(rec.findtext("Value")), to_decimal(rec.findtext("VunitRate"))))
    return rows


def _recent_success(conn: psycopg.Connection, entity: str, currency: str | None, hours: int):
    return conn.execute(
        """select max(finished_at) from meta.load_log
           where source = 'cbr' and entity = %s and status = 'success'
             and (%s::text is null or params ->> 'currency' = %s)
             and finished_at > now() - make_interval(hours => %s)""",
        (entity, currency, currency, hours),
    ).fetchone()[0]


def run(conn: psycopg.Connection, journal: Journal, session: requests.Session, cfg: dict,
        force: bool = False) -> None:
    c = cfg["sources"]["cbr"]
    timeout = cfg["http"]["timeout_sec"]
    retries, backoff = cfg["http"]["retries"], cfg["http"]["backoff_sec"]
    hours = c["min_interval_hours"]
    rate_limit_msg = "ограничение частоты: успешная загрузка была {:%Y-%m-%d %H:%M}, интервал {} ч (используйте --force)"

    url = f"{c['base_url']}/XML_valFull.asp"
    with journal.step("cbr", "currencies", {"url": url}) as st:
        last = None if force else _recent_success(conn, "currencies", None, hours)
        if last:
            st.skip(rate_limit_msg.format(last, hours))
        else:
            response, attempts = with_retries(lambda: fetch(session, "GET", url, timeout=timeout),
                                              retries, backoff, "cbr currencies")
            st.record_response(response)
            st.attempts += attempts - 1
            st.payload_id = save_payload(conn, st.load_id, "cbr", "currencies", url, None, response)
            rows = parse_currencies(response.content)
            st.apply(upsert_rows(conn, "raw.cbr_currency", ["currency_id"], CURRENCY_COLS, rows, st.payload_id))

    url = f"{c['base_url']}/XML_dynamic.asp"
    start_date = date.fromisoformat(str(c["start_date"]))
    for currency in c["currencies"]:
        with journal.step("cbr", "rates", {"currency": currency}) as st:
            last = None if force else _recent_success(conn, "rates", currency, hours)
            if last:
                st.skip(rate_limit_msg.format(last, hours))
                continue
            max_date = conn.execute("select max(rate_date) from raw.cbr_rate where currency_id = %s",
                                    (currency,)).fetchone()[0]
            date_from = max(start_date, max_date - timedelta(days=c["overlap_days"])) if max_date else start_date
            date_to = date.today()
            st.params.update(date_from=date_from.isoformat(), date_to=date_to.isoformat(),
                             mode="incremental" if max_date else "initial")
            query = {"date_req1": date_from.strftime("%d/%m/%Y"), "date_req2": date_to.strftime("%d/%m/%Y"),
                     "VAL_NM_RQ": currency}
            response, attempts = with_retries(lambda: fetch(session, "GET", url, params=query, timeout=timeout),
                                              retries, backoff, f"cbr {currency}")
            st.record_response(response)
            st.attempts += attempts - 1
            st.payload_id = save_payload(conn, st.load_id, "cbr", "rates", response.url, st.params, response)
            rows = parse_dynamic(response.content)
            st.apply(upsert_rows(conn, "raw.cbr_rate", ["currency_id", "rate_date"], RATE_COLS, rows, st.payload_id))
