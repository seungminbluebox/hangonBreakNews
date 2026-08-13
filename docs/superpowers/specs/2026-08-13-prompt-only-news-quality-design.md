# Prompt-only news quality refinement

## Goal

Improve the economic relevance, Korean summary quality, and urgency labeling of
GNews selections by changing only the existing AI selection and repair prompts.

## Scope

- Change prompt wording in `news_selector.py` only.
- Keep GNews collection requests, schedule, batch size, and AI call flow unchanged.
- Keep deterministic filters, validators, importance normalization, categories,
  database schema, notifications, and frontend contracts unchanged.
- Keep material follow-ups as separate articles. A new casualty count, confirmed
  decision, revised statistic, approval, cancellation, or other state change may
  be stored even when it belongs to an existing event.

## Selection guidance

The prompt will reject stories whose main fact is a local crime, ceremonial
event, award, routine visit, tiny community grant, generic regional roundup, or
other general-interest item without a direct economic, market, industry,
policy, supply-chain, employment, production, pricing, or major-company effect.

It will continue to accept concrete economic developments regardless of company
or country size, including earnings, investment, financing, production,
employment, pricing, contracts, regulation, trade, supply disruption, and
material corporate actions.

Major disasters and geopolitical events remain eligible when the event itself
is nationally significant or can affect energy, commodities, trade routes,
supply chains, sovereign risk, or financial markets. The prompt must not invent
an economic impact that is absent from the provider text.

## Summary guidance

- Preserve the source actor, country, direction, currency, unit, and time basis.
- Do not convert currencies or guess missing geography.
- Do not emit vague titles such as `major ASEAN news` or an unexplained metric
  such as `PPI surged` without the country and measured item.
- Use natural Korean for proper nouns and institutions when the source supports
  the identification; otherwise retain a faithful transliteration rather than
  guessing.
- Exclude a candidate when the available provider text is too broken or
  ambiguous to produce a faithful Korean title and summary.

## Urgency guidance

Scores 9-10 are reserved for confirmed, time-sensitive events with broad and
immediate consequences, such as war escalation, a major disaster, closure of a
strategic transport route, severe supply disruption, emergency central-bank
action, sovereign default, or a systemic market shock.

Routine proposals, planned discussions, regulatory sandboxes, statements,
forecasts, ordinary earnings, ordinary transactions, and non-final reviews must
remain at 7-8 even when economically relevant.

## Duplicate behavior

The existing URL and semantic duplicate handling remains unchanged. Reworded
coverage without a new fact is a duplicate. A material follow-up remains a
separate article, preserving its newer facts and original URL.

## Tests

Prompt contract tests will verify that:

1. general-interest local incidents and vague roundup stories are explicitly
   excluded unless they contain a direct material economic effect;
2. concrete small-company economic developments remain eligible;
3. geography, currency, units, direction, and actors must stay source-backed;
4. vague or irreparably broken summaries must be omitted;
5. routine proposals and discussions are capped at 7-8 in the prompt;
6. material follow-ups remain eligible as separate articles;
7. no request, schedule, schema, notification, or deterministic deduplication
   behavior changes.
