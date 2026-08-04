import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace
import sys
import unittest
from unittest.mock import patch


class FakeQuery:
    def __init__(self, client, table_name):
        self.client = client
        self.table_name = table_name
        self.column = None
        self.value = None

    def select(self, fields):
        return self

    def eq(self, column, value):
        self.column = column
        self.value = value
        return self

    def execute(self):
        self.client.queries.append((self.table_name, self.column, self.value))
        records = self.client.records
        if self.column == "fcm_token":
            records = [item for item in records if item.get("fcm_token") == self.value]
        elif self.column and self.column.startswith("preferences->>"):
            category = self.column.split(">>", maxsplit=1)[1]
            records = [
                item
                for item in records
                if str(item.get("preferences", {}).get(category, False)).lower()
                == self.value
            ]
        return SimpleNamespace(data=[item.copy() for item in records])


class FakeSupabase:
    def __init__(self, records):
        self.records = records
        self.queries = []

    def table(self, table_name):
        return FakeQuery(self, table_name)


class FakeMessaging:
    def __init__(self):
        self.sent_messages = []

    def MulticastMessage(self, **kwargs):
        return SimpleNamespace(**kwargs)

    def AndroidConfig(self, **kwargs):
        return kwargs

    def AndroidNotification(self, **kwargs):
        return kwargs

    def WebpushConfig(self, **kwargs):
        return kwargs

    def WebpushFCMOptions(self, **kwargs):
        return kwargs

    def send_each_for_multicast(self, message):
        self.sent_messages.append(message)
        return SimpleNamespace(success_count=len(message.tokens), failure_count=0, responses=[])


def load_push_notification_module():
    firebase_admin = ModuleType("firebase_admin")
    firebase_admin._apps = [object()]
    firebase_admin.credentials = SimpleNamespace()
    firebase_admin.messaging = FakeMessaging()

    pywebpush = ModuleType("pywebpush")
    pywebpush.webpush = lambda **kwargs: None
    pywebpush.WebPushException = Exception

    supabase = ModuleType("supabase")
    supabase.create_client = lambda *args: None
    supabase.Client = object

    dotenv = ModuleType("dotenv")
    dotenv.load_dotenv = lambda *args, **kwargs: None

    module_path = Path(__file__).parents[1] / "push_notification.py"
    spec = importlib.util.spec_from_file_location("push_notification_under_test", module_path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(
        sys.modules,
        {
            "firebase_admin": firebase_admin,
            "pywebpush": pywebpush,
            "supabase": supabase,
            "dotenv": dotenv,
        },
    ):
        spec.loader.exec_module(module)
    return module


class PushAudienceTests(unittest.TestCase):
    def test_existing_single_category_calls_keep_their_audience(self):
        module = load_push_notification_module()
        database = FakeSupabase(
            [
                {
                    "id": "currency-user",
                    "fcm_token": "token-currency",
                    "preferences": {"common_currency": True},
                },
                {
                    "id": "other-user",
                    "fcm_token": "token-other",
                    "preferences": {"common_currency": False},
                },
            ]
        )
        module.create_client = lambda *args: database
        module.is_quiet_time = lambda: False
        module.revalidate_path = lambda path: None

        module.send_push_notification(
            "환율 변동",
            "원·달러 환율이 움직였습니다.",
            "/currency-desk",
            category="common_currency",
        )

        self.assertEqual(
            module.messaging.sent_messages[0].tokens,
            ["token-currency"],
        )
        self.assertEqual(
            database.queries,
            [("fcm_subscriptions", "preferences->>common_currency", "true")],
        )

    def test_multiple_categories_send_once_to_the_union_of_subscribers(self):
        module = load_push_notification_module()
        database = FakeSupabase(
            [
                {
                    "id": "realtime",
                    "fcm_token": "token-realtime",
                    "preferences": {"breaking_news": True},
                },
                {
                    "id": "important",
                    "fcm_token": "token-important",
                    "preferences": {"important_breaking_news": True},
                },
                {
                    "id": "both",
                    "fcm_token": "token-both",
                    "preferences": {
                        "breaking_news": True,
                        "important_breaking_news": True,
                    },
                },
            ]
        )
        module.create_client = lambda *args: database
        module.is_quiet_time = lambda: False
        module.revalidate_path = lambda path: None

        module.send_push_notification(
            "긴급 속보",
            "중요한 경제 소식입니다.",
            "/live",
            categories=("breaking_news", "important_breaking_news"),
        )

        sent_tokens = module.messaging.sent_messages[0].tokens
        self.assertEqual(
            set(sent_tokens),
            {"token-realtime", "token-important", "token-both"},
        )
        self.assertEqual(len(sent_tokens), 3)
        self.assertEqual(
            database.queries,
            [
                ("fcm_subscriptions", "preferences->>breaking_news", "true"),
                (
                    "fcm_subscriptions",
                    "preferences->>important_breaking_news",
                    "true",
                ),
            ],
        )

    def test_etiquette_mode_suppresses_multi_category_breaking_alerts(self):
        module = load_push_notification_module()
        database = FakeSupabase(
            [
                {
                    "id": "quiet-user",
                    "fcm_token": "token-quiet",
                    "preferences": {
                        "breaking_news": True,
                        "etiquette_mode": True,
                    },
                }
            ]
        )
        module.create_client = lambda *args: database
        module.is_quiet_time = lambda: True
        module.revalidate_path = lambda path: None

        module.send_push_notification(
            "긴급 속보",
            "중요한 경제 소식입니다.",
            "/live",
            categories=("breaking_news", "important_breaking_news"),
        )

        self.assertEqual(module.messaging.sent_messages, [])


if __name__ == "__main__":
    unittest.main()
