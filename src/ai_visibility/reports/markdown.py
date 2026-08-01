from __future__ import annotations

# ruff: noqa: E501
from typing import Any

from jinja2 import Environment, StrictUndefined

_TEMPLATE = """# Company Intelligence Report: {{ report.company.canonical_name }}

Generated from the complete valid monitoring history for client
`{{ report.company.client_id }}`.

## Executive summary

- Period: {{ report.analysis_period.start }} to {{ report.analysis_period.end }}
- Valid runs: {{ report.analysis_period.valid_run_count }}
- Invalid runs excluded: {{ report.analysis_period.invalid_run_count }}
- Latest weighted literal visibility: {{ "%.1f%%" | format(report.overall_visibility.latest_weighted_visibility * 100) }}
- Overall direction: {{ report.executive_summary.overall_direction }}
- Primary hypothesis: {{ report.executive_summary.primary_hypothesis.type if report.executive_summary.primary_hypothesis else "insufficient evidence" }}
- Confidence: {{ report.executive_summary.confidence }}

{% if report.signals %}
## Material signals

{% for signal in report.signals %}
### {{ signal.signal_type | replace("_", " ") | title }} — {{ signal.observed.provider }}

Observed: {{ signal.observed.company }} changed from
{{ signal.observed.previous_literal_answer_count }}/{{ signal.observed.previous_answer_count | default("?") }}
answers (visibility {{ "%.1f%%" | format(signal.observed.previous_literal_visibility * 100) }})
to {{ signal.observed.current_literal_answer_count }}/{{ signal.observed.current_answer_count | default("?") }}
(visibility {{ "%.1f%%" | format(signal.observed.current_literal_visibility * 100) }}).

Inference: {{ signal.primary_hypothesis.inference }}

Primary hypothesis: **{{ signal.primary_hypothesis.type | replace("_", " ") | title }}**.

Confidence: **{{ signal.confidence }}**. Single-page causation confidence:
**{{ signal.single_page_causation_confidence }}**.

Evidence:

{% if signal.evidence.recommendation_pattern %}
- Repeated recommendation bundle frequency changed from
  {{ "%.1f%%" | format(signal.evidence.recommendation_pattern.previous_frequency * 100) }}
  to {{ "%.1f%%" | format(signal.evidence.recommendation_pattern.current_frequency * 100) }}.
- Bundle: {{ signal.evidence.recommendation_pattern.bundle | join(", ") }}
{% endif %}
{% for source in signal.evidence.sources[:8] %}
- `{{ source.url }}`: {{ source.status }}, coverage
  {{ source.previous_answer_coverage }} → {{ source.current_answer_coverage }},
  relationship `{{ source.relationship }}`, attribution confidence
  `{{ source.attribution.confidence }}`.
{% endfor %}

Alternative explanations:

{% for alternative in signal.alternative_explanations %}
- {{ alternative }}
{% endfor %}

Warnings: {{ signal.warnings | join(", ") }}

{% endfor %}
{% endif %}
## Provider intelligence

| Provider | Runs | Latest visibility | Average | Trend | Volatility |
|---|---:|---:|---:|---|---:|
{% for provider in report.provider_intelligence -%}
| {{ provider.provider }} | {{ provider.run_count }} | {{ "%.1f%%" | format(provider.latest_visibility * 100) }} | {{ "%.1f%%" | format(provider.average_visibility * 100) }} | {{ provider.trend }} | {{ "%.3f" | format(provider.volatility) }} |
{% endfor %}

## Competitor intelligence

{% for competitor in report.competitor_intelligence[:15] %}
- {{ competitor.company }}: {{ competitor.status }}; visibility delta
  {{ "%+.1f%%" | format(competitor.visibility_delta * 100) }} on {{ competitor.provider }}.
{% else %}
- No material competitor movement was calculable.
{% endfor %}

## Recommended actions

{% for action in report.recommended_actions %}
- {{ action }}
{% endfor %}

## Data quality and limitations

{% for warning in report.data_quality_flags %}
- {{ warning }}
{% endfor %}

The report treats `public.ai_monitoring` as immutable evidence. Literal company
visibility is recomputed from distinct answers, upstream metrics remain
available for comparison, raw citation occurrences are separate from distinct
answer coverage, and URL/company co-occurrence is never presented as proven
causation. Historical page changes are not claimed without stored snapshots.
"""


def render_markdown(report: dict[str, Any]) -> str:
    environment = Environment(
        autoescape=False,
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    return environment.from_string(_TEMPLATE).render(report=report).strip() + "\n"
