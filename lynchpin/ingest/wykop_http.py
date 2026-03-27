from __future__ import annotations

import time
from typing import Any

import requests
import typer

WYKOP_BASE = "https://wykop.pl"
WYKOP_API_BASE = "https://wykop.pl/api/v3"


def get(
    session: requests.Session,
    url: str,
    *,
    retries: int = 6,
    timeout_s: int = 60,
    allow_statuses: set[int] | None = None,
) -> requests.Response:
    for attempt in range(1, retries + 1):
        resp = session.get(url, timeout=timeout_s)
        if resp.status_code in {429, 500, 502, 503, 504}:
            if attempt == retries:
                resp.raise_for_status()
            backoff = min(60.0, 2.0 ** (attempt - 1))
            retry_after = resp.headers.get("Retry-After")
            if retry_after:
                try:
                    backoff = max(backoff, float(retry_after))
                except ValueError:
                    pass
            typer.echo(f"[wykop] {resp.status_code} for {url} (attempt {attempt}/{retries}); sleeping {backoff:.1f}s", err=True)
            time.sleep(backoff)
            continue
        if allow_statuses is not None and resp.status_code in allow_statuses:
            return resp
        resp.raise_for_status()
        return resp
    raise RuntimeError(f"Unreachable: retries loop exhausted for {url}")


def request(
    session: requests.Session,
    method: str,
    url: str,
    *,
    json_body: Any | None = None,
    params: dict[str, Any] | None = None,
    retries: int = 6,
    timeout_s: int = 60,
    allow_statuses: set[int] | None = None,
) -> requests.Response:
    for attempt in range(1, retries + 1):
        resp = session.request(method, url, json=json_body, params=params, timeout=timeout_s)
        if resp.status_code in {429, 500, 502, 503, 504}:
            if attempt == retries:
                resp.raise_for_status()
            backoff = min(60.0, 2.0 ** (attempt - 1))
            retry_after = resp.headers.get("Retry-After")
            if retry_after:
                try:
                    backoff = max(backoff, float(retry_after))
                except ValueError:
                    pass
            typer.echo(
                f"[wykop] {resp.status_code} for {method} {url} (attempt {attempt}/{retries}); sleeping {backoff:.1f}s",
                err=True,
            )
            time.sleep(backoff)
            continue
        if allow_statuses is not None and resp.status_code in allow_statuses:
            return resp
        resp.raise_for_status()
        return resp
    raise RuntimeError(f"Unreachable: retries loop exhausted for {method} {url}")
