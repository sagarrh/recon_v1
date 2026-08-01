from __future__ import annotations

import ipaddress
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_PARAMETERS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "msclkid",
    "ref_src",
}


def is_tracking_parameter(name: str) -> bool:
    lowered = name.casefold()
    return lowered in TRACKING_PARAMETERS or lowered.startswith("utm_")


def normalize_url(value: str) -> str:
    parts = urlsplit(value.strip())
    if parts.scheme.casefold() not in {"http", "https"} or not parts.hostname:
        raise ValueError(f"Unsupported citation URL: {value!r}")
    scheme = parts.scheme.casefold()
    host = parts.hostname.casefold().rstrip(".")
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError(f"Invalid URL port: {value!r}") from exc
    default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    netloc = host if port is None or default_port else f"{host}:{port}"
    path = parts.path or "/"
    if path != "/":
        path = path.rstrip("/")
    query_items = [
        (name, item)
        for name, item in parse_qsl(parts.query, keep_blank_values=True)
        if not is_tracking_parameter(name)
    ]
    query = urlencode(query_items, doseq=True)
    return urlunsplit((scheme, netloc, path, query, ""))


def domain_from_url(value: str) -> str:
    hostname = urlsplit(value).hostname
    if hostname is None:
        raise ValueError(f"URL has no hostname: {value!r}")
    return hostname.casefold().rstrip(".")


def is_forbidden_ip(value: str) -> bool:
    address = ipaddress.ip_address(value)
    return any(
        (
            address.is_private,
            address.is_loopback,
            address.is_link_local,
            address.is_multicast,
            address.is_reserved,
            address.is_unspecified,
        )
    )
