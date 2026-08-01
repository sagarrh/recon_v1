from __future__ import annotations

from importlib.resources import files

from jinja2 import Environment

from aivc.reporting.models import FinalReportSnapshot


def _percent(value: float | int | None) -> str:
    return "—" if value is None else f"{float(value) * 100:.1f}%"


def _points(value: float | int | None) -> str:
    return "—" if value is None else f"{float(value):+.2f}pp"


def render_markdown(snapshot: FinalReportSnapshot) -> str:
    snapshot.verify_checksum()
    template_text = files("aivc.resources").joinpath(
        "templates/final_report.md.j2"
    ).read_text(encoding="utf-8")
    environment = Environment(autoescape=False, trim_blocks=True, lstrip_blocks=True)
    environment.filters["percent"] = _percent
    environment.filters["points"] = _points
    return environment.from_string(template_text).render(report=snapshot) + "\n"
