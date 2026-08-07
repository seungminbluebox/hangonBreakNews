# Broad Economic News Quality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve broad stock-market and economic coverage while rejecting malformed summaries and preventing ordinary transactions from being labeled as emergency breaking news.

**Architecture:** Keep the existing `news_selector.py` pipeline and add narrowly scoped deterministic checks after AI normalization. Relax only pre-AI filters that currently remove concrete economic actions, while retaining filters for promotion, stale recaps, unsupported opinion, and events without a new fact.

**Tech Stack:** Python 3.12+, `unittest`, regular expressions, existing GNews/OpenRouter/Supabase pipeline.

## Global Constraints

- Keep `breaking_news` schema and frontend contract unchanged.
- Keep the five-minute schedule, Korea/US/world request structure, per-cycle volume, and push behavior unchanged.
- Store importance as an integer from 7 through 10.
- Do not add dependencies, API calls, database migrations, or secrets.
- Write a failing regression test before every production behavior change.

## File Structure

- Modify `news_selector.py`: candidate scope, Korean normalization, title/body validation, importance normalization, and prompt rules.
- Modify `tests/test_news_selector.py`: public `select_and_summarize` regression tests.
- Modify `readme.md`: document broad collection and the new validation rules.
- Use the approved design at `docs/superpowers/specs/2026-08-07-broad-economic-news-quality-design.md` as the behavior contract.

---

### Task 1: Preserve Concrete Niche Economic Actions

**Files:**
- Modify: `tests/test_news_selector.py`
- Modify: `news_selector.py` in `_is_low_value_item`

**Interfaces:**
- Consumes: `select_and_summarize(articles, generator, *, batch_size=10, recent_news=None)`.
- Produces: concrete pricing and regulatory actions reach the AI candidate prompt; pure product promotion still does not.

- [ ] **Step 1: Change the local-penalty regression test to require retention**

Update the existing test so a monetary penalty against named companies remains a candidate:

```python
def test_keeps_concrete_company_penalties_before_ai(self):
    scooter_fine = article(
        "rome-scooter-fine",
        "Rome fines Lime Bird and Dott over e-scooter services",
        "The city imposed a EUR 2.675 million fine on three scooter operators.",
        "https://example.com/rome-scooter-fine",
    )
    generator = FakeGenerator("[]")

    select_and_summarize([scooter_fine], generator)

    self.assertIn("rome-scooter-fine", generator.prompts[0])
```

- [ ] **Step 2: Add a regression test for a concrete future pricing change**

```python
def test_keeps_announced_company_pricing_changes_before_ai(self):
    baggage_fee = article(
        "future-baggage-fee",
        "Jetstar to charge baggage fee from February 2027",
        "The airline will charge passengers up to $37 for baggage next year.",
        "https://example.com/future-baggage-fee",
    )
    generator = FakeGenerator("[]")

    select_and_summarize([baggage_fee], generator)

    self.assertIn("future-baggage-fee", generator.prompts[0])
```

- [ ] **Step 3: Run both tests and verify RED**

Run:

```powershell
python -m unittest tests.test_news_selector.NewsSelectorTests.test_keeps_concrete_company_penalties_before_ai tests.test_news_selector.NewsSelectorTests.test_keeps_announced_company_pricing_changes_before_ai
```

Expected: both fail because `_is_low_value_item` removes these candidates before prompt creation.

- [ ] **Step 4: Remove only the two over-broad hard filters**

Delete the `_is_low_value_item` branches that unconditionally reject baggage-fee changes and local e-scooter penalties. Keep product-launch, routine stable-currency, unsupported warning, scam-advisory, and contradictory-data filters unchanged.

- [ ] **Step 5: Run the focused tests and selector suite**

Run:

```powershell
python -m unittest tests.test_news_selector.NewsSelectorTests.test_keeps_concrete_company_penalties_before_ai tests.test_news_selector.NewsSelectorTests.test_keeps_announced_company_pricing_changes_before_ai
python -m unittest tests.test_news_selector
```

Expected: PASS.

---

### Task 2: Correct Jobless-Claims Units for Every Direction

**Files:**
- Modify: `tests/test_news_selector.py`
- Modify: `news_selector.py` in `_normalize_known_korean_terms`

**Interfaces:**
- Consumes: source text containing `jobless claims` and Korean AI output containing a numeric count followed by `명`.
- Produces: the same count expressed with `건`; the existing `rose to` correction remains intact.

- [ ] **Step 1: Add the failing below-threshold regression test**

```python
def test_normalizes_jobless_claim_count_below_threshold_as_cases(self):
    source = article(
        "jobless-below-200k",
        "US jobless claims stay below 200,000 for third week",
        "Initial jobless claims remained below 200,000 for a third consecutive week.",
        "https://example.com/jobless-below-200k",
    )
    response = """[{"temp_id":0,"source_ref":"jobless-below-200k","source_title":"US jobless claims stay below 200,000 for third week","title":"미국 신규 실업수당 청구 3주 연속 20만명 하회","content":"미국 신규 실업수당 청구 건수가 3주 연속 20만명을 밑돌았습니다.","importance_score":7,"category":"indicator","news_type":"new_development","selection_reason":"주간 고용 지표가 발표됐습니다."}]"""

    selected = select_and_summarize([source], FakeGenerator(response))

    self.assertEqual(selected[0]["normalized_title"], "미국 신규 실업수당 청구 3주 연속 20만건 하회")
    self.assertIn("20만건", selected[0]["normalized_content"])
```

- [ ] **Step 2: Run the test and verify RED**

Expected: output still contains `20만명` because the current correction only handles `rose to`.

- [ ] **Step 3: Generalize the unit correction after the existing rise-to correction**

Add a source-gated replacement:

```python
if "jobless claims" in source_text:
    value = re.sub(
        r"(\d[\d,.]*(?:만|천)?)(\s*)명",
        r"\1\2건",
        value,
    )
```

Keep the special `명 증가 -> 건으로 증가` transformation before this general replacement.

- [ ] **Step 4: Run both jobless tests and the selector suite**

Expected: the new threshold case and the existing `rose to 199,000` case both pass.

---

### Task 3: Reject Incomplete Titles and Title/Body Number Drift

**Files:**
- Modify: `tests/test_news_selector.py`
- Modify: `news_selector.py`

**Interfaces:**
- Produces: `_has_incomplete_title(title: str) -> bool` and `_title_numbers_missing_from_content(title: str, content: str) -> set[str]`.
- Consumes: normalized Korean title and content before source-number validation.

- [ ] **Step 1: Add a failing incomplete-title test**

Use an RWE response whose title ends with `규모 가` and assert `selected == []`.

- [ ] **Step 2: Add a failing title/body number-drift test**

Use a DoorDash source containing both `$4.45 billion revenue` and `$33.1 billion gross order value`. Generate a title containing `44.5억 달러` and content containing only `331억 달러`; assert `selected == []`.

- [ ] **Step 3: Run both tests and verify RED**

Expected: both malformed summaries are currently accepted because all numbers exist somewhere in the source.

- [ ] **Step 4: Implement the minimal validators**

```python
INCOMPLETE_TITLE_ENDINGS = (
    "규모 가",
    "위한",
    "관련",
    "따른",
    "통해",
    "대해",
    "하며",
    "하고",
)

def _has_incomplete_title(title: str) -> bool:
    normalized = title.strip().rstrip(".!?")
    return any(normalized.endswith(ending) for ending in INCOMPLETE_TITLE_ENDINGS)

def _title_numbers_missing_from_content(title: str, content: str) -> set[str]:
    title_numbers = _event_numeric_tokens(title)
    content_numbers = _event_numeric_tokens(content)
    return title_numbers - content_numbers
```

Call both after `_normalize_known_korean_terms` and before `_unsupported_summary_numbers`. Log the source reference and reject the decision when either check fails.

- [ ] **Step 5: Add passing controls**

Verify a complete short title and a title whose material number is repeated in content remain selected. This guards against rejecting titles only because the publication year is omitted from content; `_event_numeric_tokens` already excludes years from 1900 through 2100.

- [ ] **Step 6: Run focused tests and selector suite**

Expected: PASS.

---

### Task 4: Cap Broader Acquisition Wording at Importance 8

**Files:**
- Modify: `tests/test_news_selector.py`
- Modify: `news_selector.py` in `_normalize_importance_score`

**Interfaces:**
- Consumes: raw source text and AI importance 9 or 10.
- Produces: importance 8 for ordinary acquisitions and takeovers unless an existing systemic marker applies.

- [ ] **Step 1: Add a failing takeover-wording regression test**

```python
def test_caps_takeover_deal_at_eight(self):
    source = article(
        "apollo-easyjet-takeover",
        "Apollo reaches $7.7 billion deal to buy easyJet",
        "Apollo agreed a takeover of the airline.",
        "https://example.com/apollo-easyjet-takeover",
    )
    # AI response uses importance_score 9 and repeats $7.7 billion in title/content.
    selected = select_and_summarize([source], FakeGenerator(response))
    self.assertEqual(selected[0]["importance_score"], 8)
```

- [ ] **Step 2: Run the test and verify RED**

Expected: score remains 9 because current markers do not include `deal to buy` or `takeover`.

- [ ] **Step 3: Extend ordinary-transaction detection**

Add exact markers `agreed to buy`, `deal to buy`, `takeover agreement`, `acquisition of`, and `buyout`, plus bounded regexes for `plans/agrees/agreed to acquire` and the word `takeover`. Preserve `has_systemic_marker` as the exception.

- [ ] **Step 4: Run all importance tests and selector suite**

Expected: existing systemic events remain 9–10, while all ordinary transaction wording is capped at 8.

---

### Task 5: Strengthen Prompt Contracts Without Narrowing Coverage

**Files:**
- Modify: `tests/test_news_selector.py`
- Modify: `news_selector.py` in `_selection_prompt`
- Modify: `readme.md`

**Interfaces:**
- Produces: a prompt that explicitly preserves causality, evidence level, test context, identifiable legal subjects, and complete title/body claims.

- [ ] **Step 1: Add a prompt-contract test**

Call `select_and_summarize` with a candidate and `FakeGenerator("[]")`, then assert its captured prompt contains requirements equivalent to:

- concrete niche stock/economic events remain eligible at score 7;
- cause, action, and response actors must not be reversed;
- exploration results are not confirmed deposits unless explicitly stated;
- controlled security tests are distinguished from real external attacks;
- legal/regulatory stories name the affected subject;
- every title number is repeated in the summary;
- a complete title is preferred over a hard 35-character truncation.

- [ ] **Step 2: Run the prompt test and verify RED**

Expected: one or more required clauses are absent.

- [ ] **Step 3: Update the prompt minimally**

Replace the strict `35자 이내` wording with `가급적 35자, 완결성을 위해 최대 55자`. Add the six factual/coverage clauses without changing JSON fields, selectable categories, news types, or score range.

- [ ] **Step 4: Update README behavior documentation**

Document broad retention, jobless `건`, title/body number consistency, incomplete-title rejection, and broader ordinary-transaction capping. Keep schedule, per-cycle volume, database, and notification text unchanged.

- [ ] **Step 5: Run the selector suite**

Expected: PASS.

---

### Task 6: Full Verification and Delivery

**Files:**
- Verify: `news_selector.py`
- Verify: `tests/test_news_selector.py`
- Verify: `readme.md`

**Interfaces:**
- Produces: a tested commit with no schema, tracker, or dependency changes.

- [ ] **Step 1: Compile changed Python files**

```powershell
python -m py_compile news_selector.py gnews_tracker.py gnews_adapter.py tests\test_news_selector.py
```

- [ ] **Step 2: Run the full project suite**

```powershell
python -m unittest discover -s tests
```

Expected: all tests pass.

- [ ] **Step 3: Verify the diff scope**

```powershell
git status --short
git diff --check
git diff --stat
git diff --numstat -- gnews_tracker.py gnews_adapter.py
```

Expected: only `news_selector.py`, `tests/test_news_selector.py`, `readme.md`, and this plan are involved; tracker and adapter have no changes.

- [ ] **Step 4: Commit after verification**

```powershell
git add news_selector.py tests/test_news_selector.py readme.md docs/superpowers/plans/2026-08-07-broad-economic-news-quality.md
git commit -m "fix: preserve broad economic news with stricter summaries"
```

- [ ] **Step 5: Push only with user authorization**

```powershell
git push origin main
```
