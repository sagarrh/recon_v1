# handoff.py — Tier 3: deploy hand-off packages (FR-DEPLOY). Scout NEVER writes to a client site:
# this module emits local files a human Deploy Executor places. Client-facing copy carries the
# Tier-1 figure with its basis tag and obeys no-baseline-disclosure (confident finished intel).
import json
import os
from datetime import UTC, datetime

_EXT = {"schema_jsonld": ".jsonld", "faq_page": ".jsonld", "llms_txt": ".txt",
        "ai_bots_allowlist": ".txt", "robots_txt": ".txt"}

_INSTRUCTIONS = {
    "schema_jsonld": (
        "1. Open the page template for {target}.\n"
        "2. Paste each JSON-LD object from the artifact into its own "
        "`<script type=\"application/ld+json\">...</script>` block inside `<head>`.\n"
        "3. Publish and confirm the page renders unchanged.\n"
        "To remove: delete the inserted script block(s)."
    ),
    "faq_page": (
        "1. Open the page template for {target}.\n"
        "2. Paste the FAQPage JSON-LD object into a `<script type=\"application/ld+json\">` block in `<head>`.\n"
        "3. Ensure the visible page answers match the marked-up questions.\n"
        "To remove: delete the inserted script block."
    ),
    "llms_txt": (
        "1. Publish the artifact file at {target}/llms.txt (site root, exact path).\n"
        "2. Confirm it is publicly reachable (HTTP 200, text/plain).\n"
        "To remove: delete the file."
    ),
    "ai_bots_allowlist": (
        "1. Append the directives in the artifact to the robots.txt served at {target}/robots.txt.\n"
        "2. Do not remove existing directives; add these blocks at the end.\n"
        "To remove: delete the appended blocks."
    ),
    "robots_txt": (
        "1. Append the directives in the artifact to the robots.txt served at {target}/robots.txt.\n"
        "2. Do not remove existing directives; add these blocks at the end.\n"
        "To remove: delete the appended blocks."
    ),
}


def install_instructions(asset_class: str, target: str) -> str:
    tpl = _INSTRUCTIONS.get(asset_class, "Place the artifact at {target} per your platform's process.\nTo remove: delete it.")
    return tpl.format(target=(target or "").rstrip("/"))


def revenue_rationale_line(rec_or_brief: dict) -> str:
    """Client-facing: confident, basis-tagged, never $0, never a data-gap disclosure."""
    d = rec_or_brief or {}
    cluster = d.get("cluster_label") or "this demand cluster"
    usd = d.get("revenue_opportunity_usd")
    basis = d.get("revenue_basis") or "none"
    if usd is None or usd == 0 or basis == "none":
        return f"Built to capture the revenue opportunity on {cluster} (value not yet quantified)."
    return f"Built to capture the ${usd:,.0f} [{basis}] opportunity on {cluster}."


def create_handoff_package(assets: list[dict], recs: dict[str, dict], output_dir: str) -> dict:
    os.makedirs(output_dir, exist_ok=True)
    entries: list[dict] = []
    doc: list[str] = ["# Deployment Hand-off Package", ""]

    for asset in assets:
        cls = asset.get("asset_type", "unknown")
        aid = str(asset.get("id", ""))[:8] or f"{len(entries) + 1:02d}"
        filename = f"{aid}-{cls}{_EXT.get(cls, '.txt')}"
        with open(os.path.join(output_dir, filename), "w", encoding="utf-8") as f:
            f.write(asset.get("drafted_payload") or "")
        rec = recs.get(asset.get("recommendation_id"), {})
        rationale = revenue_rationale_line({**rec, "cluster_label": asset.get("cluster_label")
                                            or rec.get("cluster_label")})
        entries.append({"filename": filename, "asset_class": cls, "asset_id": asset.get("id"),
                        "target": asset.get("target"), "recommendation_id": asset.get("recommendation_id")})
        doc += [f"## {filename}", "", rationale, "",
                f"Target: {asset.get('target')}", "",
                install_instructions(cls, asset.get("target") or ""), ""]

    manifest = {"created_at": datetime.now(UTC).isoformat(),
                "total_assets": len(entries), "assets": entries}
    with open(os.path.join(output_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    with open(os.path.join(output_dir, "INSTRUCTIONS.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(doc))
    return manifest
