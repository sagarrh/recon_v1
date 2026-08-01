from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import httpcore
import httpx


class PinnedNetworkBackend(httpcore.SyncBackend):
    """Resolve exactly once, then connect only to the validated address."""

    def __init__(self, hostname: str, address: str) -> None:
        self._hostname = hostname.casefold().rstrip(".")
        self._address = address

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.NetworkStream:
        if host.casefold().rstrip(".") != self._hostname:
            raise httpcore.ConnectError(f"Refusing an unvalidated transport hostname: {host}")
        return super().connect_tcp(
            self._address,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )


def pinned_transport(hostname: str, address: str) -> httpx.HTTPTransport:
    """Build a TLS-preserving transport pinned to one pre-validated address.

    HTTPX does not currently expose network-backend injection publicly. The
    compatible HTTPX/HTTPCore range is therefore capped in ``pyproject.toml``,
    and this function fails explicitly if that integration point changes.
    """
    transport = httpx.HTTPTransport(trust_env=False)
    existing_pool = getattr(transport, "_pool", None)
    if existing_pool is None or not hasattr(existing_pool, "close"):
        transport.close()
        raise RuntimeError("Installed HTTPX is incompatible with the pinned transport.")
    existing_pool.close()
    transport._pool = httpcore.ConnectionPool(
        ssl_context=httpx.create_ssl_context(verify=True, trust_env=False),
        network_backend=PinnedNetworkBackend(hostname, address),
        max_connections=2,
        max_keepalive_connections=1,
    )
    return transport
