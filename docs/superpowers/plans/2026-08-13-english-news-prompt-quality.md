# English News Prompt Quality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert every AI control prompt to English while requiring Korean news output and incorporating the approved relevance, fidelity, urgency, and material-follow-up guidance.

**Architecture:** Keep the collector and deterministic validation pipeline unchanged. Treat the prompt strings as the model boundary: the initial selection prompt decides eligibility and labeling, the JSON-repair prompt repairs syntax only, and the focused quality-repair prompt corrects rejected Korean output without reselecting articles.

**Tech Stack:** Python 3, `unittest`, existing `news_selector.py` JSON decision contract.

## Global Constraints

- Modify AI instruction text only; do not change filtering, validation, score normalization, deduplication, database, notification, schedule, batch size, or external-call logic.
- Preserve material follow-ups as separate articles when they contain a new casualty count, confirmed decision, revised statistic, approval, cancellation, or other state change.
- Require `title`, `content`, and `selection_reason` values in natural Korean; retain English JSON keys and enum values.
- Do not add GNews requests, AI calls, URL crawling, dependencies, or migrations.
- Do not commit or push implementation changes until the user requests it.

---

### Task 1: Lock the English prompt boundary with regression tests

**Files:**
- Modify: `tests/test_news_selector.py`

**Interfaces:**
- Consumes: `select_and_summarize(articles, generator, *, batch_size=10, recent_news=None)`.
- Produces: captured prompt assertions for the initial selection, JSON syntax repair, and focused quality repair calls.

- [ ] **Step 1: Update and add failing prompt-contract tests**

  Assert literal English requirements for: Korean-only output values; direct economic relevance; exclusion of local crime, ceremony, awards, routine visits, tiny community grants, and vague roundups; retention of concrete small-company economic developments; source-backed geography, actor, currency, unit, direction, and period; exclusion of irreparably broken source text; a 7-8 cap for proposals, discussions, sandboxes, statements, forecasts, ordinary results and transactions; 9-10 only for confirmed broad immediate shocks; and separate `follow_up` eligibility for material new facts. Update JSON-repair assertions to require syntax-only English instructions, and focused-repair assertions to require English fidelity and Korean-output instructions.

- [ ] **Step 2: Run the targeted tests and verify RED**

  Run:

  ```powershell
  python -m unittest discover -s tests -p test_news_selector.py
  ```

  Expected: failures only where the current Korean prompts lack the new English contract.

---

### Task 2: Translate and strengthen the three AI prompts

**Files:**
- Modify: `news_selector.py`
- Test: `tests/test_news_selector.py`

**Interfaces:**
- Consumes: candidate and recent-news dictionaries already passed to the prompt builders.
- Produces: the same JSON array schema and decision fields currently consumed by `_decision_to_item()`.

- [ ] **Step 1: Replace the JSON syntax-repair instructions**

  Use English instructions that permit JSON syntax repair only, prohibit fact changes or additions, preserve Korean string values, and require one bare JSON array.

- [ ] **Step 2: Replace `_selection_prompt()` control prose**

  Express the approved economic relevance, exclusions, factual fidelity, Korean output, urgency scoring, and material-follow-up rules in English. Keep the injected candidates, recent-news context, JSON keys, enums, title length, and summary length contract unchanged.

- [ ] **Step 3: Replace `_quality_repair_prompt()` control prose**

  In English, limit the operation to correcting failed title/summary quality, prohibit reselection and unsupported facts, require natural Korean values, preserve identifiers, actors, confidence, currency, units and metrics, and omit entries that cannot be repaired faithfully.

- [ ] **Step 4: Run the targeted tests and verify GREEN**

  Run:

  ```powershell
  python -m unittest discover -s tests -p test_news_selector.py
  ```

  Expected: all selector tests pass with the same selected rows and call counts.

---

### Task 3: Verify unchanged system behavior

**Files:**
- Verify: `news_selector.py`
- Verify: `gnews_adapter.py`
- Verify: `gnews_tracker.py`
- Verify: `tests/`

**Interfaces:**
- Consumes: the unchanged collector, selector, repository, and publisher contracts.
- Produces: verification evidence only; no additional production changes.

- [ ] **Step 1: Compile changed Python modules**

  ```powershell
  python -m py_compile news_selector.py tests/test_news_selector.py
  ```

- [ ] **Step 2: Run the complete test suite**

  ```powershell
  python -m unittest discover -s tests -p 'test_*.py'
  ```

  Expected: all tests pass, including request-count, deduplication, notification, schedule, DB, and source-content tests.

- [ ] **Step 3: Check the final diff**

  ```powershell
  git diff --check
  git diff --stat
  git status --short --branch
  ```

  Expected: no whitespace errors and changes limited to the approved prompt, selector tests, and documentation files.
