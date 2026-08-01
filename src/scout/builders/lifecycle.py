# lifecycle.py — Tier 3: pure asset lifecycle state machine (no I/O, no LLM).
# Purpose: The single source of truth for which lifecycle transitions are legal and which
#          human-approval ledger evidence each transition requires (PRD §7.3 matrix).
# Consumers: scout/db/asset_writer.transition_asset (enforcement), tests.

_ALLOWED: dict[str | None, set[str]] = {
    None: {"generated"},                              # a Tier-2 target row enters the builder
    "generated": {"internal_approved", "needs_edit", "rejected", "superseded"},
    "needs_edit": {"generated", "rejected", "superseded"},
    "internal_approved": {"client_approved", "handed_off", "needs_edit", "rejected", "superseded"},
    "client_approved": {"handed_off", "needs_edit", "rejected", "superseded"},
    "handed_off": {"verified", "verify_failed", "superseded"},
    "verify_failed": {"handed_off", "needs_edit", "superseded"},   # re-check after redeploy, or re-work
    "verified": {"superseded"},
    "rejected": set(),                                 # terminal
    "superseded": set(),                               # terminal
}


def _has(approvals, gate: str, decision: str) -> bool:
    return any(a.get("gate") == gate and a.get("decision") == decision for a in (approvals or []))


def can_transition(current: str | None, new: str, *, approvals: list[dict],
                   client_approval_required: bool = True) -> tuple[bool, str]:
    """Return (allowed, reason). Evidence-based: approved states and handed_off require the
    corresponding append-only ledger rows to already exist — code cannot skip a human gate."""
    if current not in _ALLOWED:
        return False, f"unknown current state: {current!r}"
    if new not in _ALLOWED.get(current, set()):
        return False, f"illegal transition {current!r} -> {new!r}"
    if new == "internal_approved" and not _has(approvals, "internal", "internal_approved"):
        return False, "internal_approved requires an internal-gate ledger entry"
    if new == "client_approved" and not _has(approvals, "external", "client_approved"):
        return False, "client_approved requires an external-gate ledger entry"
    if new == "handed_off":
        if not _has(approvals, "internal", "internal_approved"):
            return False, "handed_off requires internal approval on the ledger"
        if client_approval_required and not _has(approvals, "external", "client_approved"):
            return False, "handed_off requires client approval on the ledger (policy: required)"
    return True, ""
