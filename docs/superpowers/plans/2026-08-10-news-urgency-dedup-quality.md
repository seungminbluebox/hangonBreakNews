# News Urgency, Duplicate, and Summary Quality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Correct clear urgency inversions, remove only high-confidence non-event articles, merge one confirmed bank-overdraft delinquency duplicate pattern, and retry observed Korean summary defects without changing policy-category behavior.

**Architecture:** Keep the existing AI-first selector and add narrow deterministic guardrails inside `news_selector.py`. Reuse `SelectionResult.invalid_fields` and the current bounded quality-retry queue instead of adding an AI request or persistence layer. Extend semantic duplicate matching with one conservative report signature and preserve the more complete item through the existing deduplication path.

**Tech Stack:** Python 3.12, standard-library `re` and `difflib`, `unittest`, existing GNews/OpenRouter/Supabase pipeline.

## Global Constraints

- Do not change the policy/regulation category or frontend integration.
- Do not change GNews regions, request counts, five-minute cadence, database schema, or frontend fields.
- Do not add another AI call.
- Keep broadly relevant stock, company, market, macroeconomic, commodity, technology, and geopolitical news.
- Only source-backed deterministic rules may change the AI result.
- Repairable defects use the existing maximum of three later-cycle retries; permanent low-value rejections do not retry.

## File Structure

- Modify `news_selector.py`: urgency normalization, high-confidence low-value rules, source-gated Korean normalization, confidence/metric validation, and semantic duplicate signature.
- Modify `tests/test_news_selector.py`: all new unit and regression coverage.
- No change to `gnews_tracker.py`: its existing `invalid_fields` retry handling remains the interface for repairable failures.

---

### Task 1: Confirmed systemic-event urgency correction

**Files:**
- Modify: `news_selector.py:1247-1471`
- Test: `tests/test_news_selector.py`

**Interfaces:**
- Consumes: normalized GNews article dictionaries and integer AI `importance_score` values.
- Produces: `_has_speculative_language(value: str) -> bool`, `_has_confirmed_systemic_event(source_text: str) -> bool`, and the existing `_normalize_importance_score(article: dict, importance_score: int) -> int` with conservative score floors and caps.

- [ ] **Step 1: Write failing urgency tests**

Add tests that run `select_and_summarize` with AI scores below or above the desired boundary:

```python
def test_upgrades_confirmed_joint_fx_intervention_to_urgent(self):
    source = article(
        "confirmed-joint-intervention",
        "US and Japan conduct joint yen-buying intervention",
        "Officials confirmed direct foreign-exchange intervention.",
        "https://example.com/confirmed-joint-intervention",
    )
    selected = select_and_summarize([source], FakeGenerator(decision_json(score=8)))
    self.assertEqual(selected[0]["importance_score"], 9)

def test_does_not_upgrade_speculative_joint_intervention_report(self):
    source = article(
        "possible-joint-intervention",
        "US and Japan may have intervened to support yen",
        "Traders estimated that a joint intervention could have occurred.",
        "https://example.com/possible-joint-intervention",
    )
    selected = select_and_summarize([source], FakeGenerator(decision_json(score=8)))
    self.assertEqual(selected[0]["importance_score"], 8)

def test_caps_ai_data_constraint_trend_below_urgent(self):
    source = article(
        "china-ai-data-constraint",
        "China AI progress constrained by data shortage",
        "The article describes a broad industry trend without a new action.",
        "https://example.com/china-ai-data-constraint",
    )
    selected = select_and_summarize([source], FakeGenerator(decision_json(score=9)))
    self.assertEqual(selected[0]["importance_score"], 8)
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
python -m unittest tests.test_news_selector.NewsSelectorTests.test_upgrades_confirmed_joint_fx_intervention_to_urgent tests.test_news_selector.NewsSelectorTests.test_does_not_upgrade_speculative_joint_intervention_report tests.test_news_selector.NewsSelectorTests.test_caps_ai_data_constraint_trend_below_urgent
```

Expected: the confirmed intervention remains 8 before implementation; the two safety cases pass or expose missing protection.

- [ ] **Step 3: Implement the minimum urgency helpers**

Add a narrow helper and call it before the current `importance_score < 9` early return:

```python
def _has_speculative_language(value: str) -> bool:
    normalized = value.casefold()
    return any(marker in normalized for marker in SPECULATIVE_EVENT_MARKERS)


def _has_confirmed_systemic_event(source_text: str) -> bool:
    if _has_speculative_language(source_text):
        return False
    return any(
        all(marker in source_text for marker in required_markers)
        for required_markers in CONFIRMED_SYSTEMIC_EVENT_PATTERNS
    )
```

Use explicit pattern groups for direct/joint FX intervention, emergency central-bank action, market-wide trading halt, and sovereign default. Do not match general commentary containing only `intervention`, `emergency`, or `default`.

- [ ] **Step 4: Run urgency tests and the existing scoring tests**

Run:

```powershell
python -m unittest tests.test_news_selector.NewsSelectorTests.test_upgrades_confirmed_joint_fx_intervention_to_urgent tests.test_news_selector.NewsSelectorTests.test_does_not_upgrade_speculative_joint_intervention_report tests.test_news_selector.NewsSelectorTests.test_caps_ai_data_constraint_trend_below_urgent
python -m unittest tests.test_news_selector
```

Expected: PASS, including existing ordinary-earnings and trend-cap tests.

- [ ] **Step 5: Commit the urgency correction**

```powershell
git add news_selector.py tests/test_news_selector.py
git commit -m "fix: correct confirmed systemic news urgency"
```

---

### Task 2: High-confidence non-event filtering

**Files:**
- Modify: `news_selector.py:464-935`
- Test: `tests/test_news_selector.py`

**Interfaces:**
- Consumes: source `raw_title`, `raw_description`, and `raw_content` fields.
- Produces: the existing `_is_low_value_item(article: dict) -> bool`, returning `True` only for a clearly non-material pattern.

- [ ] **Step 1: Write failing low-value tests with positive counterexamples**

Cover the observed patterns as a table-driven test:

```python
def test_rejects_clear_non_event_analysis_but_keeps_concrete_developments(self):
    rejected = [
        article("stock-return-comparison", "Broadcom 6-month return versus Nvidia", "A backward-looking stock comparison.", "https://example.com/comparison"),
        article("business-model-explainer", "How McDonald's real-estate business works", "An evergreen explainer with no new transaction or result.", "https://example.com/explainer"),
        article("soft-buyer-interest", "Middle East buyers show interest in Canadian LNG", "No contract, tender, or investment has been announced.", "https://example.com/interest"),
        article("market-preview", "Metals rally faces a test from future risks", "A market outlook with no newly reported action or figure.", "https://example.com/preview"),
        article("analyst-platform-dispute", "OpenAI blocks a bitcoin analyst", "The analyst moved to another chatbot after an account dispute.", "https://example.com/dispute"),
        article("minor-airport-incident", "Aircraft narrowly avoid collision at Sydney airport", "No service disruption, financial impact, or regulatory action followed.", "https://example.com/airport"),
    ]
    kept = [
        article("lng-contract", "Buyer signs Canadian LNG supply contract", "A binding 20-year contract was signed.", "https://example.com/contract"),
        article("airport-disruption", "Airport closes after collision and airlines cancel 200 flights", "The closure disrupted a major transport hub.", "https://example.com/disruption"),
    ]
    selected = select_and_summarize(rejected + kept, generator_for(kept))
    self.assertEqual([item["provider_article_id"] for item in selected], ["lng-contract", "airport-disruption"])
```

- [ ] **Step 2: Run the focused low-value test and verify RED**

Run:

```powershell
python -m unittest tests.test_news_selector.NewsSelectorTests.test_rejects_clear_non_event_analysis_but_keeps_concrete_developments
```

Expected: at least one rejected fixture still reaches the AI generator before implementation.

- [ ] **Step 3: Add conjunction-based rules to `_is_low_value_item`**

Each rule must require both a non-event form and the absence of a material event marker:

```python
material_event = any(marker in text for marker in MATERIAL_EVENT_MARKERS)
if _is_backward_looking_stock_comparison(title, text) and not material_event:
    return True
if _is_evergreen_business_explainer(title, text) and not material_event:
    return True
if _is_soft_interest_story(title, text) and not material_event:
    return True
```

Use equivalent conjunctions for previews, isolated platform disputes, and operational incidents. Include deal, contract, investment, earnings, official action, shutdown, cancellations, supply disruption, and material quantified impact in `MATERIAL_EVENT_MARKERS` so legitimate developments remain selectable.

- [ ] **Step 4: Run the low-value and full selector tests**

Run:

```powershell
python -m unittest tests.test_news_selector.NewsSelectorTests.test_rejects_clear_non_event_analysis_but_keeps_concrete_developments
python -m unittest tests.test_news_selector
```

Expected: PASS with existing broad company and market news retained.

- [ ] **Step 5: Commit the low-value filters**

```powershell
git add news_selector.py tests/test_news_selector.py
git commit -m "fix: filter clear economic news non-events"
```

---

### Task 3: Source-gated Korean wording and repairable summary validation

**Files:**
- Modify: `news_selector.py:1004-1245`
- Modify: `news_selector.py:2308-2407`
- Test: `tests/test_news_selector.py`

**Interfaces:**
- Consumes: source article plus generated Korean `title` and `content`.
- Produces: source-gated output from `_normalize_known_korean_terms`, and quality failures `confidence_mismatch` or `missing_primary_metric` returned through `SelectionResult.invalid_fields`.

- [ ] **Step 1: Write failing normalization tests**

```python
def test_normalizes_employment_data_and_share_repurchase_only_with_source_support(self):
    employment = article("jobs", "US employment data weaken", "Payroll growth slowed.", "https://example.com/jobs")
    buyback = article("buyback", "HG completes share repurchase", "The company completed its buyback.", "https://example.com/buyback")
    decisions = [
        decision(employment, title="미국 취업 데이터 약화", content="미국 취업 데이터가 둔화됐습니다."),
        decision(buyback, title="HG, 주식 매수 회수 완료", content="HG가 주식 매수 회수를 완료했습니다."),
    ]
    selected = select_and_summarize([employment, buyback], FakeGenerator(json.dumps(decisions, ensure_ascii=False)))
    self.assertIn("고용지표", selected[0]["normalized_title"])
    self.assertIn("자사주 매입", selected[1]["normalized_title"])
```

- [ ] **Step 2: Write failing confidence and metric tests**

```python
def test_retries_definitive_title_when_source_and_summary_are_speculative(self):
    source = article("possible-intervention", "US may have intervened in yen market", "Dealers estimated intervention could have occurred.", "https://example.com/possible")
    result = select_and_summarize([source], FakeGenerator(decision_json(title="미국, 엔화 방어 위해 시장 개입", content="미국이 개입한 것으로 추정됩니다.")), include_failures=True)
    self.assertEqual(result.selected, [])
    self.assertEqual(result.invalid_fields[0]["reason"], "confidence_mismatch")

def test_retries_quantified_trend_summary_that_omits_every_source_metric(self):
    source = article("marvell-results", "Marvell revenue rises 42% to $2.0 billion", "Quarterly revenue rose 42% to $2.0 billion.", "https://example.com/marvell")
    result = select_and_summarize([source], FakeGenerator(decision_json(title="마벨, 분기 실적 개선", content="마벨의 분기 실적이 증가했습니다.")), include_failures=True)
    self.assertEqual(result.selected, [])
    self.assertEqual(result.invalid_fields[0]["reason"], "missing_primary_metric")
```

- [ ] **Step 3: Run the four focused tests and verify RED**

Run the new test methods with `python -m unittest`; expect unchanged mistranslations and accepted invalid summaries before implementation.

- [ ] **Step 4: Implement source-gated normalization and validators**

Extend `_normalize_known_korean_terms` only when source markers are present:

```python
if any(marker in source_text for marker in ("employment data", "jobs data", "labor market data")):
    value = value.replace("취업 데이터", "고용지표")
if any(marker in source_text for marker in ("share repurchase", "stock repurchase", "buyback")):
    value = value.replace("주식 매수 회수", "자사주 매입")
```

Add `_has_confidence_mismatch(article, title, content) -> bool` and `_is_missing_primary_metric(article, title, content) -> bool`. Call them from `_decision_to_item` after normalization and before the item is accepted. The metric rule activates only when the generated copy makes a rise/fall/growth/decline claim, the source contains a central numeric metric, and both Korean fields omit every source-backed metric.

- [ ] **Step 5: Run focused tests, retry-flow tests, and full selector tests**

Run:

```powershell
python -m unittest tests.test_news_selector.NewsSelectorTests.test_normalizes_employment_data_and_share_repurchase_only_with_source_support tests.test_news_selector.NewsSelectorTests.test_retries_definitive_title_when_source_and_summary_are_speculative tests.test_news_selector.NewsSelectorTests.test_retries_quantified_trend_summary_that_omits_every_source_metric
python -m unittest tests.test_gnews_worker
python -m unittest tests.test_news_selector
```

Expected: PASS; `invalid_fields` remains compatible with the existing bounded retry queue.

- [ ] **Step 6: Commit wording and summary validation**

```powershell
git add news_selector.py tests/test_news_selector.py
git commit -m "fix: retry unsupported news wording and summaries"
```

---

### Task 4: Five-bank overdraft delinquency duplicate signature

**Files:**
- Modify: `news_selector.py:1917-2011`
- Test: `tests/test_news_selector.py`

**Interfaces:**
- Consumes: pairs of normalized titles and contents through `_has_shared_event_signature`.
- Produces: `_is_same_overdraft_delinquency_release(first_text: str, second_text: str) -> bool`; existing deduplication keeps the richer item.

- [ ] **Step 1: Write a failing duplicate test and a period-change counterexample**

```python
def test_deduplicates_same_five_bank_overdraft_delinquency_release(self):
    sources = overdraft_sources_for_same_release()
    selected = select_and_summarize(sources, FakeGenerator(overdraft_decisions_json()))
    self.assertEqual([item["provider_article_id"] for item in selected], ["five-bank-complete"])

def test_keeps_overdraft_delinquency_reports_for_different_periods(self):
    sources = overdraft_sources_for_june_and_july()
    selected = select_and_summarize(sources, FakeGenerator(overdraft_period_decisions_json()))
    self.assertEqual(len(selected), 2)
```

Fixtures must include the observed angles `청년·고령층 마이너스 통장 연체율 급증` and `5대 은행 마이너스통장 연체율 0.22%로 상승`; the richer item includes institution set, period, and figures.

- [ ] **Step 2: Run the two duplicate tests and verify RED**

Run both new methods with `python -m unittest`; expect the same-release test to return two items before implementation.

- [ ] **Step 3: Implement a conservative report signature**

```python
def _is_same_overdraft_delinquency_release(first_text: str, second_text: str) -> bool:
    if not all(_has_overdraft_delinquency_terms(value) for value in (first_text, second_text)):
        return False
    if not all(_has_five_major_bank_context(value) for value in (first_text, second_text)):
        return False
    if _has_explicit_event_dimension_conflict(first_text, second_text):
        return False
    return bool(_event_numeric_tokens(first_text) & _event_numeric_tokens(second_text))
```

Call the helper from `_has_shared_event_signature`. Reuse existing event-dimension conflict handling so month, quarter, and year differences stay distinct.

- [ ] **Step 4: Run duplicate tests and the entire selector module**

Run:

```powershell
python -m unittest tests.test_news_selector.NewsSelectorTests.test_deduplicates_same_five_bank_overdraft_delinquency_release tests.test_news_selector.NewsSelectorTests.test_keeps_overdraft_delinquency_reports_for_different_periods
python -m unittest tests.test_news_selector
```

Expected: PASS without merging distinct labor, earnings, or bank reports.

- [ ] **Step 5: Commit the duplicate signature**

```powershell
git add news_selector.py tests/test_news_selector.py
git commit -m "fix: merge matching overdraft delinquency reports"
```

---

### Task 5: Full regression verification

**Files:**
- Verify: `news_selector.py`
- Verify: `gnews_tracker.py`
- Verify: `gnews_adapter.py`
- Verify: `tests/test_*.py`

**Interfaces:**
- Consumes: all completed tasks.
- Produces: a syntax-clean, regression-tested backend with no policy-category, schema, frontend, request-count, or schedule changes.

- [ ] **Step 1: Compile all touched production and test modules**

```powershell
python -m py_compile news_selector.py gnews_tracker.py gnews_adapter.py tests/test_news_selector.py tests/test_gnews_worker.py tests/test_gnews_tracker.py
```

Expected: exit code 0.

- [ ] **Step 2: Run the complete test suite**

```powershell
python -m unittest discover -s tests -p "test_*.py"
```

Expected: all tests pass.

- [ ] **Step 3: Verify scope and whitespace**

```powershell
git diff --check
git diff --stat HEAD~4..HEAD
git status --short
```

Expected: only `news_selector.py`, `tests/test_news_selector.py`, and these approved design/plan documents changed; no policy-category, schema, frontend, adapter request-count, or schedule edits.

- [ ] **Step 4: Record final verification if any uncommitted cleanup was required**

If verification required a test-only correction, stage exactly the touched selector/test files and commit it:

```powershell
git add news_selector.py tests/test_news_selector.py
git commit -m "test: finalize news quality regressions"
```
