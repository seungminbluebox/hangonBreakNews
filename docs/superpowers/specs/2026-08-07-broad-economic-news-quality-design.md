# Broad Economic News Quality Design

## Goal

Keep the feed broad enough to cover concrete stock-market and economic developments, while preventing inaccurate summaries and exaggerated breaking-news labels.

## Chosen approach

Use broad collection with strict output validation and ranking.

- Do not remove an article merely because it concerns a small company, a local market, a reverse split, an asset purchase, a plant closure, or a board-level appointment.
- Keep concrete events that can affect a company, industry, employment, regulation, commodities, or investors.
- Continue excluding pure promotion, lifestyle content without economic consequences, unsupported opinion, stale repackaging, and items without a new fact.
- Use importance to control prominence instead of using hard filters for every niche event.

This is preferred over two alternatives:

1. Strong hard filtering would produce a cleaner feed but would miss useful company- and portfolio-specific developments.
2. Storing everything and relying on the frontend would preserve coverage but would allow malformed summaries and inflated alerts into the user experience.

## Selection and importance

- Score 7: concrete but narrow company, regional, regulatory, employment, or industry news.
- Score 8: major-company results and transactions, significant regulation, policy changes, and broadly relevant economic releases.
- Score 9–10: only events with immediate and wide market or financial-system impact.
- Ordinary acquisitions and takeover agreements remain capped at 8 even when the source uses wording such as `takeover`, `deal to buy`, or `agreed to buy` instead of `agrees to acquire`.

## Summary quality rules

1. Jobless-claims counts use `건`, never `명`, regardless of whether the count rose, fell, or stayed below a threshold.
2. Every material number in the generated title must also appear in the summary body, allowing equivalent unit conversions. This prevents a revenue number in the title from being replaced by a different order-volume number in the body.
3. Titles that end with an incomplete modifier, particle, or dangling expression are rejected rather than stored. The prompt also requires a shorter complete title instead of character-level truncation.
4. The prompt must preserve cause, action, and response roles. A union opposing a closure must not be described as causing the closure.
5. Preliminary exploration results must not be promoted to a confirmed reserve or deposit discovery unless the source explicitly says so.
6. Security-test behavior must retain whether it occurred in a controlled test or against a real external system.
7. Legal and regulatory summaries must identify the affected company, institution, rule, or case. Generic summaries without an identifiable subject are excluded.

## Data flow and compatibility

The existing flow remains unchanged:

`GNews candidates -> deterministic candidate checks -> AI selection/translation -> deterministic summary validation -> recent-event deduplication -> breaking_news insert -> existing push rules`

There is no database migration, adapter change, schedule change, collection-volume change, or notification-contract change.

## Failure behavior

- A malformed title or summary is not stored.
- Because rejected articles are not inserted into `breaking_news`, URL-based database deduplication does not mark them as processed; they may be considered again if GNews returns them in a later cycle.
- AI failures retain the existing retry behavior.

## Tests

Add regression coverage for:

- retaining concrete niche corporate and market actions;
- jobless-claims unit correction for non-rising counts;
- rejecting a title number missing from the summary;
- rejecting incomplete titles;
- capping broader acquisition and takeover wording at 8;
- prompt requirements for causality, preliminary exploration, controlled security tests, and identifiable legal subjects;
- preserving current database, schedule, collection-volume, and notification behavior.
