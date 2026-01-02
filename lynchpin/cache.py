from __future__ import annotations

import json
import time
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Dict, Tuple, TypeVar

from .config import get_config

F = TypeVar("F", bound=Callable[..., Any])


def cache_json(name: str, ttl_seconds: int, loader: Callable[[], Any]) -> Any:
    cfg = get_config()
    path = cfg.cache_dir / f"{name}.json"
    meta_path = cfg.cache_dir / f"{name}.meta.json"
    now = time.time()
    if path.exists() and meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if now - float(meta.get("timestamp", 0)) <= ttl_seconds:
                return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError):
            pass
    value = loader()
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    meta_path.write_text(json.dumps({"timestamp": now}), encoding="utf-8")
    return value


def memoize_to_disk(ttl_seconds: int = 900) -> Callable[[F], F]:
    def decorator(func: F) -> F:
        cache_name = func.__module__.replace(".", "_") + f"_{func.__name__}"

        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            key = json.dumps({"args": args, "kwargs": kwargs}, default=str, sort_keys=True)
            key_name = f"{cache_name}_{hash(key)}"
            return cache_json(key_name, ttl_seconds, lambda: func(*args, **kwargs))

        return wrapper  # type: ignore[return-value]

    return decorator
