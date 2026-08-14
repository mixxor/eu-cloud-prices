"""Shared helpers for provider fetchers."""

from __future__ import annotations

import gzip
import json
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

HOURS_PER_MONTH = 730

USER_AGENT = "Mozilla/5.0 (compatible; eu-cloud-prices-fetcher/1.0)"

#: Every upstream host any fetcher is permitted to contact. A fetcher cannot
#: request an arbitrary URL; adding a provider means adding its host here.
ALLOWED_HOSTS = frozenset({
    "data-api.ecb.europa.eu",
    "b0.p.awsstatic.com",
    "www.gstatic.com",
    "azure.microsoft.com",
    "prices.azure.com",
    "api.ovh.com",
    "api.scaleway.com",
    "api.atlantic.cloud",
})

#: Keys a fetcher is allowed to own. Everything else in an existing price file
#: is hand-maintained and is preserved verbatim by ``merge_price_file``.
FETCHER_OWNED_KEYS = frozenset({
    "provider", "fetched_at", "source", "source_url", "source_zone",
    "source_region", "manual", "instances", "gpu_instances",
    "eur_usd_rate", "fx",
})


class FetchError(Exception):
    """Raised when an upstream fetch or parse fails."""


def _validate_fetch_url(url: str) -> None:
    """Raise FetchError unless ``url`` uses http/https and its host is in ALLOWED_HOSTS.

    Called for both the initial URL and every redirect target, so the
    allowlist is enforced on every hop. Uses ``.hostname`` rather than
    ``netloc``, which strips port and userinfo, so "evil.com@ovh.com"
    resolves to its true target host instead of bypassing the check.
    """
    parsed = urllib.parse.urlparse(url)

    if parsed.scheme not in ("http", "https"):
        raise FetchError(f"scheme {parsed.scheme!r} is not allowed; only http and https permitted")

    host = parsed.hostname
    if host is None or host not in ALLOWED_HOSTS:
        raise FetchError(f"host {host!r} is not allowed")


class _AllowlistEnforcingHTTPRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Custom redirect handler that enforces ALLOWED_HOSTS on every hop."""

    def redirect_request(self, req, fp, code, msg, hdrs, newurl):
        _validate_fetch_url(newurl)
        return super().redirect_request(req, fp, code, msg, hdrs, newurl)


def http_get_json(url: str, headers: dict | None = None, *, timeout: int = 60) -> dict:
    _validate_fetch_url(url)

    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept-Encoding": "gzip, deflate",
        **(headers or {}),
    })

    opener = urllib.request.build_opener(_AllowlistEnforcingHTTPRedirectHandler)

    try:
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip" or raw[:2] == b"\x1f\x8b":
                raw = gzip.decompress(raw)
            return json.loads(raw.decode("utf-8"))
    except FetchError:
        raise
    except Exception as exc:  # noqa: BLE001 - upstream failures are all equivalent here
        raise FetchError(f"GET {url} failed: {exc}") from exc


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def merge_price_file(existing: dict, fresh: dict) -> dict:
    """Overlay ``fresh`` onto ``existing`` without ever dropping a key.

    Every key present in ``existing`` survives unless ``fresh`` also sets
    it, in which case ``fresh``'s value wins. This protects hand-maintained,
    fetcher-less keys (e.g. ``primary_ip``) while still letting
    fetcher-owned blocks (``load_balancer``, ``block_storage``, ``egress``,
    ``object_storage``, ``control_plane_cost``) be replaced on every run.
    """
    return {**existing, **fresh}


def load_existing(name: str, prices_dir: Path) -> dict:
    path = prices_dir / f"{name}.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def write_price_file(name: str, fresh: dict, prices_dir: Path) -> Path:
    path = prices_dir / f"{name}.json"
    merged = merge_price_file(load_existing(name, prices_dir), fresh)
    path.write_text(json.dumps(merged, indent=2, ensure_ascii=False) + "\n")
    return path


@dataclass
class Context:
    """Everything a fetcher needs, injected so tests can run without network."""

    prices_dir: Path
    fx: Any = None
    http_get_json: Callable[..., dict] = field(default=http_get_json)
