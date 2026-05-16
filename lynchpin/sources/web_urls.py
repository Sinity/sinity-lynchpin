"""URL normalization helpers for webhistory sources."""

from __future__ import annotations

from urllib.parse import parse_qs, urlencode, urlparse


TRACKING_PREFIXES = {
    "utm_",
    "fbclid",
    "gclid",
    "igshid",
    "yclid",
    "dclid",
    "ref_",
    "spm",
    "sc_",
    "mc_",
    "mkt_",
    "pk_campaign",
    "pk_kwd",
    "ga_",
    "gs_",
    "ved",
    "ei",
    "sa",
    "rlz",
    "dpr",
    "biw",
    "bih",
}

SPECIAL_PARAM_WHITELIST = {
    "youtube.com": {"v", "list", "t"},
    "youtu.be": {"v", "t"},
    "github.com": {"ref", "sha"},
    "reddit.com": {"sort", "type", "t"},
    "twitter.com": {"s", "q"},
}


def normalize_url(url: str) -> str:
    if not url:
        return ""
    try:
        parsed = urlparse(url.strip())
        if not parsed.netloc:
            return url.strip()
        scheme = (parsed.scheme or "https").lower()
        if scheme not in ("http", "https"):
            return url.strip()
        host = _normalize_domain(parsed.netloc)
        path = parsed.path or "/"
        if len(path) > 1 and path.endswith("/"):
            path = path[:-1]
        query = parse_qs(parsed.query, keep_blank_values=True)
        cleaned = _strip_tracking_params(query, host)
        if host == "youtu.be" and path.lstrip("/"):
            vid = path.lstrip("/")
            host = "youtube.com"
            path = "/watch"
            if "v" not in cleaned:
                cleaned["v"] = [vid]
            cleaned = _strip_tracking_params(cleaned, host)
        query_str = urlencode(cleaned, doseq=True)
        rebuilt = f"https://{host}{path}"
        if query_str:
            rebuilt += f"?{query_str}"
        return rebuilt
    except Exception:
        return url.strip()


def _strip_tracking_params(
    query: dict[str, list[str]], host: str
) -> dict[str, list[str]]:
    keep = SPECIAL_PARAM_WHITELIST.get(host.split(":")[0], set())
    return {
        k: v
        for k, v in query.items()
        if k in keep or not any(k.startswith(p) for p in TRACKING_PREFIXES)
    }


def _normalize_domain(netloc: str) -> str:
    netloc = netloc.strip().lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    if ":" in netloc:
        netloc = netloc.split(":", 1)[0]
    return netloc
