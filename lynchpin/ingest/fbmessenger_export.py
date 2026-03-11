from __future__ import annotations

import asyncio
import getpass
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import urllib.request
import time
from pathlib import Path
from typing import Optional

import typer

from ..core.config import get_config

app = typer.Typer(help="Export Facebook Messenger messages via fbmessengerexport.")

SQLITE_HEADER = b"SQLite format 3\x00"


def _jsonify(obj: object) -> object:
    try:
        json.dumps(obj)
        return obj
    except TypeError:
        return str(obj)


class _LiteExportDb:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS threads ("
            "uid TEXT PRIMARY KEY,"
            "name TEXT,"
            "message_count INTEGER,"
            "last_message_timestamp INTEGER,"
            "data JSON"
            ")"
        )
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS messages ("
            "uid TEXT PRIMARY KEY,"
            "thread_id TEXT,"
            "author TEXT,"
            "text TEXT,"
            "timestamp INTEGER,"
            "data JSON"
            ")"
        )
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_thread ON messages(thread_id)")
        self._ensure_compat_columns()
        self._pending = 0

    def _ensure_compat_columns(self) -> None:
        self._ensure_column("threads", "name", "TEXT")
        self._ensure_column("messages", "author", "TEXT")
        self._ensure_column("messages", "text", "TEXT")
        self._backfill_threads()
        self._backfill_messages()
        self._conn.commit()

    def _ensure_column(self, table: str, column: str, ddl: str) -> None:
        existing = {
            row[1]
            for row in self._conn.execute(f"PRAGMA table_info({table})")
        }
        if column not in existing:
            self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    def _backfill_threads(self) -> None:
        rows = self._conn.execute(
            "SELECT uid, data FROM threads WHERE name IS NULL"
        ).fetchall()
        for uid, payload in rows:
            try:
                data = json.loads(payload) if payload else {}
            except json.JSONDecodeError:
                data = {}
            self._conn.execute(
                "UPDATE threads SET name=? WHERE uid=?",
                (data.get("name"), uid),
            )

    def _backfill_messages(self) -> None:
        rows = self._conn.execute(
            "SELECT uid, data FROM messages WHERE author IS NULL OR text IS NULL"
        ).fetchall()
        for uid, payload in rows:
            try:
                data = json.loads(payload) if payload else {}
            except json.JSONDecodeError:
                data = {}
            self._conn.execute(
                "UPDATE messages SET author=COALESCE(author, ?), text=COALESCE(text, ?) WHERE uid=?",
                (data.get("author"), data.get("text"), uid),
            )

    def _maybe_commit(self) -> None:
        self._pending += 1
        if self._pending >= 1000:
            self._conn.commit()
            self._pending = 0

    def insert_thread(self, thread) -> None:
        dd = vars(thread).copy()
        for key in ("type", "nicknames", "admins", "approval_requests", "participants", "plan"):
            dd.pop(key, None)
        if "color" in dd and dd["color"] is not None:
            dd["color"] = getattr(dd["color"], "value", dd["color"])
        if "last_message_timestamp" in dd:
            dd["last_message_timestamp"] = int(dd["last_message_timestamp"])
        dd = {k: _jsonify(v) for k, v in dd.items()}
        self._conn.execute(
            "INSERT OR REPLACE INTO threads (uid, name, message_count, last_message_timestamp, data) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                str(thread.uid),
                dd.get("name"),
                int(thread.message_count),
                int(thread.last_message_timestamp),
                json.dumps(dd, ensure_ascii=False),
            ),
        )
        self._maybe_commit()

    def insert_message(self, thread, message) -> None:
        dd = vars(message).copy()
        for key in (
            "mentions",
            "read_by",
            "attachments",
            "quick_replies",
            "reactions",
            "sticker",
            "emoji_size",
            "replied_to",
        ):
            dd.pop(key, None)
        if "timestamp" in dd:
            dd["timestamp"] = int(dd["timestamp"])
        dd["thread_id"] = thread.uid
        dd = {k: _jsonify(v) for k, v in dd.items()}
        self._conn.execute(
            "INSERT OR REPLACE INTO messages (uid, thread_id, author, text, timestamp, data) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                str(message.uid),
                str(thread.uid),
                dd.get("author"),
                dd.get("text"),
                int(message.timestamp),
                json.dumps(dd, ensure_ascii=False),
            ),
        )
        self._maybe_commit()

    def get_oldest_and_newest(self, thread) -> Optional[tuple[int, int]]:
        cur = self._conn.execute(
            "SELECT MIN(timestamp), MAX(timestamp) FROM messages WHERE thread_id=?",
            (str(thread.uid),),
        )
        row = cur.fetchone()
        if not row or row[0] is None:
            return None
        return int(row[0]), int(row[1])

    def check_fetched_all(self, thread):
        cur = self._conn.execute(
            "SELECT COUNT(*) FROM messages WHERE thread_id=?",
            (str(thread.uid),),
        )
        row = cur.fetchone()
        if row and row[0] != thread.message_count:
            yield RuntimeError(
                f"Expected {thread.message_count} messages in thread {thread.name}, got {row[0]}"
            )

    @property
    def db(self):
        return self._conn

    def __del__(self) -> None:
        try:
            self._conn.commit()
            self._conn.close()
        except Exception:
            pass


def ensure_export_db_compatibility(db_path: Path) -> None:
    if not db_path.exists():
        return
    db = _LiteExportDb(db_path)
    db.db.commit()
    db.db.close()


def _cookies_from_chrome(cookie_db: Path) -> dict[str, str]:
    try:
        import browser_cookie3  # type: ignore
        from browser_cookie3 import BrowserCookieError  # type: ignore
    except Exception as exc:  # pragma: no cover - import guard
        raise RuntimeError("browser-cookie3 is required to read Chrome cookies") from exc

    cookies: dict[str, str] = {}
    missing_domains: list[str] = []
    for domain in ("facebook.com", "messenger.com"):
        before = len(cookies)
        try:
            jar = browser_cookie3.chrome(cookie_file=str(cookie_db), domain_name=domain)
        except BrowserCookieError:
            cookies.update(_cookies_from_chrome_v10_secret(cookie_db, domain))
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
        cookies.update(_cookies_from_chrome_remote(cookie_db, missing_domains))
    return cookies


def _cookies_from_chrome_v10_secret(cookie_db: Path, domain: str) -> dict[str, str]:
    import sqlite3

    try:
        from browser_cookie3 import CHROMIUM_DEFAULT_PASSWORD  # type: ignore
        from browser_cookie3 import USE_DBUS_LINUX  # type: ignore
        from browser_cookie3 import BrowserCookieError  # type: ignore
        from browser_cookie3 import PBKDF2, AES, unpad  # type: ignore
        from browser_cookie3 import _LinuxPasswordManager  # type: ignore
        from browser_cookie3 import _text_factory  # type: ignore
    except Exception as exc:  # pragma: no cover - import guard
        raise RuntimeError("browser-cookie3 is required to read Chrome cookies") from exc

    password = _LinuxPasswordManager(USE_DBUS_LINUX).get_password("chrome")
    salt = b"saltysalt"
    iv = b" " * 16
    length = 16
    iterations = 1
    v10_key = PBKDF2(CHROMIUM_DEFAULT_PASSWORD, salt, length, iterations)
    v11_key = PBKDF2(password, salt, length, iterations)
    v11_empty_key = PBKDF2(b"", salt, length, iterations)

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


def _find_chrome_binary() -> Optional[str]:
    for candidate in ("google-chrome-stable", "google-chrome", "chromium", "chromium-browser"):
        path = shutil.which(candidate)
        if path:
            return path
    return None


def _chrome_user_agent() -> str:
    chrome = _find_chrome_binary()
    version = None
    if chrome:
        try:
            output = subprocess.check_output([chrome, "--version"], text=True).strip()
            match = re.search(r"(\\d+\\.\\d+\\.\\d+\\.\\d+)", output)
            if match:
                version = match.group(1)
        except Exception:
            version = None
    if not version:
        version = "120.0.0.0"
    return (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        f"(KHTML, like Gecko) Chrome/{version} Safari/537.36"
    )


def _requests_user_agent() -> str:
    try:
        import requests  # type: ignore
    except Exception:
        return "python-requests/2.32.3"
    return f"python-requests/{requests.__version__}"


def _fetch_cookies_via_devtools(ws_url: str, domains: list[str]) -> dict[str, str]:
    if not domains:
        return {}
    async def fetch() -> dict[str, str]:
        import json as _json
        try:
            import websockets  # type: ignore
        except Exception as exc:  # pragma: no cover - import guard
            raise RuntimeError("websockets is required for Chrome remote cookie fetch") from exc

        def extract(msg: dict[str, object]) -> dict[str, str]:
            if msg.get("error"):
                return {}
            cookies: dict[str, str] = {}
            for cookie in msg.get("result", {}).get("cookies", []):
                cookie_domain = cookie.get("domain", "")
                if any(domain in cookie_domain for domain in domains) and cookie.get("value"):
                    cookies[cookie["name"]] = cookie["value"]
            return cookies

        cookies: dict[str, str] = {}
        async with websockets.connect(ws_url, open_timeout=5) as ws:
            async def recv(timeout: float = 5.0) -> dict[str, object]:
                data = await asyncio.wait_for(ws.recv(), timeout=timeout)
                return _json.loads(data)

            async def send(msg_id: int, method: str, params: Optional[dict[str, object]] = None) -> dict[str, object]:
                payload: dict[str, object] = {"id": msg_id, "method": method}
                if params:
                    payload["params"] = params
                await ws.send(_json.dumps(payload))
                while True:
                    msg = await recv()
                    if msg.get("id") == msg_id:
                        return msg

            await send(1, "Network.enable")
            urls = [f"https://{domain.lstrip('.')}" for domain in domains]
            msg = await send(2, "Network.getCookies", {"urls": urls})
            cookies = extract(msg)
            if not cookies:
                msg = await send(3, "Network.getAllCookies")
                cookies = extract(msg)
            if not cookies:
                await send(4, "Page.enable")
                await send(5, "Page.navigate", {"url": "https://messenger.com/"})
                for _ in range(100):
                    try:
                        msg = await recv()
                    except asyncio.TimeoutError:
                        break
                    if msg.get("method") == "Page.loadEventFired":
                        break
                msg = await send(6, "Network.getCookies", {"urls": urls})
                cookies = extract(msg)
                if not cookies:
                    msg = await send(7, "Network.getAllCookies")
                    cookies = extract(msg)
        return cookies

    try:
        result = asyncio.run(fetch())
        if not result:
            typer.secho(
                "Chrome remote cookie fetch returned 0 cookies.",
                fg=typer.colors.YELLOW,
            )
        return result
    except Exception as exc:
        typer.secho(
            f"Chrome remote cookie fetch failed: {exc}",
            fg=typer.colors.YELLOW,
        )
        return {}


def _cookies_from_chrome_remote(cookie_db: Path, domains: list[str]) -> dict[str, str]:
    chrome = _find_chrome_binary()
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

    try:
        shutil.copy2(cookie_db, profile_dir / "Cookies")
        cookie_journal = cookie_db.parent / f"{cookie_db.name}-journal"
        if cookie_journal.exists():
            shutil.copy2(cookie_journal, profile_dir / cookie_journal.name)
        local_state = cookie_db.parents[1] / "Local State"
        if local_state.exists():
            shutil.copy2(local_state, temp_root / "Local State")

        cmd = [
            chrome,
            "--headless=new",
            f"--user-data-dir={temp_root}",
            "--profile-directory=Default",
            "--remote-debugging-port=0",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-gpu",
        ]
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)
        resolved_port = _wait_for_devtools_port(temp_root)
        ws_url = _devtools_ws_from_port(resolved_port) if resolved_port else None
        if not ws_url:
            proc.terminate()
            typer.secho(
                "Chrome remote debugging did not expose a websocket; skipping remote cookie fetch.",
                fg=typer.colors.YELLOW,
            )
            return {}

        return _fetch_cookies_via_devtools(ws_url, domains)
    finally:
        if "proc" in locals():
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                pass
        shutil.rmtree(temp_root, ignore_errors=True)


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


def _devtools_ws_from_port(port: int) -> Optional[str]:
    base = f"http://127.0.0.1:{port}"
    try:
        with urllib.request.urlopen(f"{base}/json/list", timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if isinstance(data, list):
            preferred = None
            for item in data:
                if not isinstance(item, dict):
                    continue
                url = str(item.get("url", ""))
                if "messenger.com" in url or "facebook.com" in url:
                    preferred = item
                    break
            if preferred is None:
                for item in data:
                    if isinstance(item, dict) and item.get("type") == "page":
                        preferred = item
                        break
            if preferred:
                ws_url = preferred.get("webSocketDebuggerUrl")
                if ws_url:
                    return ws_url
    except Exception:
        pass
    try:
        with urllib.request.urlopen(f"{base}/json/version", timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("webSocketDebuggerUrl")
    except Exception:
        return None


def _profile_from_cookie_db(cookie_db: Path) -> tuple[Path, str]:
    profile_dir = cookie_db.parent.name
    user_data_dir = cookie_db.parent.parent
    return user_data_dir, profile_dir


def _read_devtools_active_port(user_data_dir: Path) -> Optional[int]:
    active_file = user_data_dir / "DevToolsActivePort"
    if not active_file.exists():
        return None
    try:
        lines = active_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    if not lines:
        return None
    try:
        return int(lines[0].strip())
    except (TypeError, ValueError):
        return None


def _wait_for_devtools_port(user_data_dir: Path, timeout: float = 10.0) -> Optional[int]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        port = _read_devtools_active_port(user_data_dir)
        if port:
            return port
        time.sleep(0.25)
    return None


def _chrome_lock_pid(user_data_dir: Path) -> Optional[int]:
    lock_path = user_data_dir / "SingletonLock"
    if not lock_path.exists():
        return None
    pid: Optional[int] = None
    try:
        target = lock_path.resolve()
        name = target.name
    except Exception:
        name = lock_path.name
    for part in name.split("-")[::-1]:
        if part.isdigit():
            pid = int(part)
            break
    if pid is None:
        try:
            text = lock_path.read_text(encoding="utf-8").strip()
            if text.isdigit():
                pid = int(text)
        except OSError:
            pid = None
    if pid is not None and Path(f"/proc/{pid}").exists():
        return pid
    return None


def _prepare_debug_profile(cookie_db: Path) -> tuple[Path, str]:
    temp_root = Path(tempfile.mkdtemp(prefix="lynchpin-chrome-debug-"))
    profile_name = cookie_db.parent.name
    profile_dir = temp_root / profile_name
    profile_dir.mkdir(parents=True, exist_ok=True)

    copy_targets = [
        "Cookies",
        "Cookies-journal",
        "Preferences",
        "Secure Preferences",
        "Login Data",
        "Login Data-journal",
        "Web Data",
        "Web Data-journal",
    ]
    for name in copy_targets:
        src = cookie_db.parent / name
        if src.exists():
            shutil.copy2(src, profile_dir / src.name)

    local_state = cookie_db.parents[1] / "Local State"
    if local_state.exists():
        shutil.copy2(local_state, temp_root / "Local State")

    return temp_root, profile_name


def _launch_debug_chrome(
    user_data_dir: Path,
    profile_dir: str,
    port: Optional[int],
) -> tuple[subprocess.Popen[str], Optional[str]]:
    chrome = _find_chrome_binary()
    if not chrome:
        raise RuntimeError("Chrome binary not found in PATH.")
    if port is None:
        port = 0
    active_file = user_data_dir / "DevToolsActivePort"
    if port == 0 and active_file.exists():
        try:
            active_file.unlink()
        except OSError:
            pass
    cmd = [
        chrome,
        f"--remote-debugging-port={port}",
        "--remote-allow-origins=*",
        "--remote-debugging-address=127.0.0.1",
        f"--user-data-dir={user_data_dir}",
        f"--profile-directory={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)
    ws_url: Optional[str] = None
    if port == 0:
        resolved_port = _wait_for_devtools_port(user_data_dir)
        if resolved_port:
            ws_url = _devtools_ws_from_port(resolved_port)
    else:
        for _ in range(20):
            time.sleep(0.5)
            ws_url = _devtools_ws_from_port(port)
            if ws_url:
                break
    return proc, ws_url


def _maybe_start_keyring() -> None:
    if os.environ.get("GNOME_KEYRING_CONTROL"):
        return
    if not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        return
    gnome_keyring = shutil.which("gnome-keyring-daemon")
    if gnome_keyring is None:
        return
    result = subprocess.run(
        [gnome_keyring, "--start", "--components=secrets"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not result.stdout:
        return
    for fragment in result.stdout.replace("\n", ";").split(";"):
        fragment = fragment.strip()
        if not fragment or fragment.startswith("export "):
            continue
        if "=" not in fragment:
            continue
        key, value = fragment.split("=", 1)
        if key and value:
            os.environ[key] = value


def _maybe_unlock_keyring() -> bool:
    if not sys.stdin.isatty():
        return False
    if shutil.which("gnome-keyring-daemon") is None:
        return False
    typer.secho(
        "Keyring unlock required to decrypt Chrome cookies.",
        fg=typer.colors.YELLOW,
    )
    try:
        password = getpass.getpass("Keyring passphrase: ")
    except (EOFError, KeyboardInterrupt):
        return False
    result = subprocess.run(
        ["gnome-keyring-daemon", "--unlock"],
        input=f"{password}\n",
        text=True,
        check=False,
    )
    return result.returncode == 0


def _iter_cookie_db_candidates(default_cookie_db: Path) -> list[Path]:
    candidates = [default_cookie_db]
    for root in (Path("~/.config/google-chrome").expanduser(), Path("~/.config/chromium").expanduser()):
        if not root.exists():
            continue
        for path in sorted(root.glob("*/Cookies")):
            if path not in candidates:
                candidates.append(path)
    return candidates


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

    if cookies_file and not cookies:
        cookies = _read_cookies_file(cookies_file)

    debug_proc: Optional[subprocess.Popen[str]] = None
    temp_profile_root: Optional[Path] = None
    if not cookies and remote_debug_port is not None:
        ws_url = _devtools_ws_from_port(remote_debug_port)
        if not ws_url and launch_debug_chrome:
            default_cookie_db = Path("~/.config/google-chrome/Default/Cookies").expanduser()
            cookie_db = cookie_db or default_cookie_db
            user_data_dir, profile_dir = _profile_from_cookie_db(cookie_db)
            lock_pid = _chrome_lock_pid(user_data_dir)
            if lock_pid is not None:
                typer.secho(
                    "Chrome profile appears locked; starting from a temporary profile copy.",
                    fg=typer.colors.YELLOW,
                )
            temp_profile_root, profile_dir = _prepare_debug_profile(cookie_db)
            debug_proc, ws_url = _launch_debug_chrome(
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
            cookie_dict = _fetch_cookies_via_devtools(ws_url, ["facebook.com", "messenger.com"])
        finally:
            if debug_proc is not None:
                debug_proc.terminate()
            if temp_profile_root is not None:
                shutil.rmtree(temp_profile_root, ignore_errors=True)
        if not cookie_dict:
            raise RuntimeError("No Facebook cookies found via Chrome DevTools.")
        cookies = json.dumps(cookie_dict, ensure_ascii=False)

    if not cookies:
        _maybe_start_keyring()
        default_cookie_db = Path("~/.config/google-chrome/Default/Cookies").expanduser()
        selected_cookie_db = cookie_db or default_cookie_db
        candidates = (
            [selected_cookie_db]
            if cookie_db is not None
            else _iter_cookie_db_candidates(default_cookie_db)
        )
        cookie_dict: dict[str, str] = {}
        found_any = False
        for candidate in candidates:
            if not candidate.exists():
                continue
            found_any = True
            cookie_dict = _cookies_from_chrome(candidate)
            if cookie_dict:
                selected_cookie_db = candidate
                break
        if not found_any:
            raise RuntimeError(
                "Chrome cookie DB not found. Pass --cookie-db to point at a profile Cookies DB."
            )
        if not cookie_dict and _maybe_unlock_keyring():
            for candidate in candidates:
                if not candidate.exists():
                    continue
                cookie_dict = _cookies_from_chrome(candidate)
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
        cookies = json.dumps(cookie_dict, ensure_ascii=False)

    if dry_run:
        typer.secho("✓ Cookies prepared; dry-run requested.", fg=typer.colors.GREEN)
        return

    import fbmessengerexport.export as exporter  # type: ignore
    try:
        import fbchat  # type: ignore
    except Exception:
        fbchat = None
    if fbchat is not None:
        fbchat._util.USER_AGENTS = [_requests_user_agent()]  # type: ignore[attr-defined]
        fbchat._state.FB_DTSG_REGEX = re.compile(  # type: ignore[attr-defined]
            r'(?:\"name\"\\s*:\\s*\"fb_dtsg\"\\s*,\\s*\"value\"\\s*:\\s*\"|name=\"fb_dtsg\"[^>]*value=\")([^\"<>]+)'
        )
        def _from_session(cls, session):  # type: ignore[no-redef]
            user_id = fbchat._state.get_user_id(session)
            mobile = session.get("https://m.facebook.com/")
            soup_mobile = fbchat._state.find_input_fields(mobile.text)
            fb_dtsg_element = soup_mobile.find("input", {"name": "fb_dtsg"})
            if fb_dtsg_element:
                fb_dtsg = fb_dtsg_element["value"]
            else:
                match = fbchat._state.FB_DTSG_REGEX.search(mobile.text)
                if not match:
                    raise RuntimeError("Unable to extract fb_dtsg from m.facebook.com")
                fb_dtsg = match.group(1)

            desktop = session.get("https://www.facebook.com/")
            revision_match = re.search(r'\"client_revision\":(\\d+)', desktop.text)
            revision = int(revision_match.group(1)) if revision_match else 0
            soup_desktop = fbchat._state.find_input_fields(desktop.text)
            logout_h_element = soup_desktop.find("input", {"name": "h"})
            logout_h = logout_h_element["value"] if logout_h_element else None

            return cls(
                user_id=user_id,
                fb_dtsg=fb_dtsg,
                revision=revision,
                session=session,
                logout_h=logout_h,
            )

        fbchat._state.State.from_session = classmethod(_from_session)  # type: ignore[attr-defined]
        def _is_logged_in(self) -> bool:  # type: ignore[no-redef]
            return bool(self._session.cookies.get("c_user"))

        fbchat._state.State.is_logged_in = _is_logged_in  # type: ignore[attr-defined]
        try:
            from fbchat import _group  # type: ignore
        except Exception:
            _group = None
        if _group is not None:
            def _group_from_graphql(cls, data):  # type: ignore[no-redef]
                if data.get("image") is None:
                    data["image"] = {}
                c_info = cls._parse_customization_info(data)
                last_message_timestamp = None
                last_message = data.get("last_message") or {}
                last_nodes = last_message.get("nodes") or []
                if last_nodes:
                    last_message_timestamp = last_nodes[0].get("timestamp_precise")
                plan = None
                event_reminders = data.get("event_reminders") or {}
                event_nodes = event_reminders.get("nodes") or []
                if event_nodes:
                    plan = _group._plan.Plan._from_graphql(event_nodes[0])
                joinable = data.get("joinable_mode") or {}
                thread_admins = data.get("thread_admins") or []
                approval_queue = data.get("group_approval_queue") or {}
                approval_nodes = approval_queue.get("nodes") or []

                return cls(
                    data["thread_key"]["thread_fbid"],
                    participants=set(
                        node["messaging_actor"]["id"]
                        for node in data["all_participants"]["nodes"]
                    ),
                    nicknames=c_info.get("nicknames"),
                    color=c_info.get("color"),
                    emoji=c_info.get("emoji"),
                    admins=set(node.get("id") for node in thread_admins),
                    approval_mode=bool(data.get("approval_mode"))
                    if data.get("approval_mode") is not None
                    else None,
                    approval_requests=set(
                        node["requester"]["id"] for node in approval_nodes
                    )
                    if approval_nodes
                    else None,
                    join_link=joinable.get("link"),
                    photo=data["image"].get("uri"),
                    name=data.get("name"),
                    message_count=data.get("messages_count"),
                    last_message_timestamp=last_message_timestamp,
                    plan=plan,
                )

            _group.Group._from_graphql = classmethod(_group_from_graphql)  # type: ignore[attr-defined]
        try:
            from fbchat import _client  # type: ignore
        except Exception:
            _client = None
        if _client is not None:
            def _fetch_thread_list(self, thread_location, before=None, after=None, limit=20, offset=None):  # type: ignore[no-redef]
                if offset is not None:
                    _client.log.warning(
                        "Using `offset` in `fetchThreadList` is no longer supported, "
                        "since Facebook migrated to GraphQL. Use `before` instead."
                    )
                if limit > 20 or limit < 1:
                    raise _client.FBchatUserError("`limit` should be between 1 and 20")
                if thread_location in _client.ThreadLocation:
                    loc_str = thread_location.value
                else:
                    raise _client.FBchatUserError('"thread_location" must be a value of ThreadLocation')
                params = {
                    "limit": limit,
                    "tags": [loc_str],
                    "before": before,
                    "includeDeliveryReceipts": True,
                    "includeSeqID": False,
                }
                (j,) = self.graphql_requests(_client._graphql.from_doc_id("1349387578499440", params))
                rtn = []
                for node in j["viewer"]["message_threads"]["nodes"]:
                    _type = node.get("thread_type")
                    if _type == "GROUP":
                        rtn.append(_client.Group._from_graphql(node))
                    elif _type in ("ONE_TO_ONE", "AI_BOT"):
                        rtn.append(_client.User._from_thread_fetch(node))
                    else:
                        _client.log.warning("Unknown thread type %s; skipping", _type)
                return rtn

            _client.Client.fetchThreadList = _fetch_thread_list  # type: ignore[attr-defined]
        try:
            from fbchat import _user  # type: ignore
        except Exception:
            _user = None
        if _user is not None:
            def _user_from_thread_fetch(cls, data):  # type: ignore[no-redef]
                c_info = cls._parse_customization_info(data)
                participants = [
                    node["messaging_actor"] for node in data["all_participants"]["nodes"]
                ]
                target_id = data["thread_key"].get("other_user_id")
                user = next((p for p in participants if p.get("id") == target_id), None)
                if user is None and participants:
                    user = participants[0]
                if user is None:
                    user = {}

                last_message_timestamp = None
                last_message = data.get("last_message") or {}
                last_nodes = last_message.get("nodes") or []
                if last_nodes:
                    last_message_timestamp = last_nodes[0].get("timestamp_precise")

                first_name = user.get("short_name")
                if first_name is None:
                    last_name = None
                else:
                    name = user.get("name") or ""
                    last_name = name.split(first_name, 1).pop().strip() if name else None

                plan = None
                event_reminders = data.get("event_reminders") or {}
                event_nodes = event_reminders.get("nodes") or []
                if event_nodes:
                    plan = _user._plan.Plan._from_graphql(event_nodes[0])

                photo = None
                big_image = user.get("big_image_src") or {}
                if isinstance(big_image, dict):
                    photo = big_image.get("uri")

                return cls(
                    user.get("id"),
                    url=user.get("url"),
                    name=user.get("name"),
                    first_name=first_name,
                    last_name=last_name,
                    is_friend=user.get("is_viewer_friend"),
                    gender=_user.GENDERS.get(user.get("gender")),
                    affinity=user.get("affinity"),
                    nickname=c_info.get("nickname"),
                    color=c_info.get("color"),
                    emoji=c_info.get("emoji"),
                    own_nickname=c_info.get("own_nickname"),
                    photo=photo,
                    message_count=data.get("messages_count"),
                    last_message_timestamp=last_message_timestamp,
                    plan=plan,
                )

            _user.User._from_thread_fetch = classmethod(_user_from_thread_fetch)  # type: ignore[attr-defined]

    exporter.ExportDb = _LiteExportDb  # type: ignore[attr-defined]
    def _process_all(client, db):  # type: ignore[no-redef]
        if locations:
            mapping = {
                "inbox": exporter.ThreadLocation.INBOX,
                "other": exporter.ThreadLocation.OTHER,
                "archived": exporter.ThreadLocation.ARCHIVED,
            }
            locs = []
            for raw in locations.split(","):
                key = raw.strip().lower()
                if not key:
                    continue
                if key not in mapping:
                    raise RuntimeError(f"Unknown thread location: {raw}")
                locs.append(mapping[key])
            if not locs:
                raise RuntimeError("No thread locations selected.")
        else:
            locs = [
                exporter.ThreadLocation.INBOX,
                exporter.ThreadLocation.OTHER,
                exporter.ThreadLocation.ARCHIVED,
            ]
        threads = []
        for loc in locs:
            exporter.logger.info("Fetching threads: %s", loc)
            thr = client.fetchThreads(loc)
            exporter.logger.info("Fetched %d threads from %s", len(thr), loc)
            threads.extend(thr)
        exporter.logger.info("Total threads: %d", len(threads))

        for idx, thread in enumerate(threads, 1):
            if idx == 1 or idx % 50 == 0:
                exporter.logger.info("Indexing thread %d/%d: %s", idx, len(threads), thread.name)
            db.insert_thread(thread)

        for idx, thread in enumerate(threads, 1):
            if idx == 1 or idx % 25 == 0:
                exporter.logger.info("Exporting thread %d/%d: %s", idx, len(threads), thread.name)
            on = db.get_oldest_and_newest(thread)
            if on is None:
                oldest = None
                newest = None
            else:
                oldest, newest = on

            def error(e):
                exporter.logger.error("While processing thread %s", thread)
                exporter.logger.exception(e)
                yield e

            iter_oldest = exporter.iter_thread(client=client, thread=thread, before=oldest)
            for r in iter_oldest:
                if isinstance(r, Exception):
                    yield from error(r)
                else:
                    db.insert_message(thread, r)

            if newest is not None:
                iter_newest = exporter.iter_thread(client=client, thread=thread, before=None)
                with db.db:
                    for r in iter_newest:
                        if isinstance(r, Exception):
                            yield from error(r)
                        else:
                            mts = int(r.timestamp)
                            if newest > mts:
                                exporter.logger.info(
                                    "%s: fetched all new messages (up to %s)", thread.name, newest
                                )
                                break
                            db.insert_message(thread, r)

            yield from db.check_fetched_all(thread)

    exporter.process_all = _process_all  # type: ignore[attr-defined]

    exporter.run(cookies=cookies, db=output_db)
    typer.secho(f"✓ Exported Messenger DB → {output_db}", fg=typer.colors.GREEN)


if __name__ == "__main__":
    app()
