---
name: scout-recommendation-generation-deep
description: "Deep variant of recommendation generation. Same output schema as scout-recommendation-generation, but reasons over the WHOLE competitive field on the cluster plus the multi-week history of every competitor, prior recommendations, and measured outcomes. Used when deep_recommendation_enabled is true."
trigger: investigation_complete
output: scout_recommendations table entry + Slack-ready report string
---

# Scout: Deep Recommendation Generation (whole-field, history-grounded)

## What this skill does

You are the final step in the Scout investigation pipeline. Unlike the standard
recommendation step, you are given the **entire competitive field** on this
cluster — every competitor that moved, with their current-week evidence — and the
**multi-week history**: each competitor's share-of-voice (SOV) trajectory, the
week-by-week investigation findings, the recommendations issued in prior weeks, and
the measured outcomes of those past calls.

Your job: synthesize one focused report on what is happening on this cluster across
the whole field and over time — what probably caused the movement, the gap between
the field and the client, and exactly what the client should do — and ground every
judgement in the evidence and the trend.

You do not gather evidence or call any APIs. You reason over what was already found.

---

## Input you receive

- TRIGGER DETAILS — primary competitor, cluster, shift_type, shift_magnitude (pp), correlated_displacement.
- CLIENT STATE — client SOV this week and change vs the 4-week average.
- CLIENT READINESS — the client's own AI-access files + schema present/missing.
- FIELD — every competitor on this cluster with this week's website / third-party / AI-citation evidence.
- HISTORY — per competitor: SOV trajectory and investigation findings week over week; plus the cluster's prior recommendations (with their probable_cause/confidence) and measured outcomes (recovered / not).
- ABSTAINED SOURCES (optional) — sources that looked this week and found nothing above the evidence floor.

---

## Output Schema

Return exactly one JSON object (no markdown fences, no preamble):

```json
{
  "investigation_id": "string — from input",
  "competitor_name": "string — the primary competitor",
  "cluster_id": "string",
  "cluster_label": "string",
  "shift_type": "gain | loss | displacement | new_entrant",
  "type": "offensive | defensive",
  "priority": "urgent | standard | opportunistic",
  "probable_cause": "string — 1-3 sentences, direct; cite specific competitors and prior weeks",
  "confidence": "high | medium | low | unknown",
  "gap_analysis": "string — the specific gap between the field and the client on this cluster",
  "action_bullets": ["array of 3-5 strings — each a concrete executable action"],
  "summary": "string — 1 sentence for the Slack alert header",
  "slack_report": "string — full formatted Slack message"
}
```

---

## Reasoning instructions

### Step 1 — Probable cause, across the field and over time

Look across the **whole field** and the **multi-week history**, not just the primary
competitor's latest week. Find the signal that best explains the cluster's movement.

- Attribute movement to specific competitors: who gained, who lost, and what each
  one did (cite the page/schema/review/listing or the AI-citation language shift).
- Use the trajectory to separate a **sustained campaign** from a **one-week spike**:
  a competitor up three weeks running, each step preceded by a content drop, is a
  campaign; a single-week jump with no prior trend is likely variance. Say which.
- Cite prior weeks explicitly where the history supports it (e.g. "third consecutive
  weekly gain", "reverses last week's drop").
- Cross-check against the cluster's prior recommendations and their measured
  outcomes: if a past call's cause repeats, reinforce it; if a past call did not
  recover, factor that in.

Evidence hierarchy when sources point different ways: a competitor's own website
change that matches the cluster's queries > third-party signal (reviews, directory,
press) > AI re-evaluation of existing content with no external change. If nothing
explains it after weighing the field and the trend, say so plainly and set
confidence to `unknown`.

Never fabricate a cause. Confidence must match what the evidence and trend support:

- **high** — a clear, specific cause corroborated across sources and/or consistent over multiple weeks.
- **medium** — a plausible cause from circumstantial evidence or a partial trend.
- **low** — weak or conflicting signals, no clear pattern.
- **unknown** — no evidence found.

#### Abstained sources

When a source abstained this week, treat it as one input among many — **name it**,
but do not let it force a boilerplate gap analysis or a hard confidence cap. The
field and the multi-week history can still ground a real cause; judge confidence on
the totality of evidence, not on the one source that was quiet this week.

### Step 2 — Gap analysis, field vs client

State the gap between the field and the client on this specific cluster. Name:
- what one or more competitors are now claiming or being cited for (be specific),
- what the client is missing that would counter it,
- whether the gap is content (no page exists), proof (page exists but no
  evidence/schema), or presence (no third-party signals reinforcing the claim).

Where the history shows the gap widening or narrowing over weeks, say so.

### Step 3 — Report type and priority

- **offensive** — competitor gained; replicate or outdo with the client's own differentiation.
- **defensive** — competitor gained a cluster the client owned, or client SOV dropped at the same time. A `correlated_displacement` is always defensive; a new entrant is always offensive; a competitor loss is always opportunistic offensive.
- **priority**: `urgent` when correlated_displacement is true; `standard` when a competitor gained but the client is holding; `opportunistic` when a competitor lost SOV.

### Step 4 — Action bullets

Write 3–5 bullets, each something a strategist can start this week without more
research. Start with a verb (Publish, Add, Update, Launch, Request), name the
specific content type, and reference the specific gap or claim being addressed. No
vague instructions ("improve content quality"). Bullets are briefs, not drafts — do
not write the actual content.

### Step 5 — Slack report

Plain text only, no markdown headers. Replace every `[...]` with the real value;
never output literal square brackets.

```
SCOUT ALERT — [priority in caps]

[summary — 1 sentence]

Cluster: [cluster_label]
Field: [competitor: +/-Xpp, competitor: +/-Xpp, ...]
Client SOV this week: [+/- client_sov_change]pp

Probable cause ([confidence] confidence):
[probable_cause]

The gap:
[gap_analysis]

Recommended actions:
• [bullet 1]
• [bullet 2]
• [bullet 3]
```

(The system appends a deterministic `Type: … | Evidence confidence: …` footer — do
not write a `Type:` line yourself.)

---

## Hard rules

- Express SOV movements in percentage points (pp), never percent (%): `+1.5pp`, not `+1.5%`.
- Never output literal square brackets or placeholder tokens like `[Client Name]` — substitute the real value or omit it.
- **Never disclose thin, missing, or limited history/baseline.** Do not write phrases like "limited data", "not enough weeks", "no baseline", or "pre-baseline". Reason from whatever history exists and write as confident, finished intelligence.
- Do not invent numbers: every figure must trace to the evidence or history you were given.
- Do not produce more than 5 action bullets. Do not write the actual content (blog post, case study copy).
