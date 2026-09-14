import re
import time
from dataclasses import dataclass

import psycopg
import requests
from lxml import etree

from .common import SourceSchemaError, to_decimal
from .http import fetch, with_retries
from .journal import Journal
from .storage import save_payload, upsert_rows

NS = {"g": "http://www.SDMX.org/resources/SDMXML/schemas/v1_0/generic"}
OBS_KEY = ["okato_code", "product_code", "obs_year", "period_label"]
OBS_VALS = ["week_no", "value", "value_raw", "unit"]

_ITEM_RE = re.compile(r"(\d+):\s*\{\s*title:\s*'((?:[^'\\]|\\.)*)',\s*(\w+):")
_TOKEN_RE = re.compile(r'id="downloadTokenHolder">.*?name="token" value="([^"]+)"', re.S)
_WEEK_RE = re.compile(r"^\s*(\d{1,2})\s*неделя", re.I)


@dataclass
class IndicatorMeta:
    filters: dict[int, dict]
    left: list[int]
    top: list[int]
    token: str


def _unescape(text: str) -> str:
    return re.sub(r"\\u([0-9A-Fa-f]{4})", lambda m: chr(int(m.group(1), 16)), text).replace("\\'", "'")


def _int_list(text: str, name: str) -> list[int]:
    m = re.search(name + r":\s*\[([^\]]*)\]", text)
    if not m:
        raise SourceSchemaError(f"на странице показателя нет блока {name}")
    return [int(x) for x in re.findall(r"\d+", m.group(1))]


def parse_token(html: str) -> str:
    m = _TOKEN_RE.search(html)
    if not m:
        raise SourceSchemaError("на странице показателя не найден токен выгрузки (downloadTokenHolder)")
    return m.group(1)


def parse_indicator_page(html: str) -> IndicatorMeta:
    start, end = html.find("filters:"), html.find("left_columns")
    if start < 0 or end < start:
        raise SourceSchemaError("на странице показателя не найден блок описания фильтров")
    filters: dict[int, dict] = {}
    current = None
    for m in _ITEM_RE.finditer(html, start, end):
        item_id, title, next_key = int(m.group(1)), _unescape(m.group(2)), m.group(3)
        if next_key == "order":
            if current is None:
                raise SourceSchemaError("значение фильтра встретилось раньше самого фильтра")
            current["values"][item_id] = title
        else:
            current = filters[item_id] = {"title": title, "values": {}}
    tail = html[end:]
    return IndicatorMeta(filters, _int_list(tail, "left_columns"), _int_list(tail, "top_columns"), parse_token(html))


def build_download_form(meta: IndicatorMeta, indicator_id: int, token: str,
                        selection: dict[int, list[int]]) -> list[tuple[str, str]]:
    form = [("id", str(indicator_id)), ("title", f"indicator {indicator_id}"),
            ("struts.token.name", "token"), ("token", token)]
    layout = set(meta.left) | set(meta.top)
    form += [("lineObjectIds", str(f)) for f in meta.filters if f not in layout]
    form += [("lineObjectIds", str(f)) for f in meta.left]
    form += [("columnObjectIds", str(f)) for f in meta.top]
    for fid, flt in meta.filters.items():
        form += [("selectedFilterIds", f"{fid}_{vid}") for vid in selection.get(fid, flt["values"])]
    return form


def parse_week(label: str | None) -> int | None:
    m = _WEEK_RE.match(label or "")
    return int(m.group(1)) if m else None


def parse_sdmx(body: bytes, concepts: dict) -> tuple[list[tuple], list[tuple]]:
    try:
        root = etree.fromstring(body, parser=etree.XMLParser(huge_tree=True))
    except etree.XMLSyntaxError as exc:
        raise SourceSchemaError(f"ответ fedstat не является XML: {body[:120]!r}") from exc
    if etree.QName(root).localname != "GenericData":
        raise SourceSchemaError(f"ожидался SDMX GenericData, получен {etree.QName(root).localname}")

    codes = [(cl.get("id"), code.get("value"), (code.findtext("{*}Description") or "").strip())
             for cl in root.iterfind(".//{*}CodeList") for code in cl.iterfind("{*}Code")]

    region_c, product_c, period_c, unit_c = (concepts[k] for k in ("region", "product", "period", "unit"))
    rows = []
    for series in root.iterfind(".//g:Series", NS):
        key = {v.get("concept"): v.get("value") for v in series.iterfind("g:SeriesKey/g:Value", NS)}
        attrs = {v.get("concept"): v.get("value") for v in series.iterfind("g:Attributes/g:Value", NS)}
        if region_c not in key or product_c not in key or period_c not in attrs:
            raise SourceSchemaError(f"в серии нет ожидаемых измерений: ключ {sorted(key)}, атрибуты {sorted(attrs)}")
        period = attrs[period_c]
        for obs in series.iterfind("g:Obs", NS):
            value_el = obs.find("g:ObsValue", NS)
            value_raw = value_el.get("value") if value_el is not None else None
            rows.append((key[region_c], key[product_c], int(obs.findtext("g:Time", namespaces=NS)), period,
                         parse_week(period), to_decimal(value_raw), value_raw, attrs.get(unit_c)))
    return rows, codes


def resolve_selection(meta: IndicatorMeta, c: dict) -> dict[int, list[int]]:
    dims = c["dimensions"]
    for name, fid in dims.items():
        if fid not in meta.filters:
            raise SourceSchemaError(f"измерение «{name}» (id={fid}) отсутствует на странице показателя")
    by_title = {title: vid for vid, title in meta.filters[dims["product"]]["values"].items()}
    missing = [p for p in c["products"] if p not in by_title]
    if missing:
        raise SourceSchemaError(f"товары не найдены в классификаторе источника: {missing}")
    rx = re.compile(c["regions"]["include_regex"], re.I)
    regions = [vid for vid, title in meta.filters[dims["region"]]["values"].items() if rx.search(title)]
    if not regions:
        raise SourceSchemaError("ни одна территория не подходит под regions.include_regex")
    return {dims["product"]: [by_title[p] for p in c["products"]], dims["region"]: regions}


def run(conn: psycopg.Connection, journal: Journal, session: requests.Session, cfg: dict,
        full: bool = False, from_year: int | None = None) -> None:
    c = cfg["sources"]["fedstat"]
    timeout, pause = cfg["http"]["timeout_sec"], cfg["http"]["pause_sec"]
    retries, backoff = cfg["http"]["retries"], cfg["http"]["backoff_sec"]
    dims = c["dimensions"]
    page_url = f"{c['base_url']}/indicator/{c['indicator_id']}"
    download_url = f"{c['base_url']}/indicator/downloadData.do?format=sdmx"

    with journal.step("fedstat", "indicator_page", {"url": page_url}) as st:
        response, attempts = with_retries(lambda: fetch(session, "GET", page_url, timeout=timeout),
                                          retries, backoff, "fedstat page")
        st.record_response(response)
        st.attempts += attempts - 1
        st.payload_id = save_payload(conn, st.load_id, "fedstat", "indicator_page", page_url, None, response)
        meta = parse_indicator_page(response.text)
        selection = resolve_selection(meta, c)
        st.rows_parsed = sum(len(f["values"]) for f in meta.filters.values())
        st.note = (f"фильтров {len(meta.filters)}, выбрано товаров {len(selection[dims['product']])}, "
                   f"территорий {len(selection[dims['region']])}")
    if st.status == "failed":
        return

    last_year = conn.execute("select max(obs_year) from raw.fedstat_obs").fetchone()[0]
    first_year = from_year or (c["start_year"] if full or last_year is None else last_year)
    years = [y for y in sorted(meta.filters[dims["year"]]["values"]) if y >= first_year]
    product_names = meta.filters[dims["product"]]["values"]
    products = selection[dims["product"]]
    chunk = c.get("products_per_request") or len(products)
    token = meta.token

    for year in years:
        for i in range(0, len(products), chunk):
            part = products[i:i + chunk]
            params = {"year": year, "products": [product_names[p] for p in part],
                      "regions": len(selection[dims["region"]]),
                      "mode": "full" if full or from_year else ("incremental" if last_year else "initial")}
            with journal.step("fedstat", "observations", params) as st:
                def download() -> requests.Response:
                    nonlocal token
                    form_selection = {**selection, dims["product"]: part, dims["year"]: [year]}
                    for _ in range(2):
                        if not token:
                            token = parse_token(fetch(session, "GET", page_url, timeout=timeout).text)
                        form = build_download_form(meta, c["indicator_id"], token, form_selection)
                        response = fetch(session, "POST", download_url, data=form, timeout=timeout)
                        if "xml" in response.headers.get("Content-Type", ""):
                            return response
                        token = None
                    return response

                time.sleep(pause)
                response, attempts = with_retries(download, retries, backoff, f"fedstat {year}")
                st.record_response(response)
                st.attempts += attempts - 1
                st.payload_id = save_payload(conn, st.load_id, "fedstat", "observations", download_url, params, response)
                content_type = response.headers.get("Content-Type", "")
                if "xml" not in content_type:
                    raise SourceSchemaError(f"вместо SDMX получен {content_type}: форма выгрузки отклонена")
                rows, codes = parse_sdmx(response.content, c["sdmx_concepts"])
                st.apply(upsert_rows(conn, "raw.fedstat_obs", OBS_KEY, OBS_VALS, rows, st.payload_id))
                upsert_rows(conn, "raw.fedstat_code", ["dim", "code"], ["name"], codes, st.payload_id)
                if not rows:
                    st.note = "источник не вернул наблюдений"
