import ast
from pathlib import Path
import unittest


class BreakingTrackerConfigTests(unittest.TestCase):
    def test_nyt_and_al_jazeera_are_separate_rss_feeds(self):
        source_path = Path(__file__).resolve().parents[1] / "breaking_tracker.py"
        module = ast.parse(source_path.read_text(encoding="utf-8"))
        rss_feeds = next(
            ast.literal_eval(node.value)
            for node in module.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "RSS_FEEDS"
                for target in node.targets
            )
        )

        self.assertIn(
            "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml",
            rss_feeds,
        )
        self.assertIn(
            "https://www.aljazeera.com/xml/rss/all.xml",
            rss_feeds,
        )


if __name__ == "__main__":
    unittest.main()
