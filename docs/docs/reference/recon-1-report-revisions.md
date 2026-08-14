# Recon 1 — AI Visibility Weekly Report (JAGGAER)

## Revision List for Design & Development

Changes are grouped by type. Each item states the problem, the fix, and where it lives in the file. Items in **Section F** are open judgment calls — do not implement until decided.

---

## A. Framing & Structure

**A1. Resolve "Weekly" vs. the two-month analysis period.** The cover says "Weekly Report" but the period is 2 Jun – 2 Aug 2026\. Label this edition honestly as the first in the series and the baseline:

- Cover eyebrow → `AI Answer Visibility · Report № 001 — Baseline Edition`  
- Frame the period as the baseline window. Subsequent editions become truly weekly.

**A2. Position Recon 1 as the co-signed baseline.** Add one line to the cover or Executive View: *"This baseline is the reference point every future report measures against."* Report № 001 becomes the document both sides point back to at day 30/60/90.

**A3. Align the narrative spine: implication vs. Action 01\.** The Primary Implication card says "Close the 2.56-point gap first" (Source-to-Pay), but Action 01 is the Procure-to-Pay page. Pick one spine:

- Either make the S2P page Action 01, or  
- Rewrite the implication: "Compound the P2P win, then close the 2.56-point S2P gap."

**A4. Rename topic "Priority 1/2/3" labels.** Readers assume Priority 1 \= most important, but the story says the biggest opportunity is in Priority 3\. Rename eyebrows to `Topic 1/2/3` (or reorder the stack so the lead opportunity comes first).

**A5. Retitle Section IV.** "Material citation opportunities" contains only *gains*, not gaps. Retitle to **"Where visibility moved"** (or "Material movements"). Optionally split into two sub-blocks: *What moved* / *Where the openings are* (see A6).

**A6. Show the zero-visibility queries.** Action 05 references zero-visibility queries, but the report never displays them. Add a small strip (3+ rows) in Section IV: **"Where JAGGAER is absent"** — the queries where JAGGAER never appears. This is the most actionable dataset in the report and makes Action 05 concrete.

**A7. Add a "Shipped this period" slot (from Recon 2 onward).** The report never states what work was delivered. For № 001, the baseline framing (A2) covers it ("work begins against these numbers"). From № 002 onward, include a short strip listing work shipped — activity next to movement, without claiming causation (consistent with the Limitations stance).

**A8. Announce the cadence.** Footer or cover: `Next report: 9 August 2026.` Sets the drumbeat as a standing promise.

---

## B. Data Presentation & Integrity

**B1. Label the skipped ranks in the bar charts.** Rank lists jump 1,2,3,4 → 6 / → 8 / → 10 with no explanation — it reads as a rendering error. Add one caption per chart (or one for the section): *"Showing tracked competitors."*

**B2. Label whose number the movement panels show.** In Section IV, "33.33% → 80.95%" never says whose metric it is. If it's JAGGAER's inclusion rate on that query, label it: e.g., `JAGGAER inclusion rate — Previous / Current`.

**B3. Show the cause of the Perplexity gain.** Finding 1 claims JAGGAER moved 33% → 81%, but its source list shows only competitor movement — jaggaer.com doesn't appear. Add JAGGAER's own citation movement to that list (as Finding 2 does with its \+10 jaggaer.com line), or the panel and the list tell two different stories.

**B4. Add units to the deltas.** "+10 / \+7 / −11" have no unit. Label once in the sub-header, e.g., `Competitor movement (citation count)`.

**B5. Explain the run math in Method.** \~32 queries → 663 answers means each query is sampled \~21 times. Add one sentence: *"Each query is sampled repeatedly across the period; visibility is the share of sampled answers naming JAGGAER."* Repeated measurement is a strength — say so before the client asks.

**B6. Put the scale numbers on the cover.** Add to cover meta: `Queries monitored: 32 · AI answers analyzed: 663`. Signals the depth of work before the first chart.

**B7. Surface the "\#4 of 71" signal.** Procure-to-Pay has 71 competitors vs. 23–31 elsewhere. Ranking \#4 in the most fragmented topic is a stronger result than \#4 of 23 — add one clause noting it (exec view or the P2P topic card).

**B8. Spell out "SOV" on first use.** Topic metrics use "SOV" cold. First instance → "Share of voice (SOV)".

**B9. Align or footnote the query cohorts.** Perplexity monitors 12 queries vs. 10 on the other providers, making the provider comparison apples-to-oranges. Either align cohorts next cycle or add a note in Limitations.

---

## C. Copy

**C1. Cover tagline.** Current: "Visibility is present. The next move is authority." — clinical, passive. Replace with a stat-carrying line, e.g.: **"Named in half of AI's answers. The next move is authority."**

**C2. Executive bullet 3\.** Has a comma splice and is vague. Replace with e.g.: *"Two queries moved materially this period — proof visibility responds fast. The same playbook now goes to the weak queries."*

**C3. Eyebrow typo.** "AI answer Visibility Weekly Report " — lowercase "answer" \+ trailing space. Fix casing (also see A1 for the full retitle).

**C4. Client name casing.** Cover and footer say "Jaggaer"; body says "JAGGAER". The brand is all-caps — standardize to **JAGGAER** everywhere.

**C5. Browser title tag.** Current: `JAGGAER, Weekly Report`. This becomes the PDF filename and bookmark label. Change to: `JAGGAER · AI Visibility Report № 001 — Multiplier AI`.

**C6. Reframe the Limitations vocabulary.** Keep the section — but "Metric mismatch" and "Execution configuration incomplete" is internal-QA language. Rename the section **"Measurement notes"** and rewrite each item as methodological rigor, e.g.: *"Where two measurement methods differed, this report uses the independently verified count."* Same facts, stated as discipline rather than defects.

**C7. Differentiate or cut the per-topic "Recommended focus" blocks.** All three currently recommend the same thing (authority page \+ Organization/Service/FAQPage schema) — reads templated. Either write genuinely distinct guidance per topic or remove the blocks and let the action plan carry the how.

---

## D. Design & Layout

**D1. Add week-over-week delta slots (template prep).** Unused CSS classes `.tag.trend-down` / `.tag.trend-stable` already exist. Wire them up: each provider card and each stat-lockup cell gets a ▲/▼ chip vs. prior report. Empty/hidden for № 001; from № 002 the weekly edition becomes a data refresh, not a redesign.

**D2. Scope the equal-height hacks to desktop.** `min-height: 2.5em` on `.finding h3` and `min-height: 5em` on `.query` create awkward white gaps when the grid stacks to one column on mobile. Move both inside the ≥900px range (or drop them and use grid row alignment).

**D3. Expand the cover meta.** Currently two items. Add: `Prepared for JAGGAER · Prepared by Multiplier AI`, the report number, and the scale numbers from B6.

**D4. Carry the report number in the footer.** Footer → `JAGGAER · AI Visibility Report № 001 · Multiplier AI` plus the next-report date (A8).

**D5. Remove or repurpose dead CSS.** Unused: `.masthead .meta`, `.on-bone` / `.on-atlas` card variants, and the trend tags (repurposed in D1). Clean up whatever isn't wired in.

---

## E. Delivery & Engineering

**E1. File weight: 1.3 MB, almost entirely embedded fonts.** Full variable Inter (100–900) plus two General Sans styles are inlined as base64. Options:

- Subset the fonts to used weights/glyphs (\~80% size reduction), or  
- Preferred: host the report (see E2) and load fonts normally.

**E2. Host instead of attach.** Serve reports at a stable path (e.g. `multiplierai.ai/recon/{client}/{week}`) and email a link, matching the benchmark-report pattern. Side benefit: per-client, per-week view data — retention telemetry that doesn't exist with attachments.

---

## F. Open Judgment Calls — decide before implementing

**F1. Decimal precision.** Two decimals everywhere (50.98%, 71.43%) reads machine-generated. Proposal: whole numbers in prose and headline stats (51%, 71%, 33%); one decimal only for the hero gap (2.6pp). Trade-off: precision-as-proof vs. exec readability.

**F2. Hosted delivery (E2).** Confirm hosting approach and URL scheme before building the send flow.

---

## Keep as-is

- Section architecture: Executive view → Providers → Topic position → Movements → Actions → Limitations.  
- The Primary Implication card — the strongest element on the page.  
- All arithmetic verified correct (338/663 overall, per-provider counts, proportional bar widths).  
- Print handling (page break after cover, hidden print button, break-inside rules).  
- No pitch anywhere in the document — it ends on method and evidence. Do not add one.

