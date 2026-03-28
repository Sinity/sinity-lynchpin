from __future__ import annotations

import asyncio
import getpass
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Optional

import typer


def find_chrome_binary() -> Optional[str]:
    for candidate in ("google-chrome-stable", "google-chrome", "chromium", "chromium-browser"):
        path = shutil.which(candidate)
        if path:
            return path
    return None


def requests_user_agent() -> str:
    try:
        import requests  # type: ignore
    except Exception:
        return "python-requests/2.32.3"
    return f"python-requests/{requests.__version__}"


def fetch_cookies_via_devtools(ws_url: str, domains: list[str]) -> dict[str, str]:
    if not domains:
        return {}

    async def fetch() -> dict[str, str]:
        import json as _json

        try:
            import websockets  # type: ignore
        except Exception as exc:  # pragma: no cover - import guard
            raise RuntimeError("websockets is required for Chrome remote cookie fetch") from exc

        def extract(message: dict[str, object]) -> dict[str, str]:
            if message.get("error"):
                return {}
            cookies: dict[str, str] = {}
            for cookie in message.get("result", {}).get("cookies", []):
                cookie_domain = cookie.get("domain", "")
                if any(domain in cookie_domain for domain in domains) and cookie.get("value"):
                    cookies[cookie["name"]] = cookie["value"]
            return cookies

        async with websockets.connect(ws_url, open_timeout=5) as ws:
            async def recv(timeout: float = 5.0) -> dict[str, object]:
                data = await asyncio.wait_for(ws.recv(), timeout=timeout)
                return _json.loads(data)

            async def send(
                message_id: int,
                method: str,
                params: Optional[dict[str, object]] = None,
            ) -> dict[str, object]:
                payload: dict[str, object] = {"id": message_id, "method": method}
                if params:
                    payload["params"] = params
                await ws.send(_json.dumps(payload))
                while True:
                    message = await recv()
                    if message.get("id") == message_id:
                        return message

            await send(1, "Network.enable")
            urls = [f"https://{domain.lstrip('.')}" for domain in domains]
            message = await send(2, "Network.getCookies", {"urls": urls})
            cookies = extract(message)
            if not cookies:
                message = await send(3, "Network.getAllCookies")
                cookies = extract(message)
            if not cookies:
                await send(4, "Page.enable")
                await send(5, "Page.navigate", {"url": "https://messenger.com/"})
                for _ in range(100):
                    try:
                        message = await recv()
                    except asyncio.TimeoutError:
                        break
                    if message.get("method") == "Page.loadEventFired":
                        break
                message = await send(6, "Network.getCookies", {"urls": urls})
                cookies = extract(message)
                if not cookies:
                    message = await send(7, "Network.getAllCookies")
                    cookies = extract(message)
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


def devtools_ws_from_port(port: int) -> Optional[str]:
    base = f"http://127.0.0.1:{port}"
    try:
        with urllib.request.urlopen(f"{base}/json/list", timeout=3) as response:
            data = json.loads(response.read().decode("utf-8"))
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
        with urllib.request.urlopen(f"{base}/json/version", timeout=3) as response:
            data = json.loads(response.read().decode("utf-8"))
        return data.get("webSocketDebuggerUrl")
    except Exception:
        return None


def profile_from_cookie_db(cookie_db: Path) -> tuple[Path, str]:
    return cookie_db.parent.parent, cookie_db.parent.name


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


def wait_for_devtools_port(user_data_dir: Path, timeout: float = 10.0) -> Optional[int]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        port = _read_devtools_active_port(user_data_dir)
        if port:
            return port
        time.sleep(0.25)
    return None


def chrome_lock_pid(user_data_dir: Path) -> Optional[int]:
    lock_path = user_data_dir / "SingletonLock"
    if not lock_path.exists():
        return None
    pid: Optional[int] = None
    try:
        name = lock_path.resolve().name
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


def prepare_debug_profile(cookie_db: Path) -> tuple[Path, str]:
    temp_root = Path(tempfile.mkdtemp(prefix="lynchpin-chrome-debug-"))
    profile_name = cookie_db.parent.name
    profile_dir = temp_root / profile_name
    profile_dir.mkdir(parents=True, exist_ok=True)

    for name in (
        "Cookies",
        "Cookies-journal",
        "Preferences",
        "Secure Preferences",
        "Login Data",
        "Login Data-journal",
        "Web Data",
        "Web Data-journal",
    ):
        src = cookie_db.parent / name
        if src.exists():
            shutil.copy2(src, profile_dir / src.name)

    local_state = cookie_db.parents[1] / "Local State"
    if local_state.exists():
        shutil.copy2(local_state, temp_root / "Local State")

    return temp_root, profile_name


def launch_debug_chrome(
    user_data_dir: Path,
    profile_dir: str,
    port: Optional[int],
) -> tuple[subprocess.Popen[str], Optional[str]]:
    chrome = find_chrome_binary()
    if not chrome:
        raise RuntimeError("Chrome binary not found in PATH.")
    resolved_port = 0 if port is None else port
    active_file = user_data_dir / "DevToolsActivePort"
    if resolved_port == 0 and active_file.exists():
        try:
            active_file.unlink()
        except OSError:
            pass
    command = [
        chrome,
        f"--remote-debugging-port={resolved_port}",
        "--remote-allow-origins=*",
        "--remote-debugging-address=127.0.0.1",
        f"--user-data-dir={user_data_dir}",
        f"--profile-directory={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    proc = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)
    ws_url: Optional[str] = None
    if resolved_port == 0:
        port_from_file = wait_for_devtools_port(user_data_dir)
        if port_from_file:
            ws_url = devtools_ws_from_port(port_from_file)
    else:
        for _ in range(20):
            time.sleep(0.5)
            ws_url = devtools_ws_from_port(resolved_port)
            if ws_url:
                break
    return proc, ws_url


def maybe_start_keyring() -> None:
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
        if not fragment or fragment.startswith("export ") or "=" not in fragment:
            continue
        key, value = fragment.split("=", 1)
        if key and value:
            os.environ[key] = value


def maybe_unlock_keyring() -> bool:
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


def iter_cookie_db_candidates(default_cookie_db: Path) -> list[Path]:
    candidates = [default_cookie_db]
    for root in (Path("~/.config/google-chrome").expanduser(), Path("~/.config/chromium").expanduser()):
        if not root.exists():
            continue
        for path in sorted(root.glob("*/Cookies")):
            if path not in candidates:
                candidates.append(path)
    return candidates
