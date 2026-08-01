from __future__ import annotations

import socket
from urllib.parse import urlsplit

from ai_visibility.utils.urls import is_forbidden_ip

_BLOCKED_HOSTNAMES = {
    "localhost",
    "metadata",
    "metadata.google.internal",
    "instance-data",
}


def validate_public_url(url: str) -> list[str]:
    if len(url) > 8_192:
        raise ValueError("Page URL exceeds the configured safety length.")
    parts = urlsplit(url)
    if parts.scheme.casefold() not in {"http", "https"}:
        raise ValueError("Only HTTP and HTTPS URLs may be fetched.")
    if parts.username or parts.password:
        raise ValueError("URLs containing credentials are not allowed.")
    hostname = (parts.hostname or "").casefold().rstrip(".")
    if not hostname or hostname in _BLOCKED_HOSTNAMES or hostname.endswith(".internal"):
        raise ValueError("Internal or missing hostname is not allowed.")
    try:
        addresses = sorted(
            {
                address
                for item in socket.getaddrinfo(
                    hostname,
                    parts.port or (443 if parts.scheme.casefold() == "https" else 80),
                    type=socket.SOCK_STREAM,
                )
                if isinstance((address := item[4][0]), str)
            }
        )
    except socket.gaierror as exc:
        raise ValueError(f"Hostname could not be resolved: {hostname}") from exc
    if not addresses:
        raise ValueError(f"Hostname has no addresses: {hostname}")
    forbidden = [address for address in addresses if is_forbidden_ip(address)]
    if forbidden:
        raise ValueError(f"Hostname resolves to a non-public address: {hostname}")
    return addresses
