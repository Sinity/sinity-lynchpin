"""Download Substack publications and materialize their Lynchpin index."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from urllib.parse import urlparse

from ..core.config import get_config
from ..ingest.substack_materialize import materialize_substack


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    download = subparsers.add_parser("download", help="run the configured sbstck-dl executable")
    download.add_argument("--url", required=True)
    download.add_argument("--publication", help="raw archive directory name")
    download.add_argument("--format", choices=("html", "md", "txt"), default="html")
    download.add_argument("--rate", type=int, default=2)
    download.add_argument("--before")
    download.add_argument("--after")
    download.add_argument("--dry-run", action="store_true")
    download.add_argument("--downloader", type=Path)
    download.add_argument("--cookie-name", choices=("substack.sid", "connect.sid"))

    materialize = subparsers.add_parser("materialize", help="build the canonical Substack NDJSON")
    materialize.add_argument("--root", type=Path)
    materialize.add_argument("--output", type=Path)

    args = parser.parse_args(argv)
    if args.command == "materialize":
        print(json.dumps(materialize_substack(root=args.root, output=args.output), sort_keys=True))
        return 0
    return _download(args)


def _download(args: argparse.Namespace) -> int:
    cfg = get_config()
    executable = args.downloader or cfg.substack_downloader
    if not executable.exists():
        raise SystemExit(f"configured Substack downloader is missing: {executable}")
    publication = args.publication or _publication_name(args.url)
    output = cfg.substack_root / publication
    command = [
        str(executable),
        "download",
        "--url",
        args.url,
        "--format",
        args.format,
        "--output",
        str(output),
        "--rate",
        str(args.rate),
    ]
    for flag in ("before", "after"):
        value = getattr(args, flag)
        if value:
            command.extend([f"--{flag}", value])
    if args.dry_run:
        command.append("--dry-run")
    if args.cookie_name:
        import os

        cookie_value = os.environ.get("LYNCHPIN_SUBSTACK_COOKIE_VALUE", "")
        if not cookie_value:
            raise SystemExit("--cookie-name requires LYNCHPIN_SUBSTACK_COOKIE_VALUE")
        command.extend(["--cookie_name", args.cookie_name, "--cookie_val", cookie_value])
    subprocess.run(command, check=True)
    return 0


def _publication_name(url: str) -> str:
    host = (urlparse(url).hostname or "").casefold()
    if host in {"astralcodexten.com", "www.astralcodexten.com"}:
        return "acx"
    host = host.removeprefix("www.")
    return host.split(".", 1)[0] or "substack"


if __name__ == "__main__":
    raise SystemExit(main())
