"""CLI for refreshing the fbmessengerexport SQLite cache from browser cookies."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from ...core.config import get_config
from .fbmessenger_cookies import resolve_cookie_json
from .fbmessenger_patch import run_export

app = typer.Typer(help="Export Facebook Messenger messages via fbmessengerexport.")


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
    remote_debug_port: Optional[int] = typer.Option(
        None,
        "--remote-debug-port",
        help="Use an existing Chrome instance with remote debugging enabled (port).",
    ),
    launch_debug_chrome: bool = typer.Option(
        False,
        "--launch-debug-chrome",
        help="If DevTools port is unreachable, launch Chrome with remote debugging on that port.",
    ),
    locations: Optional[str] = typer.Option(
        None,
        "--locations",
        help="Comma-separated thread locations (inbox,other,archived). Defaults to all.",
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

    cookie_json = resolve_cookie_json(
        cookies=cookies,
        cookies_file=cookies_file,
        cookie_db=cookie_db,
        remote_debug_port=remote_debug_port,
        launch_debug_chrome_flag=launch_debug_chrome,
    )

    if dry_run:
        typer.secho("✓ Cookies prepared; dry-run requested.", fg=typer.colors.GREEN)
        return

    run_export(cookies=cookie_json, output_db=output_db, locations=locations)
    typer.secho(f"✓ Exported Messenger DB → {output_db}", fg=typer.colors.GREEN)


if __name__ == "__main__":
    app()
