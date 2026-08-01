# Test Plan and Acceptance Criteria

## Unit tests

### JSON normalization

- valid arrays
- null arrays
- malformed shapes
- JSON-to-JSONB casts
- object key counting without `jsonb_object_length`

### Company aliases

- Aprio exact match
- BDO and BDO USA canonicalized
- Redstone aliases canonicalized
- false-positive substring protection

### URL normalization

Must not merge distinct:

- YouTube videos
- CMS records with meaningful query parameters

May remove:

- fragments
- known safe tracking parameters

### Citation deduplication

Repeated same URL in one answer:

- one coverage unit
- raw count preserved

### Citation position quality

Zero/zero is `zeroed`, not `valid`.

### Visibility

Literal visibility uses distinct answers, not mention count.

## Integration tests

### Provider isolation

Same query in Gemini and OpenAI creates separate timelines.

### Failed run exclusion

Zero-answer run is invalid and not a baseline.

### Baseline selection

Nearest earlier valid comparable run selected.

### Full-history report

Report includes all valid runs and not only latest pair.

### Metric mismatch

Aprio fixture:

- literal 14
- upstream 10
- mismatch 4
- warning emitted

### Ubiquitous source

Aprio main article:

- current coverage 21
- current co-occurrence 14
- company base rate 14/21
- lift approximately zero

Expected:

- not direct driver
- supporting/topic-context

### Localized source

New Aprio article:

- coverage 1
- co-occurrence 1
- broad company gain 14

Expected:

- localized possible contribution
- low overall contribution

### Recommendation pattern

Similar six-company list repeats in answers 8–21.

Expected:

- recommendation_pattern_shift candidate
- medium confidence when combined with history

### Idempotency

Reprocessing same run does not duplicate:

- answers
- mentions
- citations
- comparisons
- snapshots
- signals
- reports

### RLS

Cross-client report access denied.

## Scraper tests

- redirect limit
- private IP blocked
- response too large
- timeout
- robots denied
- HTML extraction
- PDF extraction
- duplicate content hash
- failed fetch retry
- conditional request

## Report acceptance

Given only:

```json
{ "company_name": "Aprio" }
```

The report must:

- resolve the correct client
- analyze all valid history
- show provider/query-level trends
- report literal/upstream mismatch
- identify July 11 Gemini change point
- avoid single-page causal claim
- identify recommendation-pattern shift as likely primary explanation
- classify the new Aprio article as localized
- show competitors that gained/lost
- expose evidence and warnings
- provide concrete actions

## Quality gate

Before merging:

- migrations pass
- tests pass
- type checks pass
- linting passes
- report schema validates
- fixture report matches expected classification
- no unsupported “caused” language
