from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import typer

from .fbmessenger_chrome import (
    chrome_lock_pid,
    devtools_ws_from_port,
    fetch_cookies_via_devtools,
    find_chrome_binary,
    iter_cookie_db_candidates,
    launch_debug_chrome,
    maybe_start_keyring,
    maybe_unlock_keyring,
    prepare_debug_profile,
    profile_from_cookie_db,
    wait_for_devtools_port,
)

SQLITE_HEADER = b"SQLite format 3\x00"
COOKIE_DOMAINS = ["facebook.com", "messenger.com"]


def cookies_from_chrome(cookie_db: Path) -> dict[str, str]:
    try:
        import browser_cookie3  # type: ignore
        from browser_cookie3 import BrowserCookieError  # type: ignore
    except Exception as exc:  # pragma: no cover - import guard
        raise RuntimeError("browser-cookie3 is required to read Chrome cookies") from exc

    cookies: dict[str, str] = {}
    missing_domains: list[str] = []
    for domain in COOKIE_DOMAINS:
        before = len(cookies)
        try:
            jar = browser_cookie3.chrome(cookie_file=str(cookie_db), domain_name=domain)
        except BrowserCookieError:
            cookies.update(cookies_from_chrome_v10_secret(cookie_db, domain))
            if len(cookies) == before:
                missing_domains.append(domain)
        except Exception as exc:
            raise RuntimeError(
                "Unable to decrypt Chrome cookies. Ensure a Secret Service "
                "(gnome-keyring/kwallet) is running, or pass --cookies/--cookies-file."
            ) from exc
        else:
            for cookie in jar:
                if cookie.name and cookie.value:
                    cookies[cookie.name] = cookie.value
            if len(cookies) == before:
                missing_domains.append(domain)
    if missing_domains:
        cookies.update(cookies_from_chrome_remote(cookie_db, missing_domains))
    return cookies


def cookies_from_chrome_v10_secret(cookie_db: Path, domain: str) -> dict[str, str]:
    import sqlite3

    try:
        from browser_cookie3 import (  # type: ignore
            AES,
            PBKDF2,
            USE_DBUS_LINUX,
            BrowserCookieError,
            CHROMIUM_DEFAULT_PASSWORD,
            _LinuxPasswordManager,
            _text_factory,
            unpad,
        )
    except Exception as exc:  # pragma: no cover - import guard
        raise RuntimeError("browser-cookie3 is required to read Chrome cookies") from exc

    password = _LinuxPasswordManager(USE_DBUS_LINUX).get_password("chrome")
    salt = b"saltysalt"
    iv = b" " * 16
    v10_key = PBKDF2(CHROMIUM_DEFAULT_PASSWORD, salt, 16, 1)
    v11_key = PBKDF2(password, salt, 16, 1)
    v11_empty_key = PBKDF2(b"", salt, 16, 1)

    def decrypt(value: str, encrypted_value: bytes) -> str:
        if value or encrypted_value[:3] not in (b"v10", b"v11"):
            return value
        prefix = encrypted_value[:3]
        payload = encrypted_value[3:]
        keys = (v11_key, v11_empty_key, v10_key) if prefix == b"v10" else (v11_key, v11_empty_key)
        for key in keys:
            cipher = AES.new(key, AES.MODE_CBC, iv)
            try:
                decrypted = unpad(cipher.decrypt(payload), AES.block_size)
                return decrypted.decode("utf-8")
            except Exception:
                continue
        raise BrowserCookieError("Unable to decrypt Chrome cookies with available keys.")

    cookies: dict[str, str] = {}
    with sqlite3.connect(str(cookie_db)) as con:
        con.text_factory = _text_factory
        cur = con.cursor()
        try:
            cur.execute(
                "SELECT host_key, path, secure, expires_utc, name, value, encrypted_value, is_httponly "
                "FROM cookies WHERE host_key like ?;",
                (f"%{domain}%",),
            )
        except sqlite3.OperationalError:
            cur.execute(
                "SELECT host_key, path, is_secure, expires_utc, name, value, encrypted_value, is_httponly "
                "FROM cookies WHERE host_key like ?;",
                (f"%{domain}%",),
            )

        for item in cur.fetchall():
            _, _, _, _, name, value, enc_value, _ = item
            if not name:
                continue
            try:
                value = decrypt(value, enc_value)
            except BrowserCookieError:
                continue
            if value:
                cookies[name] = value
    return cookies


def cookies_from_chrome_remote(cookie_db: Path, domains: list[str]) -> dict[str, str]:
    chrome = find_chrome_binary()
    if not domains:
        return {}
    if not chrome:
        typer.secho(
            "Chrome binary not found in PATH; remote cookie fetch unavailable.",
            fg=typer.colors.YELLOW,
        )
        return {}

    temp_root = Path(tempfile.mkdtemp(prefix="lynchpin-chrome-profile-"))
    profile_dir = temp_root / "Default"
    profile_dir.mkdir(parents=True, exist_ok=True)

    proc = None
    try:
        shutil.copy2(cookie_db, profile_dir / "Cookies")
        cookie_journal = cookie_db.parent / f"{cookie_db.name}-journal"
        if cookie_journal.exists():
            shutil.copy2(cookie_journal, profile_dir / cookie_journal.name)
        local_state = cookie_db.parents[1] / "Local State"
        if local_state.exists():
            shutil.copy2(local_state, temp_root / "Local State")

        proc = subprocess.Popen(
            [
                chrome,
                "--headless=new",
                f"--user-data-dir={temp_root}",
                "--profile-directory=Default",
                "--remote-debugging-port=0",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-gpu",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        resolved_port = wait_for_devtools_port(temp_root)
        ws_url = devtools_ws_from_port(resolved_port) if resolved_port else None
        if not ws_url:
            typer.secho(
                "Chrome remote debugging did not expose a websocket; skipping remote cookie fetch.",
                fg=typer.colors.YELLOW,
            )
            return {}
        return fetch_cookies_via_devtools(ws_url, domains)
    finally:
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                pass
        shutil.rmtree(temp_root, ignore_errors=True)


def read_cookies_file(path: Path) -> str:
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


def resolve_cookie_json(
    *,
    cookies: Optional[str],
    cookies_file: Optional[Path],
    cookie_db: Optional[Path],
    remote_debug_port: Optional[int],
    launch_debug_chrome_flag: bool,
) -> str:
    if cookies_file and not cookies:
        cookies = read_cookies_file(cookies_file)

    debug_proc = None
    temp_profile_root: Optional[Path] = None
    if not cookies and remote_debug_port is not None:
        ws_url = devtools_ws_from_port(remote_debug_port)
        if not ws_url and launch_debug_chrome_flag:
            default_cookie_db = Path("~/.config/google-chrome/Default/Cookies").expanduser()
            selected_cookie_db = cookie_db or default_cookie_db
            user_data_dir, profile_dir = profile_from_cookie_db(selected_cookie_db)
            if chrome_lock_pid(user_data_dir) is not None:
                typer.secho(
                    "Chrome profile appears locked; starting from a temporary profile copy.",
                    fg=typer.colors.YELLOW,
                )
            temp_profile_root, profile_dir = prepare_debug_profile(selected_cookie_db)
            debug_proc, ws_url = launch_debug_chrome(
                temp_profile_root,
                profile_dir,
                remote_debug_port,
            )
        if not ws_url:
            if debug_proc is not None and debug_proc.poll() is not None:
                raise RuntimeError(
                    f"Chrome failed to start with remote debugging on port {remote_debug_port}. "
                    "If Chrome is already running, close it and retry with --launch-debug-chrome."
                )
            raise RuntimeError(
                f"Unable to reach Chrome DevTools at 127.0.0.1:{remote_debug_port}. "
                "Start Chrome with --remote-debugging-port or pass --launch-debug-chrome."
            )
        try:
            cookie_dict = fetch_cookies_via_devtools(ws_url, COOKIE_DOMAINS)
        finally:
            if debug_proc is not None:
                try:
                    debug_proc.terminate()
                    debug_proc.wait(timeout=3)
                except Exception:
                    pass
            if temp_profile_root is not None:
                shutil.rmtree(temp_profile_root, ignore_errors=True)
        if not cookie_dict:
            raise RuntimeError("No Facebook cookies found via Chrome DevTools.")
        cookies = json.dumps(cookie_dict, ensure_ascii=False)

    if cookies:
        return cookies

    maybe_start_keyring()
    default_cookie_db = Path("~/.config/google-chrome/Default/Cookies").expanduser()
    selected_cookie_db = cookie_db or default_cookie_db
    candidates = [selected_cookie_db] if cookie_db is not None else iter_cookie_db_candidates(default_cookie_db)
    cookie_dict: dict[str, str] = {}
    found_any = False
    for candidate in candidates:
        if not candidate.exists():
            continue
        found_any = True
        cookie_dict = cookies_from_chrome(candidate)
        if cookie_dict:
            selected_cookie_db = candidate
            break
    if not found_any:
        raise RuntimeError(
            "Chrome cookie DB not found. Pass --cookie-db to point at a profile Cookies DB."
        )
    if not cookie_dict and maybe_unlock_keyring():
        for candidate in candidates:
            if not candidate.exists():
                continue
            cookie_dict = cookies_from_chrome(candidate)
            if cookie_dict:
                selected_cookie_db = candidate
                break
    if cookie_dict and selected_cookie_db != default_cookie_db and cookie_db is None:
        typer.secho(
            f"Using Chrome cookie DB: {selected_cookie_db}",
            fg=typer.colors.YELLOW,
        )
    if not cookie_dict:
        raise RuntimeError(
            "No Facebook cookies found in Chrome cookies. Ensure Chrome is logged in, "
            "or pass --cookies/--cookies-file with an explicit cookie export."
        )
    return json.dumps(cookie_dict, ensure_ascii=False)
