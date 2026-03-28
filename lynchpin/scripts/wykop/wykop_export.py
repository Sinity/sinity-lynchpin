#!/usr/bin/env python3
"""CLI for refreshing canonical Wykop exports.

`--backend api` is the default full-fidelity path. It can discover the refresh
token from CLI input, saved scrape state, or Chrome local storage. `--backend
html` is a public fallback kept mainly for debugging and for cases where the
authenticated API path is unavailable.
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Iterable

import requests
import typer

from .wykop_api import WykopApiClient
from .wykop_api_extras import scrape_api_extras
from .wykop_api_parse import API_SPECS
from .wykop_auth import extract_refresh_token_from_chrome
from .wykop_html import COLLECTIONS, page_has_prerender, parse_max_page, resolve_max_page
from .wykop_http import get
from .wykop_io import now_iso, read_json, write_json

app = typer.Typer(add_completion=False, no_args_is_help=True)


def iter_existing_ids(path: Path, id_key: str) -> Iterable[int]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            raw = obj.get(id_key)
            if isinstance(raw, int):
                yield raw


@app.command()
def scrape(
    username: str = typer.Option("Sinity", help="Wykop username to scrape."),
    out_dir: Path = typer.Option(
        Path("/realm/data/exports/wykop/raw"),
        help="Destination folder under /realm/data/exports.",
    ),
    backend: str = typer.Option(
        "auto",
        help="Scrape backend: auto (prefer API), api (requires auth), or html (public prerender, limited to ~page 49).",
    ),
    refresh_token: str | None = typer.Option(
        None,
        help="Wykop refresh token (localStorage userKeep). If omitted, tries state then Chrome Local Storage.",
    ),
    chrome_leveldb_dir: Path | None = typer.Option(
        None,
        help="Optional Chrome/Chromium 'Local Storage/leveldb' dir to scan for userKeep.",
    ),
    collections: list[str] = typer.Option(
        [],
        "--collection",
        help=f"Limit to specific collections. Options: {', '.join(sorted(COLLECTIONS))}.",
    ),
    delay_seconds: float = typer.Option(0.25, help="Sleep between HTTP requests."),
    max_pages: int | None = typer.Option(None, help="Optional cap for pages per collection (debug)."),
    user_agent: str = typer.Option(
        "Mozilla/5.0 (compatible; sinity-lynchpin/wykop-scraper; +https://wykop.pl)",
        help="HTTP User-Agent.",
    ),
    extras: bool = typer.Option(
        True,
        help="When using the API backend, also export extra account metadata endpoints (profile/badges/observed-tags/actions).",
    ),
) -> None:
    """Scrape public Wykop profile activity into JSONL (resumable)."""

    selected = [collection for collection in collections if collection]
    unknown = [collection for collection in selected if collection not in COLLECTIONS]
    if unknown:
        raise typer.BadParameter(f"Unknown collections: {', '.join(unknown)}")
    if not selected:
        selected = sorted(COLLECTIONS)

    user_dir = out_dir / username
    user_dir.mkdir(parents=True, exist_ok=True)

    state_path = user_dir / "scrape_state.json"
    manifest_path = user_dir / "scrape_manifest.json"

    state = read_json(state_path) or {
        "username": username,
        "started_at": now_iso(),
        "auth": {},
        "collections": {},
    }

    session = requests.Session()
    session.headers.update({"User-Agent": user_agent})

    backend = backend.strip().lower()
    if backend not in {"auto", "api", "html"}:
        raise typer.BadParameter("backend must be one of: auto, api, html")

    api_client: WykopApiClient | None = None
    refresh_token_source: str | None = None
    auth_username: str | None = None
    if backend in {"auto", "api"}:
        token = refresh_token
        if token:
            refresh_token_source = "cli"
        if token is None:
            auth = state.get("auth") or {}
            token = auth.get("refresh_token") if isinstance(auth, dict) else None
            if isinstance(token, str) and token:
                refresh_token_source = "state"
            else:
                token = None
        if token is None:
            token = extract_refresh_token_from_chrome(chrome_leveldb_dir)
            if token:
                refresh_token_source = "chrome"

        if token is None and backend == "api":
            raise typer.BadParameter(
                "backend=api requires --refresh-token or a discoverable Chrome Local Storage userKeep token"
            )

        if token:
            session.headers.update({"Accept": "application/json"})
            api_client = WykopApiClient(session, refresh_token=token)
            state.setdefault("auth", {})
            state["auth"].update(
                {
                    "refresh_token": api_client.refresh_token,
                    "updated_at": now_iso(),
                    "source": refresh_token_source,
                }
            )
            write_json(state_path, state)

            try:
                short = api_client.get("profile/short")
            except requests.HTTPError:
                short = None
            if isinstance(short, dict):
                data = short.get("data")
                user = data.get("username") if isinstance(data, dict) else None
                if isinstance(user, str) and user:
                    auth_username = user
                    state["auth"].update({"username": auth_username, "username_updated_at": now_iso()})
                    write_json(state_path, state)

    manifest: dict[str, object] = {
        "username": username,
        "run_started_at": now_iso(),
        "backend": backend,
        "api_enabled": api_client is not None,
        "auth_username": auth_username,
        "collections": {},
        "extras": {},
    }

    for key in selected:
        coll = COLLECTIONS[key]
        out_path = user_dir / coll.output_name
        id_key = "comment_id" if "comment" in coll.output_name else "entry_id" if "entries" in coll.output_name else "link_id"
        if coll.output_name == "wykop_entry_comments.jsonl":
            id_key = "comment_id"

        seen_ids = set(iter_existing_ids(out_path, id_key=id_key))
        use_api = api_client is not None and backend in {"auto", "api"}

        max_page_detected: int
        max_page_final: int
        api_endpoint: str | None = None
        parse_api = None
        api_error: dict[str, object] | None = None

        if use_api:
            spec = API_SPECS.get(key)
            if spec is None:
                typer.echo(f"[wykop] {key}: no API spec; falling back to HTML", err=True)
                use_api = False
            else:
                api_endpoint, parse_api = spec
                try:
                    page1 = api_client.get(api_endpoint.format(username=username), params={"page": 1})
                except requests.HTTPError as e:
                    status = e.response.status_code if e.response is not None else None
                    api_error = {"status": status, "error": str(e)}
                    typer.echo(
                        f"[wykop] {key}: API endpoint {api_endpoint.format(username=username)} failed ({status}); falling back to HTML",
                        err=True,
                    )
                    use_api = False
                else:
                    pagination = page1.get("pagination") if isinstance(page1, dict) else None
                    total = pagination.get("total") if isinstance(pagination, dict) else None
                    per_page = pagination.get("per_page") if isinstance(pagination, dict) else None
                    if isinstance(total, int) and isinstance(per_page, int) and per_page > 0:
                        max_page_detected = max(1, math.ceil(total / per_page))
                    else:
                        max_page_detected = 1
                    max_page_final = min(max_page_detected, max_pages) if max_pages else max_page_detected
        if not use_api:
            if not coll.html_enabled:
                typer.echo(f"[wykop] {key}: HTML disabled (API-only); skipping", err=True)
                manifest["collections"][key] = {
                    "section_path": coll.section_path,
                    "output": str(out_path),
                    "backend": "skipped",
                    "skipped": True,
                    "reason": "api-only",
                    "api_endpoint": api_endpoint,
                    "api_error": api_error,
                }
                continue
            root_url = coll.page_url(username, 1)
            resp = get(session, root_url)
            from lxml import html as lxml_html

            root = lxml_html.fromstring(resp.text)
            max_page_detected = parse_max_page(root, username, coll.section_path)
            max_page_detected = resolve_max_page(session, coll, username, max_page_detected)
            max_page_final = min(max_page_detected, max_pages) if max_pages else max_page_detected

        coll_state = state["collections"].get(key, {})
        previous_backend = coll_state.get("backend")
        if use_api and previous_backend == "api":
            start_page = int(coll_state.get("last_page", 0)) + 1
        elif (not use_api) and previous_backend == "html":
            start_page = int(coll_state.get("last_page", 0)) + 1
        else:
            start_page = 1

        typer.echo(
            f"[wykop] {key}: backend={'api' if use_api else 'html'} pages 1..{max_page_final} (detected {max_page_detected}); resuming at page {start_page}; ids={len(seen_ids)}"
        )

        manifest["collections"][key] = {
            "section_path": coll.section_path,
            "output": str(out_path),
            "backend": "api" if use_api else "html",
            "api_endpoint": api_endpoint,
            "api_error": api_error,
            "max_page_detected": max_page_detected,
            "max_page_final": max_page_final,
            "start_page": start_page,
            "existing_ids": len(seen_ids),
        }

        wrote = 0
        scrape_error: dict[str, object] | None = None
        for page in range(start_page, max_page_final + 1):
            if use_api and api_client is not None and api_endpoint is not None and parse_api is not None:
                try:
                    payload = api_client.get(api_endpoint.format(username=username), params={"page": page})
                except requests.HTTPError as e:
                    status = e.response.status_code if e.response is not None else None
                    scrape_error = {"status": status, "error": str(e), "failed_at_page": page}
                    typer.echo(f"[wykop] {key}: API error at page {page} ({status}); stopping", err=True)
                    max_page_final = page - 1
                    break
                data = payload.get("data") if isinstance(payload, dict) else None
                if isinstance(data, dict):
                    data_is_empty = not data
                elif isinstance(data, list):
                    data_is_empty = len(data) == 0
                else:
                    data_is_empty = True
                if data_is_empty:
                    typer.echo(f"[wykop] {key}: got empty API page at {page}; stopping early at {page - 1}", err=True)
                    max_page_final = page - 1
                    break
                items = parse_api(payload, page, username)
            else:
                url = coll.page_url(username, page)
                resp = get(session, url, allow_statuses={404})
                if resp.status_code == 404:
                    typer.echo(f"[wykop] {key}: got 404 at page {page}; stopping early at {page - 1}", err=True)
                    max_page_final = page - 1
                    break
                if not page_has_prerender(resp.text):
                    typer.echo(
                        f"[wykop] {key}: page {page} returned JS shell (no prerender); stop. Use backend=api for full history.",
                        err=True,
                    )
                    max_page_final = page - 1
                    break
                from lxml import html as lxml_html

                root = lxml_html.fromstring(resp.text)
                items = coll.parse_page(root, page, username)

            wrote_this_page = 0
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with out_path.open("a", encoding="utf-8") as fh:
                for item in items:
                    item_id = item.get(id_key)
                    if not isinstance(item_id, int):
                        continue
                    if item_id in seen_ids:
                        continue
                    seen_ids.add(item_id)
                    fh.write(json.dumps(item, ensure_ascii=False) + "\n")
                    wrote += 1
                    wrote_this_page += 1

            state["collections"].setdefault(key, {})
            state["collections"][key].update(
                {
                    "section_path": coll.section_path,
                    "output": str(out_path),
                    "backend": "api" if use_api else "html",
                    "last_page": page,
                    "last_updated_at": now_iso(),
                    "seen_ids": len(seen_ids),
                }
            )
            write_json(state_path, state)

            if wrote_this_page or page == start_page or page == max_page_final or page % 25 == 0:
                typer.echo(f"[wykop] {key}: page {page}/{max_page_final} (+{wrote_this_page}, total +{wrote})")
            time.sleep(delay_seconds)

        manifest["collections"][key].update(
            {
                "completed_at": now_iso(),
                "items_written": wrote,
                "total_ids_now": len(seen_ids),
                "ok": scrape_error is None,
                "error": scrape_error,
            }
        )

    if extras and api_client is not None and backend in {"auto", "api"}:
        typer.echo("[wykop] extras: scraping additional API endpoints")
        manifest["extras"] = scrape_api_extras(
            api_client=api_client,
            username=username,
            auth_username=auth_username,
            user_dir=user_dir,
            delay_seconds=delay_seconds,
            max_pages=max_pages,
        )

    write_json(manifest_path, manifest)


if __name__ == "__main__":
    app()
