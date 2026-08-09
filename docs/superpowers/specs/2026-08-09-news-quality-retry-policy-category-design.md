# News Quality Retry and Policy Category Design

## Purpose

Improve the GNews pipeline without narrowing its broad stock and economic-news coverage or adding an AI verification call for every article. The pipeline must preserve important articles when the first AI response contains a malformed title or summary, while preventing visibly broken output from reaching `breaking_news`.

This change also adds `policy` as a first-class news category. The existing database row shape, Pulse title/content reading contract, notification preferences, five-minute non-overlapping schedule, and GNews collection volume remain unchanged.

## Chosen Approach

Keep the current single-threaded collector and one normal AI selection call per batch. Add conservative deterministic validation after that call. Only articles that the AI selected but rendered incorrectly are eligible for a focused repair request and later retry. Articles the AI intentionally omitted remain ordinary editorial rejections.

This approach is preferred over prompt-only validation, which has already allowed malformed output, and over a mandatory second AI reviewer, which would add cost and latency to every article.

## Data Flow

1. Fetch new GNews articles at the start of every cycle.
2. Remove exact URLs already stored in `breaking_news`.
3. Process newly fetched articles before retry candidates.
4. Ask the AI once to select, translate, summarize, score, and categorize each batch.
5. Apply deterministic identity, evidence, formatting, direction, numeric, importance, and duplicate checks.
6. For an AI-selected article that fails a repairable quality check, make at most one focused repair request during that cycle.
7. Save and publish articles that pass.
8. Keep unresolved repairable articles in the process-local pending queue instead of adding them to the evaluated cache.
9. Retry a bounded number of pending quality failures in later cycles without blocking newly fetched articles.
10. Treat intentional AI exclusions, confirmed duplicates, and exhausted retry candidates as evaluated.

Cycles remain non-overlapping. No background thread or concurrent writer is introduced. If one cycle exceeds five minutes, the next cycle starts after it completes.

## Title Integrity

The structured-output schema will match the prompt: titles should normally stay within 35 Korean characters but may use up to 55 characters to finish the thought. The current 35-character hard schema limit will be raised to 55.

A title is not rejected merely because it contains a particular topic or keyword. It is considered repairable only when there is strong structural evidence of truncation, such as:

- unmatched brackets or quotation marks;
- an incomplete final connective or broken word;
- a trailing particle that requires a missing predicate;
- a bare numeric prefix where the summary contains the complete amount;
- a one-character trailing fragment near the output limit that is completed in the summary.

Ambiguous cases pass. A suspicious title is sent to focused repair with the original article identity and facts preserved.

## Title and Summary Direction

Direction terms are normalized into small opposing groups, including rise/fall, increase/decrease, approval/rejection, profit/loss, and upgrade/downgrade. A result is repairable only when the title asserts one direction and the summary contains only its opposite for the same headline claim.

Mixed corporate results remain valid when the summary contains the title's direction as well as a different direction for another metric. The validator therefore checks whether the headline direction is supported in the summary instead of rejecting any article containing both positive and negative terms.

## Translation and Numeric Quality

Known recurring transliteration errors may be corrected with narrowly scoped, source-supported normalization. Examples include `체비론` to `셰브론` and `액화석유가` to `액화석유가스` when the source refers to Chevron or LPG.

Malformed Korean numeric structures, such as repeated or out-of-order large-number units, are not guessed. They trigger focused repair. Existing checks for numbers absent from the source remain in place.

## Conservative Event Deduplication

Semantic normalization may map closely related expressions such as employment, unemployment rate, and jobs data to a common labor-market event concept. That concept alone is never enough to remove an article.

Two items are treated as the same event only when several signals agree:

- same country, company, agency, or clearly identified primary actor;
- same event family or official release;
- compatible reporting period or close publication time;
- matching key number, decision, or action.

When uncertain, keep both articles. A follow-up containing a new official decision, materially changed number, approval, cancellation, completion, or other substantive development remains eligible for storage.

## Importance and Urgency

The AI continues to assign importance from 7 to 10. Deterministic code does not discard an article merely because it is analysis, a forecast, a lawsuit, or a niche company story.

Trend, outlook, or obstacle stories without a concrete new action or immediate broad market effect may be capped at 8, so they remain visible as major economic news but do not generate an urgent alert. Scores 9 and 10 remain reserved for events with broad impact, exceptional magnitude, and immediate market relevance. Existing notification routing remains unchanged: scores 9 and above notify both breaking-news audiences.

## Policy Category

Add `policy` to the selector's allowed category values and structured-output schema.

- `policy`: legislation, taxation, government policy, regulator decisions, and rules applying across a market, industry, or broad group of consumers or firms.
- `corporate`: earnings, acquisitions, products, supply-chain changes, lawsuits, and enforcement focused on a specific company.
- `market`: asset-market moves and central-bank monetary policy.
- `indicator`: official numeric releases such as inflation, employment, growth, production, and consumption.
- `geopolitics`: war, diplomacy, sanctions, and conflict between states.

The `breaking_news.category` value is passed through as a string. Repository migrations do not show a category check constraint, so no migration is planned. Deployment instructions must still tell the operator to confirm the live column is text or that any live check constraint accepts `policy` before enabling backend output.

Pulse currently reads `id`, `title`, `content`, `importance_score`, `created_at`, and optional `pulse_story` from `breaking_news`, so no Pulse change is required. Notification routing also does not depend on the news category.

## Retry State

The selector must distinguish three outcomes:

- selected and valid;
- intentionally rejected or duplicate;
- selected but repairable quality failure.

The tracker will retain only the third outcome in `pending`. Retry metadata must be process-local and must not alter the database schema. Newly fetched articles are processed before retry candidates, and retries are capped per cycle so stale failures cannot starve fresh news.

A repairable article receives at most three retries after its initial selection call: one focused repair attempt during its first cycle, followed by at most two later-cycle attempts. At most ten carried-over quality failures are processed after fresh candidates in one cycle. These limits will be named constants and covered by tests. Database failures keep their existing retry behavior. A process restart may lose retry counters, which is acceptable because no rejected or inferred metadata is persisted as fact.

## Frontend Handoff

The backend repository will not modify frontend code. The final handoff will include a copyable prompt instructing the frontend task to:

- add `{ id: "policy", label: "정책/규제" }` to the live-page category list;
- include it in default and reset selections;
- preserve importance-based breaking labels and notification preferences;
- verify filtering, realtime inserts, share cards, and empty/legacy category behavior;
- deploy the frontend before or together with the backend so `policy` rows are not hidden by the current four-category filter.

## Testing

Regression tests will be written before each production change and observed failing for the intended reason. Coverage will include:

- 55-character schema compatibility and representative truncated-title cases;
- valid mixed-direction earnings and invalid opposite-direction summaries;
- source-gated terminology normalization and malformed Korean amounts;
- same labor-market release across different headline angles;
- preservation of ambiguous or materially updated related stories;
- urgency caps for vague trend stories without excluding them;
- `policy` schema, prompt, validation, and storage pass-through;
- retryable quality failures remaining pending while intentional exclusions become evaluated;
- new-article priority and bounded retry work per cycle;
- unchanged DB row, Pulse, and notification contracts.

## Non-Goals

- No second AI verification pass for every article.
- No multithreaded collector or overlapping cycles.
- No new canonical table or database fields.
- No change to GNews request volume, collection regions, or five-minute interval.
- No broad keyword blacklist for economic relevance or importance.
