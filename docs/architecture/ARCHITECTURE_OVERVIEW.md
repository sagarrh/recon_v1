# AI Visibility Signal Generator — Technical Overview

This document explains what the system does, how the pieces fit together, and
what happens step by step when you run it. It assumes you can read Python and
SQL, but it does not assume you have seen this codebase before.

---

## 1. What the product actually does

You give it one thing: a company name.

```bash
uv run ai-visibility report generate --company "Aprio"
```

You get back a **Company Intelligence Report** — a large JSON document plus a
readable Markdown version — that answers questions like:

- How often does this company get mentioned in AI assistant answers, and is that
  going up or down over time?
- Which AI providers (OpenAI, Gemini, Perplexity, Google AI Overview) mention it
  most, and which ignore it?
- Which specific monitored questions is it winning or losing on?
- Which competitors moved, and in which direction?
- Which cited web pages are associated with those changes — and how confident
  can we honestly be that any of them *caused* the change?

The raw material for all of this already exists in a PostgreSQL table called
`public.ai_monitoring`. Something upstream (not part of this repo) periodically
asks AI assistants a set of questions and stores the answers, the URLs those
answers cited, and its own tally of which companies were mentioned.

This project's job is to turn that pile of raw runs into a defensible analysis.

### The core stance

A lot of the design only makes sense once you understand the project's central
worry: **it is very easy to produce a confident-sounding report that is wrong.**
So the code is built around a few rules that it enforces mechanically rather
than by convention:

| Rule | How the code enforces it |
|---|---|
| The raw table is evidence, never touched | Every read runs `set transaction read only`; migrations are scanned for any statement that would mutate `ai_monitoring` and refuse to apply |
| Don't trust the upstream numbers blindly | The upstream company counts are stored *and* independently recomputed from the answer text; disagreements are flagged, not silently resolved |
| Citations are not coverage | "This URL appeared 87 times" and "this URL appeared in 21 distinct answers" are tracked as separate numbers and never conflated |
| Co-occurrence is not causation | A page is only called a likely driver if there is a stored historical snapshot showing the page changed *and* the change lines up in time with the visibility change |
| Missing data is stated, not hidden | Every gap becomes an explicit `data_quality_flag` or warning in the output |
| No LLM does the arithmetic | Everything numeric is deterministic Python. `OPENAI_API_KEY` exists in config but is genuinely unused |

That last one is worth repeating: despite the name, there is no AI model in this
pipeline. It analyses AI output; it doesn't call an AI.

---

## 2. The shape of the system

It is a single Python 3.12 CLI application talking directly to PostgreSQL. No
web server, no frontend, no queue broker, no background daemon.

```
                    ┌─────────────────────────────────┐
                    │   public.ai_monitoring          │
                    │   (read-only source of truth)   │
                    └──────────────┬──────────────────┘
                                   │ read
                                   ▼
   ┌────────────────────────────────────────────────────────────┐
   │                    ai-visibility CLI                       │
   │                                                            │
   │   resolve → normalize → analyse → attribute → report       │
   │                                    │                       │
   │                                    ├─ queues page fetches  │
   │                                    │                       │
   └────────────────────────────────────┼───────────────────────┘
                    │ write             │ fetch
                    ▼                   ▼
   ┌────────────────────────────┐   ┌──────────────────────┐
   │  ai_visibility_* tables    │   │  the public internet │
   │  (derived, append-only)    │   │  (cited web pages)   │
   └────────────────────────────┘   └──────────────────────┘
                    │
                    ▼
        output/<company>/company-intelligence-report.{json,md}
```

Everything the project creates is namespaced `ai_visibility_*`, so it can live
inside someone else's database without colliding with anything.

---

## 3. End to end: what `report generate` actually does

This is the main path, in `src/ai_visibility/pipeline.py`. Ten steps.

### Step 1 — Check the database before writing anything

`database/validation.py` connects, confirms `public.ai_monitoring` exists, and
confirms it has all eleven expected columns with JSON-compatible types. It also
grabs a row-count *estimate* from `pg_class` rather than doing a real `count(*)`,
because the source table can be large and an exact count would be slow.

This runs first on purpose: if the source is wrong, the run stops before a single
byte is written anywhere.

### Step 2 — Apply migrations

`database/migrations.py` reads the `.sql` files in `migrations/` in sorted order.
For each one it:

1. Flattens the SQL and checks it against a blocklist of statements that would
   touch `ai_monitoring` — `alter table`, `drop`, `truncate`, `delete from`,
   `update`, `insert into`. Any hit raises immediately.
2. Hashes the file (SHA-256) and looks it up in
   `ai_visibility_schema_migrations`. Already applied with the same hash? Skip.
   Already applied with a *different* hash? Error — someone edited history.
3. Otherwise runs it and records the version + checksum.

Migrations are append-only and idempotent. Re-running the whole command is safe.

### Step 3 — Resolve the company name to a client

`companies/resolver.py` has to answer: "when the user typed *Aprio*, which
`client_id` in the monitoring table do they mean?"

It tries two strategies in order:

**Registry lookup.** If previous runs already registered this company as a
client (in `ai_visibility_client_companies`), use that. Exactly one match wins.
More than one match is an error rather than a guess.

**Scanning the raw history.** Otherwise it searches `ai_monitoring` for the
company name — both as a key in the `companies_data` JSON and as literal text in
the answers. Then:

- Runs where the name appears as an explicit `companies_data` key are "exact"
  matches and count strongly.
- Runs where it only appears in answer prose are weaker evidence.
- If one client dominates the exact matches — at least 3 runs and at least 3×
  the runner-up — that client wins, and the choice is logged.
- Anything ambiguous raises an error rather than picking arbitrarily.

Name matching goes through an alias layer (`companies/aliases.py`) that knows
`EY`, `Ernst & Young`, and `Ernst and Young` are one company, and picks a single
canonical display name for each.

### Step 4 — Load the full history

`load_raw_runs` pulls **every** run for that client, ordered by creation time.
Not the last two — all of them. Analysing only the most recent pair is exactly
the failure mode this product exists to avoid.

### Step 5 — Normalize each run

`normalization/runs.py` is where messy JSON becomes clean typed data. For each
raw run it produces a `NormalizedRun`.

**Parsing defensively.** The JSON columns might be actual JSON, might be strings
containing JSON, might be null. `normalization/parsing.py` has three helpers
(`as_dict`, `as_list`, `number_or_none`) that always return the right type and
never raise. Bad data becomes empty data, not a crash.

**Identifying the query.** Two runs are comparable only if they asked the same
question the same way. The code builds a `monitor_query_key` by hashing:

```
client_id + normalized query text + service + method + configuration_hash
```

where `configuration_hash` covers the execution settings — model, model version,
prompt version, retrieval config, language, geography, generation settings. If
all seven are present the run is marked `complete`; if any are missing it's
`incomplete`, and later comparisons involving it get their confidence knocked
down from 1.0 to 0.75.

**Answers and citations.** `answers_list` and `citations_list` are parallel
arrays — answer 1's citations are at index 0, and so on. Each answer gets a
number, a SHA-256 hash of its text, and a word count. Each citation gets its URL
normalized (lowercased host, tracking parameters like `utm_*` and `fbclid`
stripped, trailing slash removed, remaining parameters kept in order), and gets
a `position_quality`:

| Quality | Meaning |
|---|---|
| `valid` | Real start/end character offsets into the answer |
| `zeroed` | Both offsets are 0 — the provider didn't really give us positions |
| `unavailable` | No usable offsets at all |

Two things are counted separately and deliberately never merged: how many times
a URL was *listed* (`raw_occurrence_count`) and how many distinct answers it
appeared in (answer coverage).

**Counting mentions honestly.** This is the heart of it. For every company, in
every answer, `utils/text.py::literal_mentions` finds alias matches with word
boundaries — so "Aprio" matches in "Aprio is listed" but *not* inside
"capriotic". Overlapping aliases are collapsed: if both "BDO USA" and "BDO" are
aliases, the text "BDO USA is listed" counts as **one** mention, not two. It does
this by preferring the longest match and discarding anything that overlaps it.

From that, per company per run:

```
literal_answer_count  = number of distinct answers containing the company
literal_visibility    = literal_answer_count / total answers
metric_difference     = literal_answer_count − upstream's own count
```

If `metric_difference` isn't zero, the run gets a `company_metric_mismatch`
flag. Both numbers survive into the report. The system does not decide who's
right; it shows you the disagreement.

**Validity.** A run with zero answers is marked invalid with reason
`zero_answers` and flagged `failed_monitoring_run`. It's still stored — you can
see it happened — but it's excluded from every baseline and trend calculation.

### Step 6 — Persist the normalized evidence

`database/repository.py::persist_normalized_run` writes everything into the
derived tables in one transaction per run: the monitor query, the run processing
record, companies and their aliases, client/competitor relationships, answers,
per-answer company mentions with context snippets, citation pages, and
per-run company metrics.

It's written to be re-runnable. Every insert has an `on conflict do update`, and
rows that no longer apply (answers that disappeared, stale metrics) are deleted
before the new ones go in. Running normalization twice produces the same state
as running it once.

### Step 7 — Load whatever page evidence exists

`load_page_intelligence` pulls the most recent snapshot for each cited page,
along with what that snapshot said about the client company and whether it
differs from the previous snapshot.

On a first run this comes back empty — no pages have been fetched yet. That's
fine and expected; the report just says so.

### Step 8 — Build the report

`reports/builder.py`. This is the biggest single piece of logic. Section 4 below
breaks it down properly.

### Step 9 — Queue page fetches (failure is survivable)

`jobs/queue.py::enqueue_report_pages` looks at which URLs moved in the material
comparisons and queues up to 25 of them for fetching, prioritised:

| Priority | Case |
|---|---|
| 100 | A newly-appearing page on a domain the client owns |
| 80 | Any other newly-appearing page |
| 50–80 | Existing pages, scaled by how much their coverage shifted |

Pages that *disappeared* are skipped — there's nothing useful to fetch.

Crucially, this whole step is wrapped in a `try`. If the queue is unreachable,
the report still completes; it just adds a `page_queue_unavailable` flag and an
explanatory note. Page enrichment is an enhancement, not a dependency.

### Step 10 — Validate, persist, write

The report dictionary is checked against
`schemas/company_intelligence_report.schema.json` (JSON Schema draft 2020-12).
Then `reports/persistence.py`:

- Computes an idempotency key from client + company + analysis period + version,
  and upserts the report on it — regenerating overwrites rather than duplicating.
- Marks all previously active signals for this company `inactive`, then writes
  the current ones as `active`. Old signals stay for history but don't pollute
  the present.
- Writes each signal's comparison into `ai_visibility_run_comparisons`.
- Writes both output files atomically — to `.tmp` first, then `replace()` — so
  a crash mid-write can never leave a half-written report on disk.

---

## 4. Inside the report builder

The builder takes normalized runs and produces the final structure. Here's what
each analysis actually computes.

### Grouping into histories

`partition_valid_runs` buckets valid runs by `monitor_query_key` and sorts each
bucket chronologically. Every timeline calculation works within these buckets,
which is what keeps providers and configurations from being compared against
each other by accident.

### Trends

`trend_status` classifies a series of visibility values:

| Result | Condition |
|---|---|
| `no_data` / `insufficient_history` | 0 or 1 observations |
| `one-time spike` | Latest is more than 0.1 above every prior value |
| `one-time drop` | Latest is more than 0.1 below every prior value |
| `volatile` | Standard deviation ≥ 0.2 but net change < 0.1 |
| `increasing` / `decreasing` | Net change of ±0.1 or more |
| `stable` | Everything else |

`rolling_baseline` gives context for the latest point: mean, median, min, max,
standard deviation over the previous five observations, plus a z-score (only
when there are at least 3 prior points and non-trivial variance — otherwise it
returns `None` rather than a meaningless number).

### The visibility timeline

`aggregate_visibility_timeline` handles a subtle problem. Different monitored
queries run at different times, so you can't just average whatever happened
today. Instead it walks every run in time order and maintains a running picture
of the **latest known state of every query**. Each event emits a point using the
current state of all queries seen so far. So when one query is re-run, only that
query's contribution updates, and the others hold their last known value.

### Change points

`analysis/change_points.py` uses two methods together:

1. **Adjacent deltas** — any step of 0.2 or more between consecutive runs.
2. **PELT changepoint detection** (via the `ruptures` library) — only when there
   are at least 8 observations and actual variance, to find structural shifts
   the simple threshold misses.

Results are merged and de-duplicated, each labelled with which method found it.

### Citation analysis

`analysis/citations.py` computes, per URL per run:

```
raw_occurrences     total times the URL was listed
answer_coverage     distinct answers it appeared in
company_cooccurrence  answers where it appeared AND the company was mentioned
cooccurrence_rate   company_cooccurrence / answer_coverage
company_base_rate   how often the company appears in answers generally
association_lift    cooccurrence_rate − company_base_rate
```

**Association lift is the interesting number.** If a URL co-occurs with the
company at exactly the background rate, lift is 0 — the URL tells you nothing.
Positive lift means the URL shows up disproportionately in answers that mention
the company. It's still only an association, and the code is careful to keep
calling it that.

Comparing two runs gives each URL a status: `new`, `removed`, `increased`,
`decreased`, or `persisted`.

### Competitor analysis

`analysis/competitors.py` compares every non-client company between two runs. It
reports both the upstream numbers and the independently recomputed literal
numbers side by side, and marks which basis it used for the headline figure
(`upstream_preserved` when both runs have upstream data, `literal_recomputed`
otherwise). Companies that didn't move at all are dropped.

### Recommendation-pattern detection

`analysis/recommendation_patterns.py` catches something specific: when an AI
provider starts answering with the same *list of firms* over and over.

1. Find answers mentioning 3 or more companies.
2. Cluster them by Jaccard similarity ≥ 0.65 — answers recommending roughly the
   same set.
3. The bundle must appear in at least 40% of answers.
4. Build a representative set from companies present in ≥ 75% of the cluster.
5. Compare against the previous run. The frequency must have jumped by at least
   0.25 to count.

This matters because it's a genuinely different explanation for a visibility
change: the company didn't do anything, the provider just started reciting a
list that happens to include them.

### Which comparisons become signals

`material_comparisons` filters to changes worth reporting: visibility moved by
0.1 or more, **or** the answer count moved by 2 or more. Only these get the full
signal treatment.

### Attribution scoring

For each material comparison, the builder takes the 15 biggest URL movements and
scores each one with `attribution/scoring.py`:

| Points | Signal |
|---:|---|
| +20 | Page actually mentions the company |
| +20 | A historical diff shows the page strengthened its mention |
| +10 each | Company in title/heading · links to official domain · published by the company · URL new or materially expanded · positive association lift · semantic alignment · repeated evidence |
| −25 | Page verifiably does **not** mention the company |
| −20 | Page is cited in ≥80% of answers (too ubiquitous to explain anything) |
| −15 each | No historical snapshot available · execution config incomplete |
| −10 each | Citation positions unavailable · only one answer of evidence · metric mismatch |

Clamped to 0–100. Above 70 is `high`, above 45 is `medium`, otherwise `low`.
The report keeps **every component** that fired, so any score can be traced back
to its reasoning.

The negative weights are the point. A page with no historical snapshot cannot
reach high confidence, no matter how good it looks otherwise.

### Picking a hypothesis

`attribution/hypotheses.py` walks a strict priority ladder:

1. **`direct_page_content_change`** — requires a stored historical diff showing
   the page added or strengthened its mention of the company, *and* the snapshot
   timestamps must bracket the run timestamps. Only this path can reach `high`.
2. **`recommendation_pattern_shift`** — the provider changed its recommendation
   bundle. Confidence `medium`.
3. **`broader_source_portfolio_shift`** — new sources appeared alongside the
   change. Confidence `low`, explicitly stated as not causal.
4. **`insufficient_evidence`** — the honest default.

Every signal, regardless of hypothesis, also carries a fixed list of alternative
explanations (provider model changed, retrieval behaviour changed, config
drifted, competitor bundles changed) so no reader walks away with a single story.

---

## 5. The page intelligence subsystem

This is the part that goes out to the internet, and it's the part with the most
security surface. It runs separately from report generation:

```bash
uv run ai-visibility pages process --limit 20
```

### Job lifecycle

Jobs live in `ai_visibility_page_fetch_jobs` and move through:

```
pending ──claim──> processing ──success──> complete
   ▲                    │
   │                    └──failure──> retry ──(3 attempts)──> failed
   └────────────────── jobs retry ──────────────────────────────┘
```

Claiming uses `for update skip locked` with a CTE, so multiple workers can run
concurrently without stepping on each other. Each claim also sweeps up jobs
whose lease expired — a worker that crashed mid-fetch leaves a job stuck in
`processing`, and after `PAGE_JOB_LEASE_SECONDS` (default 15 minutes) it gets
released back to the queue. Retries back off exponentially, capped at an hour.

### Fetching safely

`scraping/fetcher.py` and `scraping/security.py` are built against SSRF — the
attack where a URL in your data tricks your server into requesting something on
your internal network. The defence has several layers:

1. **Scheme and credential checks.** HTTP/HTTPS only; URLs with embedded
   username/password are rejected.
2. **Hostname blocklist.** `localhost`, `metadata`, `metadata.google.internal`,
   `instance-data`, and anything ending `.internal`.
3. **DNS resolution and IP inspection.** Every resolved address is checked, and
   the request is refused if *any* of them is private, loopback, link-local,
   multicast, reserved, or unspecified.
4. **Connection pinning** (`scraping/transport.py`). This is the clever bit. A
   custom `httpcore` backend forces the TCP connection to the exact IP that was
   validated. Without this, an attacker could return a public IP to the
   validation lookup and a private one to the actual connection — a DNS
   rebinding attack. The backend also refuses to connect if the hostname it's
   handed doesn't match the one that was validated.
5. **Manual redirect handling.** `follow_redirects=False`, and the loop
   re-validates every hop from scratch. A redirect to `169.254.169.254` gets
   caught on the next iteration. Conditional caching headers are sent only on
   the first hop, so they can't leak across a redirect to a different host.
6. **Byte limit.** Streamed and aborted the moment it exceeds
   `PAGE_FETCH_MAX_BYTES`.
7. **`trust_env=False`.** Environment proxy settings can't redirect traffic.
8. **robots.txt.** Checked and honoured, with a short bounded cache per origin
   to avoid downloading the same policy for every page in a batch.

There is also bounded concurrency across domains and per-domain throttling, so
processing a batch is faster without hammering one host.

### Extracting content

HTML goes through `trafilatura` for main-content extraction, with BeautifulSoup
as fallback. Script, style, noscript, iframe, and object elements are stripped
first. JSON-LD structured data is pulled out separately. PDFs go through `pypdf`.

There's also an **opt-in** Playwright fallback (`scraping/browser.py`) for
JavaScript-heavy pages. It is attempted only when static extraction is low
quality and the setting is enabled. It isn't installed by default; an unavailable
or failed browser attempt is recorded while the safe static result survives.
When it does run, it pins Chrome's DNS resolver to a validated IP, aborts any
request that leaves the pinned hostname, and re-validates the final URL after
navigation.

### Turning pages into evidence

Once fetched, `scraping/snapshots.py` analyses the page for the client company:
mention count, whether it appears in the title or headings, whether the page
links to a domain the company owns, and whether the company *published* the page.

Then it compares against the previous snapshot using `difflib.SequenceMatcher`.
Similarity below 0.9 is a material change. Critically, it also computes
per-company mention deltas between the two snapshots — that's the
`company_mention_changes` data that later lets the attribution engine say "this
page added three mentions of Aprio between these two dates."

**This is the only way a page can ever be called a likely driver.** No snapshot
history, no causal claim. That constraint is the whole reason this subsystem
exists.

---

## 6. The data model

### The source (never written to)

`public.ai_monitoring` — one row per monitoring run:

| Column | Contents |
|---|---|
| `id`, `client_id` | Identity |
| `cluster_id`, `cluster_name` | Topic grouping for queries |
| `request_payload` | What was asked and how (query, service, method, model config) |
| `answers_list` | The AI's answers |
| `citations_list` | Citations, parallel to `answers_list` |
| `citations_data`, `companies_data` | Upstream's own aggregations |
| `created_at` | When the run happened |

### The derived tables

Eighteen tables, all prefixed `ai_visibility_`. Roughly grouped:

**Identity** — `companies`, `company_aliases`, `client_companies` (who is the
client, who's a competitor, who's just tracked).

**Normalized evidence** — `monitor_queries`, `run_processing`, `answers`,
`answer_citations`, `answer_company_mentions`, `run_company_metrics`.

**Page intelligence** — `citation_pages`, `page_fetch_jobs`, `page_snapshots`,
`page_company_mentions`, `page_snapshot_diffs`.

**Output** — `run_comparisons`, `signals`, `reports`.

**Bookkeeping** — `schema_migrations`.

### Multi-tenancy

`migrations/0002_tenant_rls.sql` enables row-level security on every analytical
table with `select` policies that resolve the current client from either a
Supabase JWT claim (for `anon`/`authenticated` sessions) or a
`app.client_id` session setting (for trusted backend sessions). Helper functions
handle the indirect cases — reaching an answer's client through its run, a
page's client through its citations.

One caveat worth knowing: RLS is not `FORCE`d, so the role that owns the tables
bypasses policies entirely, and there are no explicit `grant` statements. If you
depend on this isolation, verify it against a real non-owner role.

---

## 7. Configuration and operation

### Setup

```powershell
uv sync
Copy-Item .env.example .env
# set DATABASE_URL to the direct Postgres connection string
```

Settings (`config/settings.py`) come from environment variables or `.env`, via
pydantic-settings with validated bounds. `DATABASE_URL` is deliberately optional
at import time so `--help` and offline tests work without a database; commands
that need it call `require_database_url()` and fail with a clear message.

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | — | Direct Postgres connection (required for DB commands) |
| `REPORT_OUTPUT_DIR` | `./output` | Where reports are written |
| `PAGE_FETCH_TIMEOUT_SECONDS` | 20 | Per-request timeout |
| `PAGE_FETCH_MAX_BYTES` | 10,000,000 | Response size cap |
| `PAGE_FETCH_MAX_REDIRECTS` | 5 | Redirect chain limit |
| `PAGE_FETCH_PER_DOMAIN_DELAY_SECONDS` | 0.5 | Politeness throttle |
| `PAGE_FETCH_ROBOTS_CACHE_SECONDS` | 1,800 | Per-origin robots policy cache |
| `PAGE_FETCH_MIN_TEXT_CHARACTERS` | 200 | Minimum useful static extraction size |
| `PAGE_FETCH_MAX_EXTRACTED_CHARACTERS` | 2,000,000 | Stored extracted-text cap |
| `PAGE_FETCH_MAX_PDF_PAGES` | 200 | PDF parsing page cap |
| `PAGE_FETCH_BROWSER_FALLBACK_ENABLED` | false | Opt into secure JS rendering fallback |
| `PAGE_FETCH_MAX_WORKERS` | 4 | Concurrent page workers across domains |
| `PAGE_JOB_LEASE_SECONDS` | 900 | How long before a stuck job is reclaimed |
| `DATABASE_STATEMENT_TIMEOUT_SECONDS` | 120 | Guard against runaway queries |

### The commands

```bash
# Database
ai-visibility db check              # verify connection and source table shape
ai-visibility db migrate            # apply append-only migrations

# Normalization
ai-visibility runs normalize --run-id <uuid> [--company "Name"]
ai-visibility runs backfill [--company "Name"]
ai-visibility runs status

# Reports
ai-visibility report generate --company "Aprio"
ai-visibility report show --company "Aprio" --format markdown

# Page enrichment
ai-visibility pages fetch --url "https://example.com/page"   # one-off, no writes
ai-visibility pages process --limit 20
ai-visibility jobs failed
ai-visibility jobs retry
```

Built with Typer, grouped into sub-apps. Every command routes through a `_run`
wrapper that catches expected failures and turns them into a clean
`Error: <message>` on stderr with exit code 2, rather than a traceback.

Note on `runs backfill`: without `--company`, it deliberately refuses to guess
which of the tracked companies is the client. It normalizes everything but
leaves relationships unregistered. Guessing wrong there would poison the
client/competitor labels for every downstream report.

### The typical sequence

The first report has no page evidence. Getting the full picture takes two passes:

```bash
ai-visibility report generate --company "Aprio"   # queues pages, reports without them
ai-visibility pages process                        # fetches and snapshots them
ai-visibility report generate --company "Aprio"   # now includes page verification
```

And it only gets better over time — page *diffs* need at least two snapshots, so
causal attribution becomes possible only after the same page has been fetched
across two different report cycles.

### Reading the output

```
output/aprio/company-intelligence-report.json    # complete, machine-readable
output/aprio/company-intelligence-report.md      # human summary
```

`report show` prefers the database copy when it can reach it, and falls back to
the local file otherwise — so you can read a report without a database.

---

## 8. How it behaves when things go wrong

The degradation behaviour is worth understanding, because it's deliberate:

| Failure | What happens |
|---|---|
| Source table missing or wrong shape | Stops before writing anything, names the exact missing column |
| No `DATABASE_URL` | Clear message pointing at `.env.example`; nothing attempted |
| Company name ambiguous | Error listing how many clients matched — never a guess |
| A run has zero answers | Stored as invalid, excluded from baselines, flagged |
| Upstream counts disagree with recomputed ones | Both reported, `company_metric_mismatch` flag raised |
| Page fetch fails | Job retries with backoff, then lands in `failed` for inspection |
| Page queue entirely unavailable | Report still generates, adds `page_queue_unavailable` |
| No page snapshots exist | Report generates; attribution capped at `low`; limitation stated explicitly |
| No historical page diffs | Nothing is ever called a direct driver |
| Migration file was edited after being applied | Checksum mismatch, refuses to proceed |

The pattern: **fail loudly on anything that would corrupt evidence, degrade
gracefully on anything that only reduces confidence.**

---

## 9. Quality gates

```bash
uv run pytest              # 24 tests, ≥70% coverage enforced
uv run ruff check .        # E, F, I, UP, B, SIM, RUF
uv run ruff format --check .
uv run mypy src            # strict mode
```

All four currently pass. Worth knowing about the coverage number, though: it's
71% overall, but that average hides real variation. The pure-logic modules —
normalization, the builder, the analysis code — are well covered. The database
layer is not (`repository.py` 15%, `queue.py` 17%, `persistence.py` 37%). In
practice, no SQL statement in the project is exercised by a test. If you're
extending this, that's the gap most worth closing first.

The test fixture in `tests/conftest.py` is a realistic Aprio scenario — 20 and
21 answers across two runs, with a recommendation bundle appearing partway
through — and the expected outputs in `fixtures/` are checked against it, so the
end-to-end analytical behaviour is genuinely pinned down.

---

## 10. A map of the source

```
src/ai_visibility/
├── cli/app.py              Typer commands, error handling, JSON output
├── pipeline.py             The 10-step report generation orchestration
│
├── config/
│   ├── settings.py         Environment config with validated bounds
│   └── logging.py          structlog → JSON lines
│
├── database/
│   ├── connection.py       Context-managed psycopg connections
│   ├── validation.py       Source table shape checks
│   ├── migrations.py       Checksummed, safety-scanned migration runner
│   └── repository.py       All reads and writes (the largest DB module)
│
├── companies/
│   ├── aliases.py          Known alias groups, canonical display names
│   └── resolver.py         Company name → client_id
│
├── normalization/
│   ├── models.py           Pydantic models for raw and normalized data
│   ├── parsing.py          Defensive JSON coercion
│   └── runs.py             Raw run → NormalizedRun (the core transform)
│
├── analysis/
│   ├── timelines.py        Histories, trends, rolling baselines
│   ├── change_points.py    Threshold + PELT changepoint detection
│   ├── citations.py        URL coverage, occurrences, association lift
│   ├── competitors.py      Competitor movement between runs
│   ├── recommendation_patterns.py   Repeated-bundle detection
│   └── comparisons.py      Assembles adjacent run pairs
│
├── attribution/
│   ├── scoring.py          Deterministic 0–100 page scoring
│   └── hypotheses.py       Evidence-ranked hypothesis selection
│
├── reports/
│   ├── builder.py          Assembles the full report
│   ├── models.py           Pydantic report contract
│   ├── validation.py       JSON Schema validation
│   ├── markdown.py         Jinja2 human-readable rendering
│   └── persistence.py      Idempotent DB writes + atomic file writes
│
├── scraping/
│   ├── security.py         SSRF validation
│   ├── transport.py        IP-pinned HTTP transport
│   ├── fetcher.py          Redirect-safe fetching with limits
│   ├── html.py             Content extraction
│   ├── pdf.py              PDF text extraction
│   ├── browser.py          Optional pinned Playwright fallback
│   └── snapshots.py        Page/company analysis and diffing
│
├── jobs/queue.py           Page fetch job lifecycle
└── utils/
    ├── text.py             Normalization, boundary-aware alias matching
    ├── urls.py             URL normalization, IP classification
    └── hashing.py          SHA-256 and stable JSON hashing
```

---

## 11. Where to look first

**Adding a new analysis?** Write it in `analysis/`, call it from
`reports/builder.py`, add the field to `reports/models.py`, and extend
`schemas/company_intelligence_report.schema.json`.

**Changing what counts as a mention?** `utils/text.py::literal_mentions`. Be
careful — it's the foundation every visibility number rests on.

**Adding company aliases?** `companies/aliases.py`. Two dictionaries.

**Changing attribution weights?** `attribution/scoring.py`. Every component is
in one function with its point value inline.

**Changing the report layout?** `reports/markdown.py` — a single Jinja2 template.

**Adding a table or column?** A new numbered file in `migrations/`. Never edit
an applied one; the checksum will reject it.

A companion review of known bugs and improvement opportunities is worth reading
alongside this document before making substantial changes.
