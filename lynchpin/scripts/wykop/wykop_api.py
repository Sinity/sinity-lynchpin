from __future__ import annotations

import math
from typing import Any, Iterable

import requests

from .wykop_http import WYKOP_API_BASE, request


class WykopApiClient:
    def __init__(self, session: requests.Session, *, refresh_token: str, api_base: str = WYKOP_API_BASE) -> None:
        self._session = session
        self._api_base = api_base.rstrip("/")
        self.refresh_token = refresh_token
        self._access_token: str | None = None
        self.refresh()

    def refresh(self) -> None:
        resp = request(
            self._session,
            "POST",
            f"{self._api_base}/refresh-token",
            json_body={"data": {"refresh_token": self.refresh_token}},
        )
        data = resp.json().get("data") if resp.headers.get("Content-Type", "").startswith("application/json") else None
        if not isinstance(data, dict):
            raise RuntimeError("Wykop refresh-token: unexpected response shape")
        token = data.get("token")
        if not isinstance(token, str) or not token:
            raise RuntimeError("Wykop refresh-token: missing access token")
        self._access_token = token
        new_refresh = data.get("refresh_token")
        if isinstance(new_refresh, str) and new_refresh:
            self.refresh_token = new_refresh
        self._session.headers.update({"Authorization": f"Bearer {token}"})

    def get(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self._api_base}/{path.lstrip('/')}"
        resp = request(self._session, "GET", url, params=params, allow_statuses={403})
        if resp.status_code == 403:
            self.refresh()
            resp = request(self._session, "GET", url, params=params, allow_statuses={403})
        resp.raise_for_status()
        return resp.json()


def api_iter_pages(
    api_client: WykopApiClient,
    endpoint: str,
    *,
    params: dict[str, Any] | None = None,
    max_pages: int | None = None,
) -> Iterable[tuple[int, dict[str, Any]]]:
    page = 1
    while True:
        merged_params = dict(params or {})
        merged_params["page"] = page
        payload = api_client.get(endpoint, params=merged_params)
        yield page, payload

        data = payload.get("data") if isinstance(payload, dict) else None
        if isinstance(data, dict):
            data_is_empty = not data
        elif isinstance(data, list):
            data_is_empty = len(data) == 0
        else:
            data_is_empty = True
        if data_is_empty:
            break

        pagination = payload.get("pagination") if isinstance(payload, dict) else None
        total = pagination.get("total") if isinstance(pagination, dict) else None
        per_page = pagination.get("per_page") if isinstance(pagination, dict) else None
        if isinstance(total, int) and isinstance(per_page, int) and per_page > 0:
            last_page = max(1, math.ceil(total / per_page))
            if page >= last_page:
                break

        if max_pages is not None and page >= max_pages:
            break
        page += 1
