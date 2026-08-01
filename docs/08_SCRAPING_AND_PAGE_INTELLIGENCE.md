# Scraping and Page Intelligence

## Purpose

Citation data proves that an AI response used a URL. It does not prove that the page meaningfully mentions the client or competitor.

Page retrieval is required for attribution.

## Selective retrieval

Do not fetch every URL on every run.

Prioritize:

1. client-owned new or expanding URLs
2. competitor-owned new or expanding URLs
3. third-party URLs strongly associated with changed company answers
4. pages involved in high-severity signals
5. pages missing a recent snapshot

## Safe fetch requirements

- allow only HTTP/HTTPS
- block private, loopback, link-local, metadata, and internal IPs
- validate DNS and redirected destinations
- limit redirects
- enforce timeout and size limits
- apply per-domain throttling
- use a descriptive user agent
- respect robots and site policies
- use conditional requests
- never execute downloaded code
- sanitize stored/exposed content
- isolate browser fallback

## Extraction order

1. Standard HTTP fetch
2. HTML main-content extraction
3. PDF text extraction
4. Headless browser fallback only when necessary

Store:

- final URL
- canonical URL
- content type
- status
- title
- author
- publish date
- modified date
- main text
- content hash
- structured data
- fetch method
- errors
- HTTP cache validators

## Page/company analysis

For the client and configured competitors, calculate:

- literal mentions
- aliases
- title mentions
- heading mentions
- official-domain links
- publisher ownership
- mention contexts
- mention role
- sentiment
- prominence

Mention roles:

- recommended_provider
- compared_provider
- quoted_expert
- publisher_identity
- incidental_list
- negative_reference
- unrelated
- not_mentioned

## Page snapshots

Create a snapshot when:

- first fetched
- content hash changes
- material metadata changes
- a scheduled refresh requires evidence

## Page diff

Compare current snapshot with previous snapshot.

Detect:

- company added
- company removed
- competitor added
- competitor removed
- recommendation language added
- dedicated section added
- link to official domain added
- title/heading change
- material rewrite
- no meaningful change

## Historical limitation

If no historical snapshot exists, report:

```text
historical_page_version_unavailable
```

Do not infer that the current page content existed historically.

## PDF handling

PDF citations are valid sources.

Extract:

- text
- metadata
- company mentions
- page numbers when available

Keep PDF evidence separate from HTML evidence.
