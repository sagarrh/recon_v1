from __future__ import annotations

from datetime import date, datetime
from importlib.resources import files

from jinja2 import Environment, select_autoescape

from aivc.reporting.models import FinalReportSnapshot


def _percent(value: float | int | None) -> str:
    return "—" if value is None else f"{float(value) * 100:.1f}%"


def _points(value: float | int | None) -> str:
    return "—" if value is None else f"{float(value):+.2f}pp"


def _sov(value: float | int | None) -> str:
    return "—" if value is None else f"{float(value):.2f}%"


def _date(value: date | datetime | str | None) -> str:
    if value is None:
        return "Unavailable"
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value).split("T", maxsplit=1)[0].split(" ", maxsplit=1)[0]


def render_html(snapshot: FinalReportSnapshot) -> str:
    snapshot.verify_checksum()
    template_text = (
        files("aivc.resources")
        .joinpath("templates/final_report_client.html.j2")
        .read_text(encoding="utf-8")
    )
    environment = Environment(
        autoescape=select_autoescape(default=True), trim_blocks=True, lstrip_blocks=True
    )
    environment.filters["percent"] = _percent
    environment.filters["points"] = _points
    environment.filters["sov"] = _sov
    environment.filters["date"] = _date
    return environment.from_string(template_text).render(report=snapshot)
