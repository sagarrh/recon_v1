---
name: scout-website-diff-analysis
description: "Second skill in the Scout investigation pipeline, running in parallel with scout-ai-response-analysis and scout-web-intelligence. Triggered after a significant SOV shift is detected. Reads the current Apify scrape of the competitor's domain and diffs it against the previous scrape stored in competitor_snapshots. Identifies new pages, modified content, structural changes, and schema additions that could explain the SOV shift. Outputs a structured website_changes object that feeds into recommendation generation."
trigger: sov_shift_flagged
depends_on:
  - scout-sov-detection
feeds_into:
  - scout-recommendation-generation
runs_parallel_with:
  - scout-ai-response-analysis
  - scout-web-intelligence
input: website_diff_bundle (see Input Schema below)
output: website_changes object written to competitor_investigations + updated competitor_snapshots entry
api_used: Apify Cloud (scrape already completed upstream — this skill reads and interprets the output)
---

# Scout: Website Diff Analysis

## What this skill does

When a competitor's SOV shifts, the most direct explanation is usually
something they changed on their own domain. A new case study page gives
the AI a specific proof point to cite. A restructured service page that
now targets corporate keywords gives the AI a reason to classify them
as a specialist. FAQ schema markup added to an existing page makes their
content directly parseable by AI platforms for question-answer citation.

Your job is to read the Apify scrape output for the competitor's domain
this week and diff it against the previous snapshot stored in the Agent
DB. You identify what changed, assess whether those changes explain the
SOV shift, and produce a structured evidence object.

You are not crawling the site. The crawl has already happened upstream.
You are reading and interpreting the diff. The distinction matters —
your job is forensic analysis of what changed and what it signals, not
technical crawling.

---

## Input Schema

```json
{
  "competitor_name": "string",
  "competitor_domain": "string — e.g. 'acmemovers.com'",
  "cluster_id": "string",
  "cluster_label": "string — e.g. 'Corporate relocation Mumbai'",
  "cluster_queries": ["array of monitored queries for this cluster"],
  "vertical": "string",
  "shift_type": "gain | loss | displacement | new_entrant",
  "shift_magnitude": "float",
  "week_of_shift": "date — ISO format",
  "current_scrape": {
    "scrape_date": "date",
    "pages": [
      {
        "url": "string",
        "title": "string",
        "meta_description": "string | null",
        "h1": "string | null",
        "h2s": ["array of h2 strings"],
        "word_count": "int",
        "schema_types": ["array of schema.org types present e.g. 'FAQPage', 'LocalBusiness'"],
        "internal_links_to": ["array of URLs this page links to"],
        "first_crawled": "date — when Apify first saw this URL"
      }
    ],
    "total_pages_crawled": "int"
  },
  "previous_snapshot": {
    "snapshot_date": "date",
    "pages": [
      {
        "url": "string",
        "title": "string",
        "meta_description": "string | null",
        "h1": "string | null",
        "h2s": ["array of h2 strings"],
        "word_count": "int",
        "schema_types": ["array"],
        "first_crawled": "date"
      }
    ],
    "total_pages_crawled": "int"
  }
}
```

---

## What you produce

```json
{
  "competitor_name": "string",
  "cluster_id": "string",
  "snapshot_delta": {
    "pages_added": "int",
    "pages_removed": "int",
    "pages_modified": "int",
    "schema_types_added": ["array of schema types that are new across the domain"],
    "total_pages_current": "int",
    "total_pages_previous": "int"
  },
  "new_pages": [
    {
      "url": "string",
      "title": "string",
      "content_type": "case_study | service_page | faq_page | comparison_page | blog_post | location_page | about_or_credential | other",
      "cluster_relevance": "direct | indirect | none",
      "cluster_relevance_reason": "string — why this page does or does not relate to the cluster",
      "schema_present": ["array of schema types"],
      "signals": ["array of specific signals — e.g. 'targets corporate relocation keywords', 'includes client outcome data', 'FAQ schema covering cluster queries']"
    }
  ],
  "modified_pages": [
    {
      "url": "string",
      "title": "string",
      "change_type": "content_expanded | title_changed | schema_added | h2s_restructured | word_count_significant | meta_description_changed | multiple",
      "change_detail": "string — what specifically changed",
      "cluster_relevance": "direct | indirect | none",
      "cluster_relevance_reason": "string"
    }
  ],
  "schema_changes": {
    "added": [
      {
        "schema_type": "string",
        "on_pages": ["array of URLs where this schema now appears"],
        "cluster_relevance": "string — why this schema type matters for the cluster"
      }
    ],
    "removed": [
      {
        "schema_type": "string",
        "previously_on": ["array of URLs"]
      }
    ]
  },
  "structural_observations": [
    "array of strings — broader site structure signals not captured by individual pages"
  ],
  "inferred_trigger": "string | null — the website change most likely to have caused the SOV shift",
  "confidence": "high | medium | low | none",
  "summary": "string — 2-3 sentences synthesising what changed and what it implies for the SOV shift"
}
```

---

## Reasoning instructions

### Step 1 — Run the page-level diff

Compare the current_scrape pages array against the previous_snapshot
pages array. Match pages by URL.

Classify each URL into one of three categories:

**New page** — URL exists in current_scrape but not in previous_snapshot.
Check first_crawled date to confirm. If first_crawled is within 45 days
of the week_of_shift, treat as new. If first_crawled predates that
window significantly, it may have been missed by the previous crawl —
note this but still treat as new for the purposes of this investigation.

**Modified page** — URL exists in both snapshots. A page is modified if
any of the following changed: title, h1, meta_description, h2s array
(added or removed headings), word_count increased by more than 15%,
or schema_types array changed.

A word count change under 15% is minor editing — do not flag it as a
meaningful modification unless accompanied by a title, h1, or schema
change. A word count increase over 40% in a single week typically means
a page was substantially rewritten or a significant new section was
added.

**Removed page** — URL exists in previous_snapshot but not in
current_scrape. Note removed pages but do not weight them heavily as a
cause of a SOV gain. Removed pages are more relevant for investigating
a SOV loss.

---

### Step 2 — Classify new pages by content type

For each new page, determine its content type based on URL pattern,
title, h1, and h2 structure:

| Signal | Content type |
|---|---|
| URL contains /case-study/, /success-story/, /client-story/ or title contains client name + outcome | case_study |
| URL contains /services/, /solutions/, title describes a specific service offering | service_page |
| URL contains /faq/, title contains "questions" or "FAQ", h2s are phrased as questions | faq_page |
| URL contains /compare/, /vs/, /alternatives/, title positions competitor against others | comparison_page |
| URL contains /blog/, /insights/, /resources/, title is topical or educational | blog_post |
| URL contains city name, suburb, or route (e.g. /mumbai-to-pune/) | location_page |
| URL contains /about/, /awards/, /certifications/, /team/, title mentions credential or recognition | about_or_credential |
| Does not fit above patterns | other |

---

### Step 3 — Assess cluster relevance for each changed page

For every new or modified page, assess whether it is directly relevant
to the cluster that triggered this investigation, indirectly relevant,
or not relevant.

**Direct relevance** — the page targets one or more of the cluster's
monitored queries. Check the title, h1, h2s, and meta_description
against cluster_queries. If 2 or more of the cluster's queries would
plausibly match this page's content, it is directly relevant.

**Indirect relevance** — the page is in the same vertical but targets
adjacent queries. It does not directly match the cluster but could
contribute to the competitor's overall domain authority on this topic.
A blog post about "tips for moving offices" is indirectly relevant to a
"corporate relocation" cluster.

**None** — the page clearly targets a different vertical or topic
entirely. A new page about residential moving on a competitor being
investigated for a corporate relocation cluster shift is not relevant.

Write cluster_relevance_reason as a specific sentence, not a category
label. "This page's h1 is 'Corporate Fleet Relocation Services in Mumbai'
which directly targets 3 of the 6 monitored queries in this cluster" is
useful. "Relevant to cluster" is not.

---

### Step 4 — Analyse schema changes

Schema markup is one of the highest-signal changes you can detect.
AI platforms parse structured data directly and use it to extract
specific facts, Q&A pairs, and service attributes for citation.

For each schema type added across the domain, assess its significance
for the cluster:

| Schema type | Significance |
|---|---|
| FAQPage | High — AI platforms extract Q&A pairs directly. If the FAQ questions match cluster queries, this is a direct citation trigger. |
| LocalBusiness or any subtype | Medium-High — reinforces geographic and service category signals. A new subtype (e.g. MovingCompany, LegalService) is more significant than a generic LocalBusiness addition. |
| Review or AggregateRating | Medium — makes review data machine-readable. Often added alongside a review burst. |
| BreadcrumbList | Low — structural, unlikely to affect SOV directly |
| Article or BlogPosting | Low-Medium — depends on content |
| Service | Medium — explicitly marks a page as a service offering with structured attributes |
| HowTo | Medium — parseable procedural content that AI platforms cite for instructional queries |
| Person or ProfilePage | Low unless the vertical is one where individual credentials matter (legal, healthcare) |

For FAQPage schema specifically: if you can see the h2s on the page
where FAQPage was added, check whether those h2s match or closely
paraphrase any of the cluster's monitored queries. A direct match is a
high-confidence explanation for the SOV shift even without seeing the
full page content.

---

### Step 5 — Identify structural observations

Beyond individual page changes, look for patterns across the diff that
signal a broader content strategy shift:

**Cluster of new pages in one topic area** — if 3 or more new pages all
target the same topic (e.g. three new corporate relocation pages — a
service page, an FAQ page, and a case study), this is a deliberate
content cluster build, not a single page addition. Note it explicitly.

**Significant word count expansion across multiple existing pages** —
if 4+ existing pages all increased word count by 30%+ in the same week,
the competitor likely did a site-wide content refresh. This is a
different signal than adding a single new page.

**Internal linking pattern change** — if a newly added page has a high
number of internal_links_to pointing to cluster-relevant pages, the
competitor is building topical authority signals through internal link
structure. This is an advanced SEO/AEO signal worth noting.

**Schema rollout across multiple pages simultaneously** — if the same
schema type appears on 5+ pages for the first time in a single scrape
cycle, the competitor ran a technical update, not a one-off content
addition. Technical schema rollouts often correlate with SOV shifts
across multiple clusters simultaneously.

**Total page count jump** — if total_pages_current is significantly
higher than total_pages_previous (e.g. 40+ new pages), the competitor
may have launched an entirely new content section. Note the magnitude
and what the new pages are broadly about.

---

### Step 6 — Determine inferred trigger and confidence

Look at all new pages, modified pages, and schema changes assessed as
directly or indirectly relevant to the cluster. Identify the single
change most likely to have caused the SOV shift.

Priority order for inferred_trigger:

1. A new page directly targeting the cluster's queries with schema markup
   present — highest signal, most direct cause
2. FAQPage schema added to an existing page with h2s matching cluster
   queries — direct cause, no new page needed
3. A new case study page with client outcome data in the same vertical —
   strong credential signal
4. A new service page targeting the cluster without schema — moderate
   signal, may take longer to propagate to SOV
5. Multiple indirect changes with no single direct hit — diffuse signal,
   set as medium or low confidence
6. No relevant changes found — inferred_trigger is null

Set confidence:
- **high** — one change clearly maps to the cluster's queries and the
  timing aligns with the shift window
- **medium** — relevant changes found but timing is unclear or the
  relevance is indirect
- **low** — minor changes found, plausible connection but not conclusive
- **none** — no relevant changes found in either new or modified pages

---

### Step 7 — Write the summary

2-3 sentences. Must answer:
1. What the most significant website change was (specific page URL or
   type, not a category label)
2. Why that change is relevant to this cluster
3. What is absent — if no relevant changes were found on the website,
   say so directly so recommendation generation knows to weight
   third-party signals higher

Example of a good summary:
> "Competitor added a new service page at /corporate-fleet-relocation/
> with FAQPage schema — the FAQ questions directly match 4 of the 6
> monitored queries in this cluster including 'how long does corporate
> fleet relocation take' and 'what is included in corporate relocation
> services'. No other cluster-relevant changes were found. This page
> addition with FAQ schema is the most probable direct cause of the
> SOV shift."

Example of a bad summary:
> "The competitor updated their website with some new pages and changed
> some content. This may have contributed to their improved performance."

---

## Handling scrape quality issues

Apify scrapes are not always complete. Pages behind login walls,
JavaScript-heavy SPAs, or pages with aggressive bot detection may be
missing or incomplete in the scrape output. Apply these rules when
scrape quality is degraded:

**Missing page body content** — if a page URL is present but h1, h2s,
and word_count are all null or zero, the scrape failed to extract
content for that page. Flag the URL as "scrape incomplete — content
unavailable" in the new_pages or modified_pages output. Do not infer
content from the URL alone beyond assigning a content_type.

**Inconsistent page counts** — if total_pages_crawled dropped
significantly from the previous snapshot (e.g. from 180 to 95 pages),
the scrape may have been blocked or rate-limited partway through. Note
this as a scrape quality issue in structural_observations and reduce
overall confidence by one level. A drop in crawled pages does not mean
the competitor removed those pages.

**first_crawled date unavailable** — if the first_crawled field is null
for new pages, you cannot confirm when they were added. Treat the page
as new but note the missing date and mark confidence as medium at most
for any findings based on that page.

---

## Edge cases

**Competitor SOV loss (shift_type: loss)**
Run the same analysis but weight removed pages and content degradation
signals more heavily. A page that was previously driving cluster
relevance being removed or substantially shortened is a probable cause
of a loss. Look for: pages removed, word count significantly decreased,
schema types removed.

**New entrant (shift_type: new_entrant)**
No previous_snapshot exists. Skip the diff entirely. Instead, run a
full classification pass on all pages in current_scrape — build the
baseline profile. Note which pages are most relevant to the cluster
and which schema types are present. Summary should describe the
competitor's content posture on this cluster as of first observation.

**Competitor domain redirects or rebrands**
If the domain in current_scrape does not match competitor_domain in
the input, flag this immediately in structural_observations. Do not
proceed with the diff — the scrape may be misattributed. Return an
output with confidence: none and a note requesting investigation of
the domain change.

**Large site with 500+ pages**
Do not attempt to classify every page. Focus analysis exclusively on
pages that are new, modified, or have schema_types present. Structural
observations should note that the site is large and the scrape may be
sampling rather than exhaustive.

---

## What you do not do

- Do not crawl or fetch any URLs — the Apify scrape is already done,
  you read and interpret the output only
- Do not query Perplexity or any external API — that is scout-web-intelligence
- Do not analyse AI platform responses — that is scout-ai-response-analysis
- Do not access the Agent DB directly — all data is passed in as input
- Do not infer page content beyond what title, h1, h2s, and schema
  types reveal — you do not have the full page body
- Do not flag minor word count changes (under 15%) as significant
  modifications
- Do not produce action bullets — that is recommendation generation's job
- Do not report on pages with no relevance to the cluster — keep the
  output focused on what matters for this specific investigation
