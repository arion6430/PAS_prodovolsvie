import logging
import time
from typing import Callable, TypeVar

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger(__name__)
T = TypeVar("T")

TRANSIENT_ERRORS = (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError)


def make_session(cfg: dict) -> requests.Session:
    retry = Retry(
        total=cfg["retries"],
        backoff_factor=cfg["backoff_sec"],
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "POST"}),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    session = requests.Session()
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers["User-Agent"] = cfg["user_agent"]
    return session


def fetch(session: requests.Session, method: str, url: str, *, timeout: float, **kwargs) -> requests.Response:
    response = session.request(method, url, timeout=timeout, **kwargs)
    response.raise_for_status()
    return response


def with_retries(fn: Callable[[], T], retries: int, backoff_sec: float, what: str) -> tuple[T, int]:
    # urllib3 Retry не повторяет обрыв соединения при чтении тела ответа — повторяем целиком
    for attempt in range(1, retries + 2):
        try:
            return fn(), attempt
        except TRANSIENT_ERRORS as exc:
            if attempt > retries:
                raise
            delay = backoff_sec * 2 ** (attempt - 1)
            log.warning("%s: попытка %d не удалась (%s), повтор через %.0f с", what, attempt, type(exc).__name__, delay)
            time.sleep(delay)
    raise AssertionError("unreachable")


def attempts_of(response: requests.Response) -> int:
    retries = getattr(response.raw, "retries", None)
    return len(retries.history) + 1 if retries else 1
