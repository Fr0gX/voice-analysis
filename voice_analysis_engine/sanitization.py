"""Deterministic redaction for values copied into public result artifacts."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


_SENSITIVE_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "password",
    "passwd",
    "secret",
    "token",
)
_PATH_KEY_SUFFIXES = ("_dir", "_directory", "_path", "_root")
_ABSOLUTE_WINDOWS_PATH = re.compile(r"^[A-Za-z]:[\\/]")


def sanitize_public(value: Any, key: str = "") -> Any:
    """Keep input shape while removing credentials and host filesystem paths."""
    normalized_key = key.casefold()
    if any(part in normalized_key for part in _SENSITIVE_KEY_PARTS):
        return "<redacted>" if value is not None else None
    if isinstance(value, dict):
        return {str(item_key): sanitize_public(item_value, str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [sanitize_public(item, key) for item in value]
    if isinstance(value, tuple):
        return [sanitize_public(item, key) for item in value]
    if isinstance(value, str):
        if normalized_key.endswith(_PATH_KEY_SUFFIXES) or _is_absolute_path(value):
            name = Path(value.replace("\\", "/")).name
            return name or "<path>"
        if value.startswith(("http://", "https://")):
            return _sanitize_url(value)
    return value


def _is_absolute_path(value: str) -> bool:
    return bool(_ABSOLUTE_WINDOWS_PATH.match(value)) or value.startswith(("/", "\\\\"))


def _sanitize_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname or ""
        netloc = hostname
        if ":" in hostname and not hostname.startswith("["):
            netloc = f"[{hostname}]"
        if parsed.port is not None:
            netloc += f":{parsed.port}"
        query = urlencode([
            (name, "<redacted>" if any(part in name.casefold() for part in _SENSITIVE_KEY_PARTS) else item)
            for name, item in parse_qsl(parsed.query, keep_blank_values=True)
        ])
        return urlunsplit((parsed.scheme, netloc, parsed.path, query, parsed.fragment))
    except ValueError:
        return "<redacted-url>"
