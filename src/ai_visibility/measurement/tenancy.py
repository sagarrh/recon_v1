"""Resolve a client to the GSC/GA4 handles that may legitimately be read for it.

GSC rows are keyed by ``site_url`` and GA4 rows by ``property_id``. Neither is a client. Where two
clients point at the same handle, reading it for one of them would report the other's search
performance under their name — so this module refuses rather than guesses.

The one escape hatch is an explicit per-client override, for handles an operator has verified. It
is deliberately per-client and opt-in: a global "trust shared handles" switch would silently
re-enable cross-tenant reads for every client the next time the mapping changed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

# Resolution outcomes. Only `ok` may be measured.
OK = "ok"
NO_HANDLE = "no_handle"                  # client has not connected this source
EXCLUDED = "excluded"                    # internal/test row; never measured, never counted
SHARED_HANDLE = "shared_handle"          # handle belongs to more than one client; refused
UNKNOWN_CLIENT = "unknown_client"        # no user_metrics row at all


@dataclass(frozen=True)
class TenantScope:
    """What may be read for one client, and why."""

    client_id: UUID
    gsc_site_url: str | None
    ga4_property_id: str | None
    owned_domains: frozenset[str]
    gsc_status: str
    ga4_status: str
    warnings: list[str] = field(default_factory=list)
    shared_with: tuple[str, ...] = ()
    # True when the handle is shared and an operator has explicitly cleared this client for it.
    # Domain scoping is then mandatory, not optional — it is the only thing separating the tenants.
    requires_domain_scoping: bool = False

    @property
    def gsc_measurable(self) -> bool:
        return self.gsc_status == OK and bool(self.gsc_site_url)

    @property
    def ga4_measurable(self) -> bool:
        return self.ga4_status == OK and bool(self.ga4_property_id)


def build_handle_owners(
    rows: list[dict[str, Any]],
    *,
    excluded_client_ids: frozenset[str] = frozenset(),
) -> dict[str, set[str]]:
    """Map each GSC/GA4 handle to the client_ids claiming it, ignoring excluded rows.

    Excluded clients are dropped BEFORE ownership is computed, not after. An internal or test
    account attached to a property does not make that property genuinely shared, and counting it
    would refuse measurement for the real client that owns it — the exclusion list would then be
    unable to unblock anything, which is the opposite of its purpose.
    """
    owners: dict[str, set[str]] = {}
    for row in rows or []:
        client_id = str(row.get("client_id") or "").casefold()
        if not client_id or client_id in excluded_client_ids:
            continue
        for column in ("gsc_site_url", "ga4_property_id"):
            handle = (row.get(column) or "").strip()
            if handle:
                owners.setdefault(handle, set()).add(client_id)
    return owners


def _bare_host(value: str) -> str:
    raw = (value or "").strip().lower()
    if not raw:
        return ""
    # GSC domain properties arrive as 'sc-domain:example.com'.
    if raw.startswith("sc-domain:"):
        raw = raw.split(":", 1)[1]
    if "://" not in raw:
        raw = f"//{raw}"
    host = urlsplit(raw).hostname or ""
    return host.rstrip(".").removeprefix("www.")


def host_matches_owned(host: str, owned: frozenset[str]) -> bool:
    """True when `host` is an owned domain or a subdomain of one.

    Suffix matching is deliberately excluded: notclient.com must never match client.com."""
    host = _bare_host(host)
    if not host:
        return False
    return any(host == domain or host.endswith(f".{domain}") for domain in owned)


def _resolve_handle(
    handle: str | None,
    *,
    client_id: str,
    owners: dict[str, set[str]],
    override_allowed: bool,
) -> tuple[str, tuple[str, ...], bool, str | None]:
    """Grade one handle. Returns (status, shared_with, requires_domain_scoping, warning)."""
    if not handle or not handle.strip():
        return NO_HANDLE, (), False, None
    sharers = sorted(owners.get(handle, set()) - {client_id})
    if not sharers:
        return OK, (), False, None
    if not override_allowed:
        return (
            SHARED_HANDLE,
            tuple(sharers),
            False,
            f"handle {handle!r} is shared with {len(sharers)} other client(s); refusing to "
            "attribute it without an explicit override",
        )
    return (
        OK,
        tuple(sharers),
        True,
        f"handle {handle!r} is shared with {len(sharers)} other client(s); measured under an "
        "explicit override, with domain scoping enforced",
    )


def resolve_tenant_scope(
    client_id: UUID,
    *,
    handles: dict[str, str | None] | None,
    owned_domains: frozenset[str],
    handle_owners: dict[str, set[str]],
    excluded_client_ids: frozenset[str] = frozenset(),
    shared_handle_overrides: frozenset[str] = frozenset(),
) -> TenantScope:
    """Decide which sources may be read for this client.

    `handle_owners` maps each handle to every client_id that claims it — the only way to detect
    sharing, since neither source table carries a client_id.
    """
    key = str(client_id).casefold()
    if key in excluded_client_ids:
        return TenantScope(
            client_id=client_id, gsc_site_url=None, ga4_property_id=None,
            owned_domains=owned_domains, gsc_status=EXCLUDED, ga4_status=EXCLUDED,
            warnings=["client is on the measurement exclusion list (internal/test account)"],
        )
    if handles is None:
        return TenantScope(
            client_id=client_id, gsc_site_url=None, ga4_property_id=None,
            owned_domains=owned_domains, gsc_status=UNKNOWN_CLIENT, ga4_status=UNKNOWN_CLIENT,
            warnings=["no user_metrics row for this client; neither source is connected"],
        )

    override_allowed = key in shared_handle_overrides
    site_url = (handles.get("gsc_site_url") or "").strip() or None
    property_id = (handles.get("ga4_property_id") or "").strip() or None

    gsc_status, gsc_shared, gsc_scoping, gsc_warning = _resolve_handle(
        site_url, client_id=key, owners=handle_owners, override_allowed=override_allowed
    )
    ga4_status, ga4_shared, ga4_scoping, ga4_warning = _resolve_handle(
        property_id, client_id=key, owners=handle_owners, override_allowed=override_allowed
    )

    warnings = [w for w in (gsc_warning, ga4_warning) if w]
    requires_scoping = gsc_scoping or ga4_scoping

    # Domain scoping is what separates two clients on one handle. Without a usable owned domain
    # there is nothing to scope by, so an override cannot be honoured.
    if requires_scoping and not owned_domains:
        warnings.append(
            "override requested but the client has no registered domain to scope by; refusing"
        )
        gsc_status = SHARED_HANDLE if gsc_scoping else gsc_status
        ga4_status = SHARED_HANDLE if ga4_scoping else ga4_status
        requires_scoping = False

    # A non-shared handle whose domain does not match the client's is worth flagging but not
    # refusing: sharing is the attribution risk, and an unshared property is unambiguously theirs.
    if gsc_status == OK and site_url and owned_domains and not host_matches_owned(
        site_url, owned_domains
    ):
        warnings.append(
            f"gsc_site_url {site_url!r} is outside the client's registered domains "
            f"({', '.join(sorted(owned_domains))}); verify the mapping"
        )

    return TenantScope(
        client_id=client_id,
        gsc_site_url=site_url,
        ga4_property_id=property_id,
        owned_domains=owned_domains,
        gsc_status=gsc_status,
        ga4_status=ga4_status,
        warnings=warnings,
        shared_with=tuple(dict.fromkeys(gsc_shared + ga4_shared)),
        requires_domain_scoping=requires_scoping,
    )
