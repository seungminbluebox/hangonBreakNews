"""Synthetic offline contracts for the approved expand/contract preparation."""
import ast
from datetime import datetime, timezone
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from gnews_tracker import SupabaseBreakingNewsRepository, to_breaking_news_row, run_production
from news_selector import select_and_summarize
from news_pipeline import PipelineResult, validate_legacy_headline
from test_gnews_worker import article, selected


def rss_functions(mode="legacy"):
    """Load only pure orchestration functions; never import RSS live clients/env."""
    from breaking_news_storage import prepare_breaking_news_row, recent_duplicate_context
    tree = ast.parse((Path(__file__).resolve().parents[1] / "breaking_tracker.py").read_text(encoding="utf-8"))
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name in {"get_recent_news_list", "save_and_notify"}]
    query = Mock()
    for operation in ("select", "order", "limit", "insert"):
        getattr(query, operation).return_value = query
    query.execute.return_value = Mock(data=[])
    client = Mock()
    client.table.return_value = query
    namespace = {"supabase": client, "CONTENT_COLUMN_MODE": mode,
                 "prepare_breaking_news_row": prepare_breaking_news_row,
                 "recent_duplicate_context": recent_duplicate_context,
                 "is_already_saved": Mock(return_value=False),
                 "send_push_notification": Mock(), "revalidate_path": Mock(), "print": Mock()}
    exec(compile(ast.Module(body=functions, type_ignores=[]), "rss-test", "exec"), namespace)
    return namespace, query


class ContentRemovalPreparationTests(unittest.TestCase):
    def test_default_write_satisfies_current_not_null_schema_without_generated_summary(self):
        from breaking_news_storage import prepare_breaking_news_row
        original = {"title": "합성 제목", "content": "generated summary", "source_content": " exact raw "}
        with patch.dict("os.environ", {}, clear=True):
            result = prepare_breaking_news_row(original)
        self.assertEqual(result["content"], "")
        self.assertEqual(result["source_content"], " exact raw ")
        self.assertEqual(original["content"], "generated summary")

    def test_explicit_omit_mode_omits_column_and_preserves_raw_source(self):
        with patch.dict("os.environ", {"BREAKING_NEWS_CONTENT_COLUMN_MODE": "omit"}, clear=True):
            row = to_breaking_news_row(selected(article()))
        self.assertNotIn("content", row)
        self.assertEqual(row["source_content"], article()["raw_content"])

    def test_invalid_mode_fails_before_any_repository_query(self):
        client = Mock()
        for invalid in ("", "auto", "OMIT", " legacy "):
            with self.subTest(mode=invalid), patch.dict("os.environ", {"BREAKING_NEWS_CONTENT_COLUMN_MODE": invalid}, clear=True):
                with self.assertRaises(ValueError):
                    SupabaseBreakingNewsRepository(client)
        client.table.assert_not_called()

    def test_repository_omit_mode_does_not_probe_or_retry_the_schema(self):
        query = Mock()
        for operation in ("select", "in_", "insert"):
            getattr(query, operation).return_value = query
        query.execute.side_effect = [Mock(data=[]), RuntimeError("synthetic current-schema rejection")]
        client = Mock()
        client.table.return_value = query
        repository = SupabaseBreakingNewsRepository(client, content_column_mode="omit")
        with self.assertRaises(RuntimeError):
            repository.save(selected(article()))
        query.insert.assert_called_once()
        self.assertNotIn("content", query.insert.call_args.args[0])
        self.assertEqual(query.execute.call_count, 2)  # Exact-URL check and the single insert.

    def test_invalid_production_mode_is_rejected_before_live_client_construction(self):
        with patch("supabase.create_client") as create_client:
            with self.assertRaises(ValueError):
                run_production("synthetic-key", Mock(), environment={"BREAKING_NEWS_CONTENT_COLUMN_MODE": "auto"})
        create_client.assert_not_called()

    def test_duplicate_read_uses_private_raw_source_without_legacy_summary_fallback(self):
        client = Mock()
        query = client.table.return_value
        for operation in ("select", "gte", "order", "limit"):
            getattr(query, operation).return_value = query
        query.execute.return_value = Mock(data=[
            {"title": "원문 있는 제목", "source_content": " exact provider body ", "content": "generated summary"},
            {"title": "원문 없는 제목", "source_content": None, "content": "generated summary"},
            {"title": "", "source_content": None, "content": "generated summary"},
        ])
        result = SupabaseBreakingNewsRepository(client).recent_news(datetime(2026, 10, 10, tzinfo=timezone.utc))
        query.select.assert_called_once_with("title,source_content,created_at")
        self.assertEqual([(row["title"], row["content"]) for row in result],
                         [("원문 있는 제목", " exact provider body "), ("원문 없는 제목", "")])

    def test_rss_duplicate_read_uses_source_only_and_keeps_title_only_rows(self):
        namespace, query = rss_functions()
        query.execute.return_value = Mock(data=[
            {"title": "원문 있는 제목", "source_content": "RAW", "content": "generated summary"},
            {"title": "원문 없는 제목", "source_content": None, "content": "generated summary"},
        ])
        result = namespace["get_recent_news_list"]()
        query.select.assert_called_once_with("title,source_content")
        self.assertEqual(result, [{"title": "원문 있는 제목", "content": "RAW"},
                                  {"title": "원문 없는 제목", "content": ""}])

    def test_rss_write_modes_preserve_private_source_and_blank_push_body(self):
        for mode in ("legacy", "omit"):
            with self.subTest(mode=mode):
                namespace, query = rss_functions(mode)
                namespace["save_and_notify"]({"title": "원문 제목", "content": "generated summary",
                    "source_content": " exact raw source ", "importance_score": 8,
                    "category": "corporate", "original_url": "https://example.com/synthetic"})
                row = query.insert.call_args.args[0]
                self.assertEqual("content" in row, mode == "legacy")
                if mode == "legacy":
                    self.assertEqual(row["content"], "")
                self.assertEqual(row["source_content"], " exact raw source ")
                self.assertEqual(namespace["send_push_notification"].call_args.kwargs["body"], "")

    def test_historical_selector_defaults_to_current_title_only_pipeline(self):
        item = selected(article())
        item["normalized_content"] = ""
        result = PipelineResult(selected=[item], unevaluated_urls={"https://example.com/retry"})
        generator = Mock()
        with patch("news_pipeline.run_two_stage_pipeline", return_value=result) as pipeline:
            selected_items = select_and_summarize([article()], generator)
        pipeline.assert_called_once()
        generator.assert_not_called()
        self.assertEqual(selected_items, [item])
        self.assertEqual(selected_items.retryable_urls, result.unevaluated_urls)

    def test_rss_validation_exports_actual_source_without_relabeling_generated_body(self):
        raw = "Exact provider evidence about the new factory expansion."
        original = {"id": 0, "title": "공장 증설 승인", "content_to_analyze": raw,
                    "original_url": "https://example.com/synthetic"}
        draft = {"title": "공장 증설 승인", "source_excerpt": raw, "content": "generated summary"}
        normalized = {"normalized_title": draft["title"], "normalized_content": "",
                      "importance_score": 8, "category": "corporate"}
        with patch("news_pipeline._decision_to_item", return_value=(normalized, None)):
            result = validate_legacy_headline(draft, original)
        self.assertEqual(result["source_content"], raw)
        self.assertEqual(result["content"], "")
