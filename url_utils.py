from __future__ import annotations

from urllib.parse import urlparse


SUPPORTED_SCHEMES = ("http://", "https://", "ftp://")
ONION_SUFFIX = ".onion"
TOR_FORCED_DOMAINS = ("check.torproject.org",)


def host_from_text(text: str) -> str:
    value = text.strip()
    if not value:
        return ""
    parsed = urlparse(value if "://" in value else f"//{value}")
    return (parsed.hostname or "").lower()


def is_onion_host(host: str) -> bool:
    host = host.lower().rstrip(".")
    return host == "onion" or host.endswith(ONION_SUFFIX)


def is_onion_url(text: str) -> bool:
    return is_onion_host(host_from_text(text))


def forces_tor(text: str) -> bool:
    host = host_from_text(text)
    if is_onion_host(host):
        return True
    return any(host == domain or host.endswith(f".{domain}") for domain in TOR_FORCED_DOMAINS)


def looks_like_url(text: str) -> bool:
    value = text.strip()
    if not value:
        return False
    if value.startswith(SUPPORTED_SCHEMES):
        return True
    if " " in value:
        return False
    return bool(host_from_text(value)) and "." in host_from_text(value)


def normalize_url(text: str) -> str:
    value = text.strip()
    if value.startswith(SUPPORTED_SCHEMES):
        return value
    scheme = "http://" if is_onion_url(value) else "https://"
    return scheme + value
