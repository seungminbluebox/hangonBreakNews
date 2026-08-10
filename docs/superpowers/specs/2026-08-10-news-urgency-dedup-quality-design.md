# News urgency, duplicate, and summary quality design

Date: 2026-08-10

## Goal

Improve the quality of the broad economic-news feed without narrowing its general stock-and-economy coverage. The change must correct clear urgency inversions, suppress only high-confidence low-value analysis, merge one confirmed bank-delinquency duplicate pattern, and repair observed Korean wording problems.

## Scope

- Keep the current GNews request regions, fetch counts, schedule, database schema, and frontend contract.
- Keep broadly relevant company, market, macroeconomic, commodity, technology, and geopolitical news.
- Do not change the policy/regulation category or its frontend integration in this work.
- Do not add another AI call.

## Selected approach

Use a conservative hybrid of the existing AI decision and deterministic validation:

1. Let the AI continue selecting, translating, summarizing, categorizing, and scoring articles.
2. Apply narrow deterministic score correction only when the source text contains strong evidence.
3. Reject only clearly non-event analysis or comparison patterns observed in production output.
4. Treat repairable wording and summary defects as quality failures so the existing bounded retry queue can retry them up to three times.
5. Extend semantic duplicate detection only for a well-defined bank overdraft delinquency release signature.

This is preferred over prompt-only changes because free-model output has repeatedly ignored detailed prompt rules. A second AI critic is rejected because it adds cost, latency, and another failure point.

## Urgency scoring

The deterministic layer may raise an AI score to 9 only for a narrow confirmed systemic event, such as:

- confirmed joint or direct foreign-exchange intervention;
- an emergency central-bank rate or liquidity action;
- a market-wide trading halt;
- a sovereign default or similarly explicit systemic event.

An upgrade requires affirmative source wording. Speculative or attributed uncertainty markers such as `may`, `could`, `reportedly`, `expected`, `estimated`, or their Korean equivalents prevent the upgrade.

Broad trends, constraints, comparisons, previews, and outlooks remain capped at 8 unless the source also reports a concrete qualifying action or immediate shock. Ordinary earnings and company events continue to use the existing scoring rules.

## Low-value filtering

Add only high-confidence patterns for production examples that contain no material new economic event:

- past-return comparison articles framed as one stock versus another;
- evergreen business-model explainers;
- soft expressions of buyer interest without a deal, contract, tender, investment, or other concrete step;
- previews or outlook pieces that only discuss how a market trend may be tested;
- isolated platform or analyst disputes with no material company, market, legal, or regulatory consequence;
- operational safety incidents with no material market, company-financial, supply-chain, or policy impact.

Borderline but genuine stock-and-economy developments remain eligible. The filter must not reject an article merely because it contains words such as `interest`, `comparison`, or `outlook` when concrete new facts are present.

## Duplicate handling

Treat two reports as the same bank-overdraft delinquency event only when they share a conservative signature:

- the same five-major-bank context;
- overdraft or credit-line delinquency as the same metric;
- the same reporting period or publication window;
- at least one shared source-backed figure or the same direction and institution set.

When duplicates appear in one batch, retain the item with the richer source-backed title and summary. A later report with a new period, new figure, new institution set, or material follow-up remains eligible.

## Korean wording and summary validation

- Normalize the observed mistranslation `취업 데이터` to `고용지표` only when the English source is about employment or labor-market data.
- Normalize malformed share-repurchase wording to `자사주 매입` or `자사주 매입 완료` only when the source explicitly contains `share repurchase`, `stock repurchase`, or `buyback`.
- Reject a definitive generated title when the source and generated summary remain explicitly speculative.
- If the generated title or summary claims a quantified rise, fall, growth, or decline while omitting the source's central metric, route the item through the existing quality retry flow. Do not require a number for qualitative actions such as launches, appointments, investigations, or approvals.

## Retry and failure behavior

All new repairable quality failures use the existing process-local retry queue. Fresh articles continue to be processed first each cycle. A failed item is retried on later cycles up to the existing three-attempt limit, without blocking fresh collection. Permanently low-value items are rejected normally and are not retried.

## Testing

Add regression tests before implementation for:

- confirmed joint FX intervention upgraded to 9;
- speculative intervention not upgraded;
- broad AI-data constraint trend capped below urgent;
- each high-confidence low-value pattern, plus positive counterexamples containing a concrete event;
- the two five-bank overdraft delinquency variants deduplicated while different periods remain separate;
- source-gated Korean normalization;
- definitive-title/speculative-source mismatch and metric-omission quality retry behavior;
- unchanged policy category behavior and existing broad-news acceptance through the full suite.

## Non-goals

- No policy/regulation category changes.
- No database migration or historical-row cleanup.
- No frontend changes.
- No change to GNews request volume, regions, or cadence.
- No additional LLM verification request.
