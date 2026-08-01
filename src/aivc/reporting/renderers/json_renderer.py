from __future__ import annotations

import json

from aivc.reporting.models import FinalReportSnapshot


def render_json(snapshot: FinalReportSnapshot) -> str:
    snapshot.verify_checksum()
    return json.dumps(snapshot.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n"
