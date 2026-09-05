import tempfile
import unittest
from pathlib import Path

from crawler.store import Store


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / "items.sqlite3")

    def test_recollect_updates_without_duplicate_and_isolates_sources(self):
        item = dict(key="1", title="도입 문의", body="첫 글", url="https://example.com/1", author="A")
        self.assertEqual(self.store.save("a", [item]), {"new": 1, "updated": 0, "unchanged": 0})
        self.assertEqual(self.store.save("a", [item])["unchanged"], 1)
        item["body"] = "수정된 글"
        self.assertEqual(self.store.save("a", [item])["updated"], 1)
        self.assertEqual(self.store.save("b", [item])["new"], 1)
        results = self.store.search("수정", source_id="a")
        self.assertEqual(len(results), 1)
        self.assertEqual(self.store.get(results[0]["id"])["body"], "수정된 글")

    def test_search_is_literal_bounded_and_body_is_paginated(self):
        self.store.save("a", [dict(key="1", title="100%", body="가" * 20000, url="", author="")])
        self.store.save("a", [dict(key="2", title="other", body="nothing", url="", author="")])
        rows = self.store.search("%")
        self.assertEqual(len(rows), 1)
        self.assertNotIn("body", rows[0])
        self.assertLessEqual(len(rows[0]["excerpt"]), 300)
        page = self.store.get(rows[0]["id"], offset=19990, max_chars=100)
        self.assertEqual(page["body"], "가" * 10)
        self.assertIsNone(page["next_offset"])
        self.assertEqual(self.store.search("", since="2999-01-01"), [])
        for kwargs in ({"limit": 0}, {"limit": 101}, {"since": "bad"}):
            with self.assertRaises(ValueError):
                self.store.search("", **kwargs)
        with self.assertRaises(ValueError):
            self.store.get(rows[0]["id"], offset=-1)
        with self.assertRaises(KeyError):
            self.store.get(999)

    def test_nul_rejection_rolls_back_the_entire_batch(self):
        with self.assertRaises(ValueError):
            self.store.save("a", [dict(key="good", body="valid"), dict(key="bad", body="before\x00after")])
        self.assertEqual(self.store.search(""), [])
