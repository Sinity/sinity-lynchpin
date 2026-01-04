from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer

from ..core.config import get_config

app = typer.Typer(help="Export Facebook Messenger messages via fbmessengerexport.")

SQLITE_HEADER = b"SQLite format 3\x00"


def _cookies_from_chrome(cookie_db: Path) -> dict[str, str]:
    try:
        import browser_cookie3  # type: ignore
    except Exception as exc:  # pragma: no cover - import guard
        raise RuntimeError("browser-cookie3 is required to read Chrome cookies") from exc

    cookies: dict[str, str] = {}
    for domain in ("facebook.com", "messenger.com"):
        try:
            jar = browser_cookie3.chrome(cookie_file=str(cookie_db), domain_name=domain)
        except Exception as exc:
            raise RuntimeError(
                "Unable to decrypt Chrome cookies. Ensure a Secret Service "
                "(gnome-keyring/kwallet) is running, or pass --cookies/--cookies-file."
            ) from exc
        for cookie in jar:
            if cookie.name and cookie.value:
                cookies[cookie.name] = cookie.value
    return cookies


def _read_cookies_file(path: Path) -> str:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise typer.BadParameter(f"Unable to read cookies file: {path}") from exc
    if data.startswith(SQLITE_HEADER):
        raise typer.BadParameter(
            "Cookies file is a SQLite DB. Use --cookie-db to point at Chrome's Cookies database, "
            "or provide a JSON cookies export with --cookies-file."
        )
    text = data.decode("utf-8", errors="strict").strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise typer.BadParameter("Cookies file is not valid JSON.") from exc
    if not isinstance(parsed, dict):
        raise typer.BadParameter("Cookies file must contain a JSON object (name -> value).")
    return json.dumps(parsed, ensure_ascii=False)


@app.command()
def export(
    db: Optional[Path] = typer.Option(
        None,
        "--db",
        help="Output sqlite DB path (defaults to lynchpin config).",
    ),
    cookies: Optional[str] = typer.Option(
        None,
        "--cookies",
        help="Raw JSON string of cookies (fbmessengerexport format).",
    ),
    cookies_file: Optional[Path] = typer.Option(
        None,
        "--cookies-file",
        help="Path to a file containing the raw JSON cookie string.",
    ),
    cookie_db: Optional[Path] = typer.Option(
        None,
        "--cookie-db",
        help="Chrome Cookies sqlite path (defaults to ~/.config/google-chrome/Default/Cookies).",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Validate cookies and exit without running the export.",
    ),
) -> None:
    """Run fbmessengerexport using Chrome cookies or explicit JSON."""
    cfg = get_config()
    output_db = db or cfg.fbmessenger_db
    output_db.parent.mkdir(parents=True, exist_ok=True)

    if cookies_file and not cookies:
        cookies = _read_cookies_file(cookies_file)

    if not cookies:
        default_cookie_db = Path("~/.config/google-chrome/Default/Cookies").expanduser()
        cookie_db = cookie_db or default_cookie_db
        if not cookie_db.exists():
            raise RuntimeError(f"Chrome cookie DB not found: {cookie_db}")
        cookie_dict = _cookies_from_chrome(cookie_db)
        if not cookie_dict:
            raise RuntimeError("No Facebook cookies found in Chrome cookie DB.")
        cookies = json.dumps(cookie_dict, ensure_ascii=False)

    if dry_run:
        typer.secho("✓ Cookies prepared; dry-run requested.", fg=typer.colors.GREEN)
        return

    import fbmessengerexport.export as exporter  # type: ignore

    exporter.run(cookies=cookies, db=output_db)
    typer.secho(f"✓ Exported Messenger DB → {output_db}", fg=typer.colors.GREEN)


if __name__ == "__main__":
    app()
