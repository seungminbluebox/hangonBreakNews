# News Quality Retry and Policy Category Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve broad economic-news coverage while repairing malformed AI output, retrying transient quality failures without starving fresh news, deduplicating only high-confidence same-event articles, calibrating urgency, and adding the `policy` category.

**Architecture:** Keep the existing synchronous five-minute GNews worker and `breaking_news` row contract. Extend `news_selector.py` with conservative validators and a list-compatible `SelectionResult` carrying retryable URLs; extend `gnews_tracker.py` with process-local retry counters and fresh-first batching. No database field, canonical table, concurrent worker, or mandatory second AI reviewer is added.

**Tech Stack:** Python 3.12+, `unittest`, existing OpenRouter generator abstraction, Supabase client, PM2, Next.js frontend handoff documentation.

## Global Constraints

- Keep `breaking_news` columns unchanged: `title`, `content`, `importance_score`, `category`, and `original_url`.
- Keep the five-minute non-overlapping single-threaded cycle.
- Keep GNews request regions, request volume, notification preference keys, and score-based push routing unchanged.
- Process fresh candidates before carried quality retries.
- Permit one focused repair in the first cycle and at most two later-cycle attempts: three retries after the initial selection call.
- Process at most ten carried quality failures per cycle.
- Treat uncertain event similarity as distinct; only high-confidence matches are duplicates.
- Do not add a broad topic-keyword exclusion list.
- Add `policy` without assuming a database migration; document the live constraint check.

---

## File Structure

- Modify `news_selector.py`: category contract, title/content validators, focused repair outcome, semantic event signatures, and importance cap.
- Modify `gnews_tracker.py`: process-local quality retry state, fresh-first ordering, bounded retries, and retry statistics.
- Modify `tests/test_news_selector.py`: selector-level red/green regression coverage.
- Modify `tests/test_gnews_worker.py`: worker retry lifecycle and ordering coverage.
- Modify `tests/test_gnews_tracker.py`: structured-output contract coverage where the production generator configuration is asserted.
- Modify `readme.md`: operational behavior, category list, and retry semantics.
- Create `docs/frontend-policy-category-handoff.md`: copyable frontend implementation prompt.

---

### Task 1: Add the `policy` Category Contract

**Files:**
- Modify: `tests/test_news_selector.py`
- Modify: `tests/test_gnews_worker.py`
- Modify: `news_selector.py`
- Verify: `gnews_tracker.py`

**Interfaces:**
- Produces: `SELECTABLE_CATEGORIES` containing `policy`.
- Produces: `NEWS_SELECTION_RESPONSE_FORMAT` with `policy` in its category enum and title `maxLength` set to `55`.
- Preserves: `to_breaking_news_row(news_item: dict) -> dict` category pass-through.

- [ ] **Step 1: Write failing selector contract tests**

Add tests that assert the schema and prompt accept `policy` and define its boundary:

```python
def test_allows_policy_category_and_defines_its_boundary(self):
    source = article(
        "policy-tax-law",
        "Government introduces industry-wide production tax credit",
        "The new tax credit applies to six strategic industries.",
        "https://example.com/policy-tax-law",
    )
    response = """[{
        "temp_id": 0,
        "source_ref": "policy-tax-law",
        "source_title": "Government introduces industry-wide production tax credit",
        "title": "정부, 전략산업 국내생산 세액공제 도입",
        "content": "정부가 6대 전략산업에 적용되는 국내생산 세액공제를 도입했습니다.",
        "importance_score": 8,
        "category": "policy",
        "news_type": "official_announcement",
        "selection_reason": "여러 전략산업에 적용되는 세제 정책이 발표됐습니다."
    }]"""

    generator = FakeGenerator(response)
    selected = select_and_summarize([source], generator)

    self.assertEqual(selected[0]["category"], "policy")
    self.assertIn("`policy`", generator.prompts[0])
    self.assertIn("특정 기업만 대상으로 한", generator.prompts[0])
```

Also assert:

```python
def test_response_schema_allows_complete_titles_up_to_fifty_five_characters(self):
    title_schema = NEWS_SELECTION_RESPONSE_FORMAT["json_schema"]["schema"][
        "items"
    ]["properties"]["title"]
    self.assertEqual(title_schema["maxLength"], 55)
    self.assertIn("policy", SELECTABLE_CATEGORIES)
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
python -m unittest tests.test_news_selector.NewsSelectorTests.test_allows_policy_category_and_defines_its_boundary tests.test_news_selector.NewsSelectorTests.test_response_schema_allows_complete_titles_up_to_fifty_five_characters
```

Expected: failure because `policy` is absent and `maxLength` is `35`.

- [ ] **Step 3: Implement the minimal category and schema change**

Change:

```python
SELECTABLE_CATEGORIES = {
    "market",
    "indicator",
    "geopolitics",
    "corporate",
    "policy",
}
```

Set title `maxLength` to `55`. Update `_selection_prompt` so:

```text
policy: 법률·세제·정부 정책과 시장·산업·다수 기업 또는 소비자에게 적용되는 규제
corporate: 기업 실적·인수합병·기술·공급망·소송과 특정 기업만 대상으로 한 규제 집행
market: 주식·채권·외환·원자재·가상자산 시장과 중앙은행 통화정책
```

Update the output category union to include `policy`.

- [ ] **Step 4: Add and run storage pass-through coverage**

Add to `tests/test_gnews_worker.py`:

```python
def test_maps_policy_category_to_existing_breaking_news_row(self):
    item = selected(article())
    item["category"] = "policy"

    row = to_breaking_news_row(item)

    self.assertEqual(row["category"], "policy")
    self.assertEqual(
        set(row),
        {"title", "content", "importance_score", "category", "original_url"},
    )
```

Run the focused selector and worker tests. Expected: PASS.

- [ ] **Step 5: Commit Task 1**

```powershell
git add news_selector.py tests/test_news_selector.py tests/test_gnews_worker.py
git commit -m "feat: add policy news category"
```

---

### Task 2: Add Conservative Structural and Direction Validators

**Files:**
- Modify: `tests/test_news_selector.py`
- Modify: `news_selector.py`

**Interfaces:**
- Produces: `_has_incomplete_title(title: str, content: str) -> bool`.
- Produces: `_has_opposite_title_content_direction(title: str, content: str) -> bool`.
- Produces: `_has_malformed_korean_amount(text: str) -> bool`.
- Extends: `_normalize_known_korean_terms(article: dict, value: str) -> str` with source-gated mappings.

- [ ] **Step 1: Write failing title-integrity tests**

Add table-driven tests covering these invalid titles:

```python
cases = (
    ("파키스탄 핀테크, AI 도입을위", "파키스탄 핀테크가 AI 도입을 위한 계약을 체결했습니다."),
    ("미국, 프린스 그룹 관련자 제", "미국 정부가 프린스 그룹 관련자들을 제재했습니다."),
    ("세제개편으로 서민·중산층 세부담 1", "서민·중산층의 세부담이 1조2천258억원 감소합니다."),
    ("강남 아파트값 상승폭 0.09%로", "강남 아파트값 상승폭이 0.09%로 둔화했습니다."),
)
```

For each case, create a source-backed AI decision and assert the item is absent from the selected list. Add valid controls such as `S&P 500` and `신차 7종 출시` so trailing numbers are not rejected by themselves.

- [ ] **Step 2: Run title tests and verify RED**

Expected: at least the newly listed endings pass current validation unexpectedly.

- [ ] **Step 3: Implement conservative structural checks**

Update `_has_incomplete_title` to combine unambiguous checks:

```python
def _has_incomplete_title(title: str, content: str) -> bool:
    normalized = title.strip().rstrip(".!?…")
    if _has_unbalanced_title_delimiters(normalized):
        return True
    if re.search(r"(?:을위|를위|관련자\s+[가-힣])$", normalized):
        return True
    if re.search(r"\d(?:[\d,.]*%?)\s*(?:로|에|에서|까지|부터|보다)$", normalized):
        return True
    return _has_truncated_numeric_prefix(normalized, content)
```

Keep the existing incomplete-ending list, but do not reject a title merely for containing a topic word or ending in a valid noun/number.

- [ ] **Step 4: Write failing direction tests**

Add:

```python
def test_marks_title_down_summary_up_as_invalid(self):
    source = article(
        "opposite-market-direction",
        "US stocks rise after employment report",
        "The major US indexes rose after the report.",
        "https://example.com/opposite-market-direction",
    )
    response = """[{
        "temp_id": 0,
        "source_ref": "opposite-market-direction",
        "source_title": "US stocks rise after employment report",
        "title": "미국 증시, 고용지표 발표 후 하락",
        "content": "미국 주요 지수가 고용지표 발표 후 상승했습니다.",
        "importance_score": 8,
        "category": "market",
        "news_type": "new_development",
        "selection_reason": "고용지표 발표 후 미국 증시가 움직였습니다."
    }]"""

    result = select_and_summarize([source], FakeGenerator(response))

    self.assertEqual(result, [])

def test_keeps_mixed_metric_earnings_when_title_direction_is_supported(self):
    source = article(
        "mixed-earnings-direction",
        "Company revenue rises while profit falls",
        "Quarterly revenue increased, but net profit decreased.",
        "https://example.com/mixed-earnings-direction",
    )
    response = """[{
        "temp_id": 0,
        "source_ref": "mixed-earnings-direction",
        "source_title": "Company revenue rises while profit falls",
        "title": "기업 매출 증가, 순이익은 감소",
        "content": "기업의 분기 매출은 증가했지만 순이익은 감소했습니다.",
        "importance_score": 7,
        "category": "corporate",
        "news_type": "official_announcement",
        "selection_reason": "기업이 상반된 방향의 분기 실적을 발표했습니다."
    }]"""

    result = select_and_summarize([source], FakeGenerator(response))

    self.assertEqual(
        [item["provider_article_id"] for item in result],
        ["mixed-earnings-direction"],
    )
```

The first must produce no selected article; the second must remain selected.

- [ ] **Step 5: Run direction tests and verify RED**

Expected: the opposite-direction item is currently selected.

- [ ] **Step 6: Implement normalized direction support**

Use narrow canonical groups:

```python
DIRECTION_MARKERS = {
    "up": ("상승", "증가", "급증", "확대", "상향"),
    "down": ("하락", "감소", "급감", "축소", "하향"),
    "approved": ("승인", "허가", "통과"),
    "rejected": ("거절", "불허", "기각"),
    "profit": ("흑자", "순이익"),
    "loss": ("적자", "순손실"),
}
```

Reject only when a direction asserted in the title is unsupported in the content and the content contains its explicit opposite. Do not reject when the content also supports the title direction.

- [ ] **Step 7: Write failing translation and malformed-number tests**

Cover source-gated normalization:

```python
self.assertEqual(
    selected_item["normalized_title"],
    "호르무즈 갈등으로 셰브론 옵션 매수 전략",
)
self.assertIn("액화석유가스(LPG)", selected_item["normalized_content"])
```

Cover `1억4천만8백만 싱가포르달러` as invalid while valid `1억4천8백만 싱가포르달러` remains valid.

- [ ] **Step 8: Implement source-gated normalization and numeric grammar check**

Only normalize `체비론` when the raw source contains `Chevron`, and `액화석유가` when the raw source contains `LPG` or `liquefied petroleum gas`. Detect repeated Korean large-number units with a parser or regex that distinguishes valid nested units from duplicate `만`/`억` sequences.

- [ ] **Step 9: Run the full selector suite and commit Task 2**

Run:

```powershell
python -m unittest tests.test_news_selector
```

Expected: PASS.

```powershell
git add news_selector.py tests/test_news_selector.py
git commit -m "fix: validate generated news titles and summaries"
```

---

### Task 3: Return Repairable Quality Outcomes and Repair Once

**Files:**
- Modify: `tests/test_news_selector.py`
- Modify: `news_selector.py`

**Interfaces:**
- Produces: `SelectionResult(list)` with read-only `retryable_urls: frozenset[str]`.
- Produces: `_quality_repair_prompt(candidates: list[dict]) -> str`.
- Preserves: callers may iterate, index, compare, and take `len()` as with the previous list return value.

- [ ] **Step 1: Write a failing list-compatibility and retry metadata test**

Add:

```python
def test_marks_unrepaired_selected_quality_failure_retryable(self):
    source = article(
        "retry-title",
        "Company signs agreement to introduce AI",
        "The company signed a binding agreement to introduce AI.",
        "https://example.com/retry-title",
    )
    broken_response = """[{
        "temp_id": 0,
        "source_ref": "retry-title",
        "source_title": "Company signs agreement to introduce AI",
        "title": "기업, AI 도입을위",
        "content": "기업이 AI 도입을 위한 구속력 있는 계약을 체결했습니다.",
        "importance_score": 7,
        "category": "corporate",
        "news_type": "official_announcement",
        "selection_reason": "기업이 AI 도입 계약을 체결했습니다."
    }]"""
    generator = FakeGenerator([broken_response, broken_response])

    result = select_and_summarize([source], generator)

    self.assertEqual(result, [])
    self.assertEqual(result.retryable_urls, frozenset({source["original_url"]}))
    self.assertEqual(len(generator.prompts), 2)
    self.assertIn("제목과 요약의 품질 오류만 수정", generator.prompts[1])
```

- [ ] **Step 2: Run the metadata test and verify RED**

Expected: plain list has no `retryable_urls`, and no focused repair prompt is issued.

- [ ] **Step 3: Add `SelectionResult` and isolate decision validation**

Implement:

```python
class SelectionResult(list):
    def __init__(self, items=(), *, retryable_urls=()):
        super().__init__(items)
        self.retryable_urls = frozenset(retryable_urls)
```

Extract the current decision-to-item checks into a private helper that returns either a normalized item or a named failure reason. A decision with a valid candidate identity but failing output quality is repairable. An omitted candidate remains an intentional rejection.

- [ ] **Step 4: Implement one focused repair call per affected batch**

The repair prompt must include the source article, the rejected draft, and the named validation reason. It must require the same `temp_id`, `source_ref`, and `source_title`, and return the existing JSON-array schema. Validate repaired decisions through the same helper without recursively triggering another repair.

If the focused repair succeeds, return the article normally. If it fails, include the source URL in `retryable_urls`. Catch transport, empty-response, and malformed-response failures from the focused repair so one bad article does not fail the whole primary batch.

- [ ] **Step 5: Add successful repair coverage**

Use a two-response `FakeGenerator`: first response contains a truncated title, second response contains a complete corrected title. Assert the article is selected, `retryable_urls` is empty, and the corrected title is stored.

- [ ] **Step 6: Preserve primary JSON failure behavior**

Run existing JSON repair and batch retry tests. A primary selection transport/JSON failure must still raise so `gnews_tracker` retains the entire batch. Only focused quality-repair failure becomes per-article retry metadata.

- [ ] **Step 7: Run selector tests and commit Task 3**

```powershell
python -m unittest tests.test_news_selector
git add news_selector.py tests/test_news_selector.py
git commit -m "feat: retry repairable news summaries"
```

---

### Task 4: Add Fresh-First Bounded Retry State to the Worker

**Files:**
- Modify: `tests/test_gnews_worker.py`
- Modify: `gnews_tracker.py`

**Interfaces:**
- Extends: `TrackerState` with `quality_retry_counts: dict[str, int]`.
- Adds: `MAX_QUALITY_RETRIES = 3` and `MAX_CARRIED_QUALITY_RETRIES_PER_CYCLE = 10`.
- Consumes: `SelectionResult.retryable_urls` while remaining compatible with mocked selectors returning plain lists.

- [ ] **Step 1: Write failing pending-vs-evaluated lifecycle tests**

Add tests that return a `SelectionResult([], retryable_urls={url})` and assert:

```python
self.assertIn(url, state.pending)
self.assertNotIn(url, state.evaluated_urls)
self.assertEqual(state.quality_retry_counts[url], 1)
```

Keep the existing intentional-rejection test asserting a plain empty list is evaluated and not retried.

- [ ] **Step 2: Run lifecycle tests and verify RED**

Expected: current worker removes the retryable URL from pending and marks it evaluated.

- [ ] **Step 3: Implement retry state and result adaptation**

Add constants and state:

```python
MAX_QUALITY_RETRIES = 3
MAX_CARRIED_QUALITY_RETRIES_PER_CYCLE = 10

quality_retry_counts: dict[str, int] = field(default_factory=dict)
```

Read retry metadata with `getattr(selected_articles, "retryable_urls", frozenset())` so current tests and injected selector functions returning plain lists remain valid.

- [ ] **Step 4: Write failing fresh-first ordering test**

Preload one retry URL in state, fetch one fresh URL, set `batch_size=1`, and record selector calls. Assert the first selector batch contains the fresh URL and the second contains the retry URL.

- [ ] **Step 5: Implement fresh-first batching and retry cap**

Build two sequences from pending:

```python
fresh_articles = [
    item for url, item in state.pending.items()
    if url not in state.quality_retry_counts
]
carried_retries = [
    item for url, item in state.pending.items()
    if url in state.quality_retry_counts
][:MAX_CARRIED_QUALITY_RETRIES_PER_CYCLE]
```

Process every fresh batch first and only the bounded carried list afterward. Unprocessed carried retries remain pending untouched.

- [ ] **Step 6: Write and implement retry exhaustion coverage**

Across cycles, return retryable metadata three times. Assert attempts one and two remain pending. On the third unresolved retry, assert the URL is removed from pending, added to evaluated, and removed from `quality_retry_counts`. Add `quality_retries` and `quality_retry_exhausted` to cycle statistics and log output.

On save, exact DB duplicate, or intentional rejection, clear any stale retry counter for that URL.

- [ ] **Step 7: Run worker tests and commit Task 4**

```powershell
python -m unittest tests.test_gnews_worker
git add gnews_tracker.py tests/test_gnews_worker.py
git commit -m "feat: retry quality failures without blocking fresh news"
```

---

### Task 5: Strengthen High-Confidence Event Deduplication

**Files:**
- Modify: `tests/test_news_selector.py`
- Modify: `news_selector.py`

**Interfaces:**
- Produces: `_event_concept_tokens(value: str) -> set[str]`.
- Extends: `_has_shared_event_signature`, `_is_same_event`, and recent-news duplicate comparison.

- [ ] **Step 1: Write failing same-release labor-market tests**

Create three articles for the same US July labor release using `고용지표`, `실업률`, and `일자리` angles. Give them the same country, reporting month, close publication time, and shared source-backed figure. Assert only the most complete result remains.

- [ ] **Step 2: Write ambiguity-preservation tests**

Create labor stories with a different country, reporting month, or key number and assert both remain. Add a material follow-up with a revised official figure and assert it remains alongside the original.

- [ ] **Step 3: Run focused duplicate tests and verify RED**

Expected: current lexical overlap misses at least one same-release variant.

- [ ] **Step 4: Implement conservative concept normalization**

Map only stable equivalents, for example:

```python
EVENT_CONCEPT_PATTERNS = {
    "labor_release": (
        r"고용\s*지표",
        r"실업률",
        r"일자리",
        r"jobs? report",
        r"unemployment",
    ),
}
```

Require the concept plus actor/geography and period or key-number agreement. Never deduplicate on the concept alone. Continue to call `_has_material_follow_up` before removing a related article.

- [ ] **Step 5: Run all duplicate tests and commit Task 5**

```powershell
python -m unittest tests.test_news_selector.NewsSelectorTests
git add news_selector.py tests/test_news_selector.py
git commit -m "fix: cluster same economic release across headline angles"
```

---

### Task 6: Recalibrate Trend-Only Urgency Without Excluding News

**Files:**
- Modify: `tests/test_news_selector.py`
- Modify: `news_selector.py`

**Interfaces:**
- Extends: `_normalize_importance_score(article: dict, importance_score) -> int | float`.
- Preserves: every selected article remains eligible for score 7 or 8 storage.

- [ ] **Step 1: Write a failing vague-trend cap test**

Use a source such as `China AI development constrained by data shortage` with no new binding action, official decision, exceptional quantified shock, or immediate market move. Return AI importance `9` and assert stored importance is `8`.

- [ ] **Step 2: Write a systemic-event control test**

Use a concrete central-bank emergency action, government removal decision affecting monetary-policy governance, or abrupt market-wide shock with source-backed action and magnitude. Assert a supplied score of `9` remains `9`.

- [ ] **Step 3: Run focused tests and verify RED**

Expected: vague trend remains `9` under current normalization.

- [ ] **Step 4: Implement a cap, not an exclusion**

Detect trend/constraint/outlook framing together with the absence of concrete action and systemic markers. Return `min(score, 8)`. Update the prompt to state that broad analysis may remain a major economic item but is not urgent without a new consequential action or immediate shock.

- [ ] **Step 5: Run selector tests and commit Task 6**

```powershell
python -m unittest tests.test_news_selector
git add news_selector.py tests/test_news_selector.py
git commit -m "fix: reserve urgent scores for concrete market events"
```

---

### Task 7: Document Operations and Create the Frontend Handoff Prompt

**Files:**
- Modify: `readme.md`
- Create: `docs/frontend-policy-category-handoff.md`

**Interfaces:**
- Produces: copyable frontend task prompt.
- Documents: no routine SQL migration, live constraint verification, deployment order, retry semantics, and unchanged Pulse/notification contracts.

- [ ] **Step 1: Write the frontend handoff document**

The prompt must instruct the frontend worker to modify `app/live/page.tsx` by adding:

```typescript
{ id: "policy", label: "정책/규제" },
```

It must require tests or checks for default selection, reset selection, manual filtering, Supabase realtime inserts, share-card rendering, legacy unknown categories, and importance-based labels. It must explicitly prohibit changing notification keys or using category to determine urgent status.

- [ ] **Step 2: Update backend operations documentation**

Document:

- `policy` is a new allowed string value;
- the repository contains no category CHECK migration;
- the operator must inspect the live schema before backend deployment;
- if a live constraint exists, add `policy` through a separate reviewed migration;
- deploy frontend support before or together with backend output;
- quality failures are retried in memory and no retry metadata is stored in the database.

- [ ] **Step 3: Run documentation and diff checks**

```powershell
rg -n "policy|정책/규제|MAX_QUALITY_RETRIES|재시도" readme.md docs/frontend-policy-category-handoff.md
git diff --check
```

- [ ] **Step 4: Commit Task 7**

```powershell
git add readme.md docs/frontend-policy-category-handoff.md
git commit -m "docs: add policy category frontend handoff"
```

---

### Task 8: Full Regression and Compatibility Verification

**Files:**
- Verify: `news_selector.py`
- Verify: `gnews_tracker.py`
- Verify: `gnews_adapter.py`
- Verify: `tests/`
- Verify: `readme.md`
- Verify: `docs/frontend-policy-category-handoff.md`

**Interfaces:**
- Confirms: existing GNews adapter, DB row, Pulse reader, and notification contracts are unchanged.

- [ ] **Step 1: Compile modified Python files**

```powershell
python -m py_compile news_selector.py gnews_tracker.py gnews_adapter.py tests/test_news_selector.py tests/test_gnews_worker.py tests/test_gnews_tracker.py
```

Expected: exit code `0`.

- [ ] **Step 2: Run focused suites**

```powershell
python -m unittest tests.test_news_selector tests.test_gnews_worker tests.test_gnews_tracker
```

Expected: PASS.

- [ ] **Step 3: Run the entire project test suite**

```powershell
python -m unittest discover -s tests -p "test_*.py"
```

Expected: all tests PASS. If the local interpreter lacks `requests` or `python-dotenv`, install only those packages into a temporary verification directory, set `PYTHONPATH` for the test process, and delete that exact validated temporary directory afterward.

- [ ] **Step 4: Verify unchanged contracts**

Confirm:

```powershell
git diff --numstat -- gnews_adapter.py push_notification.py revalidate.py
git diff --check
git status --short
```

Expected: no adapter, push, or revalidation changes; only planned source, tests, and docs are modified.

- [ ] **Step 5: Review and integrate only with user authorization**

Summarize implementation, test count, SQL requirement, frontend handoff path, and server commands. Commit and push only after explicit user authorization.
