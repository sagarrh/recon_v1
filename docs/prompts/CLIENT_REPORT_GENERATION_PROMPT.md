# Reusable Client Report Generation Prompt

Use this prompt with Codex or Claude Code from the repository root. Replace
`<company-slug>` with the output folder name, such as `aprio`. When a run-scoped
input exists, prefer `output/<company-slug>/runs/<parent-run-id>/inputs/report-input-snapshot.json`
so the report can be reproduced from one exact evidence cycle.

```text
Create the final client-facing HTML intelligence report using these local files:

FACTUAL INPUT:
output/<company-slug>/inputs/report-input-snapshot.json

VISUAL AND STRUCTURAL REFERENCE:
docs/reference/report-samples/report_jaggaer.html

OUTPUT:
output/<company-slug>/final-report-manual.html

Read both input files completely before writing the report.

Use report-input-snapshot.json as the sole factual source. It contains the
compact, validated AI Citation and Recon report inputs. Treat the Jaggaer HTML
only as a design, layout, component, and presentation reference. Do not copy
Jaggaer's company-specific text, numbers, findings, links, dates, or conclusions.

Reuse and adapt the sample report's established HTML/CSS structure instead of
designing the report from scratch. Preserve its professional client-facing
qualities, including the cover, executive summary, metric cards, topic sections,
comparison visualizations, priority recommendations, action plan, methodology,
responsive behavior, and print styling. Add, remove, or rename sections when the
available evidence requires it; never create empty or misleading sections merely
to imitate the sample.

Content requirements:

1. Treat the factual JSON as authoritative. Do not invent, estimate, or silently
   recalculate metrics.
2. Verify every displayed number, date, company, finding, and URL against the
   JSON before finishing.
3. Preserve exact AI visibility, citation, SOV, competitor, provider, query,
   cluster, trend, and recommendation values where present.
4. Explain the evidence in clear client-facing language: what changed, why it
   matters, and what the client should do next.
5. Include an executive summary, AI Citation performance, SOV and competitive
   performance, important topics or clusters, citation-source opportunities,
   prioritized recommendations, action plan, and concise methodology/data-quality
   notes when supported by the input.
6. Do not expose database table names, UUIDs, checksums, pipeline stages, internal
   debug information, or analyst-only implementation details.
7. Do not present NOISE-classified records as findings.
8. Do not claim causation unless the evidence explicitly supports it. Clearly
   distinguish observations, correlations, and verified page changes.
9. Disclose material missing evidence, confidence limitations, incomplete page
   history, or partial source coverage briefly and honestly.
10. Include source links when URLs are supplied, with safe escaped HTML.
11. Use only standalone HTML, embedded CSS, and optional embedded JavaScript.
    Do not require external libraries, fonts, CDNs, APIs, or network access.
12. Make the output responsive, accessible, printable, and suitable for direct
    client delivery.
13. Write “percentage points” in full in client-facing prose and labels. Do not
    use the abbreviation “pp” unless it is defined next to its first use. Use `%`
    for a measured percentage and “percentage points” for the arithmetic
    difference between two percentages. For example: “Visibility increased from
    40% to 60%, a gain of 20 percentage points.” Never label that change `+20%`.
14. Keep these measures distinct and explain them briefly where needed:
    literal answer visibility, share of voice (SOV), raw citation occurrences,
    distinct-answer citation coverage, and citation-source movement. Do not use
    one as a synonym or substitute for another.
15. Check the input for contradictions before writing. Prefer current measured
    tables and deterministic metrics over older narrative summaries or
    recommendations. Do not repeat a stale narrative claim that conflicts with a
    current metric. Briefly disclose any material unresolved contradiction.
16. If a stored percentage conflicts with its numerator, denominator, delta, or
    narrative, do not reverse-engineer or invent replacement counts. Present only
    the internally supported value, lower the strength of the wording, and
    disclose the metric mismatch in the data-quality section.
17. Treat Recon explanations of competitor movement as working hypotheses unless
    direct response, citation, or historical page evidence verifies them. Avoid
    phrases such as “caused,” “drove,” or “resulted in” for correlation-only
    evidence.
18. Consolidate duplicated recommendations into a short prioritized action plan.
    Preserve their factual intent, but do not repeat multiple versions of the same
    action or carry forward unsupported premises from their narrative text.
19. Do not describe a URL as client-owned when the input is contradictory about
    ownership. In that case, identify the URL or domain without asserting
    ownership.
20. Define acronyms such as SOV on first use, use human-readable dates, avoid
    unnecessary analyst jargon, and prefer direct sentences that explain the
    business implication of each metric.
21. Before finishing, check that the HTML has no empty charts or sections, no
    placeholder text, no stale facts copied from the sample, no broken local
    structure, and no external runtime dependencies.

Do not modify the source JSON, sample HTML, application code, or database. Write
only the requested output file. After writing it, inspect the generated HTML and
report any factual field that could not be presented because the input was
missing or ambiguous.
```
