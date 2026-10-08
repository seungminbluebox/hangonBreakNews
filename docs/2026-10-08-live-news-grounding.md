# Live-news grounding and sentence-integrity correction

## Scope and active paths

Baseline: `seungminbluebox/hangonBreakNews` main
`5846747e836624d2e8470986bf3f899aaa445f91`.

The README's production replacement is `gnews_tracker.py`. Production and
preview use `run_simple_cycle` / `run_dry_run` →
`news_pipeline.run_two_stage_pipeline` → `news_selector._decision_to_item`.
The single-stage `select_and_summarize` compatibility API shares the same
sentence/entity validators. The independently runnable older RSS
`breaking_tracker.py` now preserves the real headline through both AI passes
and uses the same provenance/quality gate after its second pass.

This work did not inspect the running server's process configuration. It does
not establish which revision/process produced an existing public row.

## Evidence and reproduction

The public `/live` audit retained five bad outputs at approximately
2026-10-08 23:17–23:21 KST. Exact strings, original links, and provenance are in
`tests/fixtures/live_quality_20261008.json`:

- Zambia rendered as `조바키아` in a solar-generation article.
- Tesla FSD validation cooperation rewritten as investment-recommendation
  restrictions, with a separate privacy statement assigned to Tesla.
- India, Iraq, and oil-stock reports with duplicated `했습니다.입니다.` endings.

The original GNews payloads and model responses are unavailable. Do not call
these captured provider replay fixtures. The fixture separates observed public
output, verified short public-source excerpts, constructed input titles, and
output-only regressions. Other tests are explicitly synthetic.

Public corroboration checked on 2026-10-08:

- Zambia Ministry of Energy: <https://www.moe.gov.zm/?p=6475>. The ministry
  confirms Zambia's 2026 solar target and identifies Hakainde Hichilema as
  president. The original Africa.com syndicated URL could not be opened.
- Original Tesla publisher: <https://news.einfomax.co.kr/news/articleView.html?idxno=4438430>.
  Search retrieved the article's source passages after direct open failed. It
  reports Tesla's FSD safety-validation cooperation; its privacy-compliance
  statement concerns BYD Korea. The fixture contains short attributed excerpts,
  not a full copy or a fabricated provider response.

The immutable baseline accepts all five observed outputs when paired with the
limited verified evidence where available. For the three ending regressions,
only the sentence validator is replayed. Patched tests reject all five. This
proves these deterministic regression boundaries, not live model quality.

## Root causes and correction

1. The old sentence validator checked only the final `니다.` suffix and split
   sentences only at punctuation followed by whitespace. It accepted bare
   ending fragments, doubled endings, and three adjacent sentences. Validation
   now checks each complete reporting sentence, including no-space boundaries,
   while retaining decimals and quoted fragments.
2. Geography checking was confined to macroeconomic indicators and omitted
   Zambia. Recognized headline country aliases now apply across categories;
   source spelling is also accepted. Added aliases include Zambia, Zimbabwe,
   Slovakia, Slovenia, and South Africa. A bounded Tesla alias protects that
   observed corporate identity. Alias matching is boundary-aware while allowing
   Korean particles, so India does not match inside Indonesia. Contradictory
   title/body substitutions are checked for the known confusable India/Indonesia,
   Slovakia/Slovenia, and Zambia/Zimbabwe groups. Explicit source-backed compound
   names preserve Bank of Japan / Bank of Korea / Tesla Korea without weakening
   the India/Indonesia boundary. Unknown names are not guessed.
3. Generated selection rationale was passed into the writing stage. The
   summary prompt now includes only source fields, stable identity, and
   deterministic source-name hints. It explicitly separates speakers, official
   roles, product restrictions, recommendations, and publisher disclaimers.
4. The summary contract now requests an exact 12–600-character source excerpt.
   Its literal presence is checked locally; missing/fabricated evidence is an
   item-level quality failure, without extra model calls. Placeholder and provider
   truncation markers cannot serve as source evidence. Identity mismatches
   and duplicate result identities fail the response contract.
5. Bounded output checks reject unsupported recommendation claims, explicit
   official-role conflicts, and unambiguous Korean privacy statements assigned
   to a different named subject. Publisher investment disclaimers are not
   evidence of recommendations. For recommendation restrictions, recognized
   actors and the restriction predicate must occur in the same supporting source
   sentence; another company's recommendation does not establish the claim.
6. The old RSS second pass could treat the first AI's rewritten headline as
   source evidence when extraction failed. The original headline now survives;
   the second result goes through the shared gate. Quality-rejected RSS
   candidates remain eligible if fetched again.

## Source-preserving fallback and observability

When a selected summary is omitted or fails quality, a fallback is possible
only if the provider has a Korean title of at most 55 characters and a complete
12–400-character Korean description/body that passes the normal gate. No
truncation, inferred completion, new translation, or silent normalization is
used. Ordinary plain-style reporting can be quoted inside a formal attribution;
the provider's wording stays intact. Published content begins `원문 발췌:`.

Unsafe cases remain unevaluated. `quality_failed` counts unresolved items,
including omitted summaries. `quality_reasons` retains internal reason counts.
`source_fallbacks` counts safe fallback candidates; cycle logs expose that count
and mark the cycle partial. Event logs include only safe reason/status fields,
not raw evidence, credentials, or private provider content. Selection/summary
still have one shared transport/schema retry and no extra repair call.

## Offline verification

Intended safe suite: `python -m unittest discover -s tests -q`.
Do not run root-level manual `test.py`, `test_script.py`, `test_time_logic.py`,
production mains, or live dry-run commands as automated regression tests.
Some are manual network/DB/notification probes.

Verification used an existing Python 3.12 environment with requests and dotenv,
with a startup socket guard blocking actual `connect`, `connect_ex`, and
`create_connection`. No packages were installed. Production APIs, DB writes,
notifications, deployments, and process restarts were not performed.

Red/green evidence:

- Initial synthetic grounding tests: 7 tests, 9 failing assertions before
  implementation; green after the sentence/entity/source-chain correction.
- Pipeline provenance/fallback contract: 6 new tests failed before its change;
  then passed with the existing request-budget tests unchanged.
- RSS shared validation and fallback cycle visibility: 2 failures before
  integration; both pass afterward.
- Plain Korean source fallback: failed before support; passes with exact source
  wording preserved, and malformed source remains rejected.
- Role/actor/disclaimer regressions: 3 failures before bounded checks; all pass
  afterward.
- Review negative controls: 7 failing assertions for disclaimer/other-company
  evidence, placeholder excerpts, and country-prefix/substitution boundaries;
  all pass after the bounded fixes, including a valid multi-country control.
- Compound-name preservation: 3 failing positive controls for 일본은행, 한국은행,
  and 테슬라코리아; source-span-backed compound aliases restore all three while
  the country-prefix/substitution negative controls remain green.
- Final full suite: 264 tests pass. Existing tests emit expected fake-repair
  warnings while exercising failure paths. They do not indicate external calls.

Two older dedup fixture titles were updated to include their source country
(`한국`), preserving their original deduplication assertions under the new
country-retention contract. Existing cost/HTTP-spy, deduplication, number,
unit, malformed-output, failure-isolation, and worker tests remain in the suite.

## Limitations and rollout

Literal evidence is provenance, not semantic entailment. Alias lists and
role/claim checks are bounded safeguards; unknown entities, complex pronouns,
multi-actor attribution, unusual punctuation, and nuanced financial meaning
still need model fidelity and human sampling. These checks are not a universal
translator or fact checker. Conservative failures can reduce accepted volume;
monitor `quality_failed`, `source_fallbacks`, and their reason events after a
separately authorized rollout. Source excerpts add output tokens within the
existing configured summary allowance; live throughput has not been measured.

No historical database rows were edited. No DB/public schema changed. Raw
`source_content` storage remains byte-for-byte provider content. Source excerpts
and internal quality metadata are not added to the public row or notification
contract. Publishing the code does not deploy it or repair already-stored rows.
