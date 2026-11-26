import unittest
from unittest.mock import patch

from crawler import slack


class Clock:
    def __init__(self, step):
        self.now, self.step = 0.0, step

    def __call__(self):
        self.now += self.step
        return self.now


class Page:
    async def wait_for_timeout(self, ms):
        pass


def endless_channel():
    """Every extract shows 3 new, older messages; the pane never reaches the top."""
    state = {"ts": 1720000000}

    async def extract(page, scope="channel"):
        rows = []
        for _ in range(3):
            state["ts"] -= 1
            rows.append({"ts": f"{state['ts']}.000100", "body": "msg", "author": "a",
                         "permalink": "", "has_replies": False})
        return rows

    async def scroll(page, scope="channel"):
        return {"found": True, "at_top": False, "moved": True}

    return extract, scroll


class SlackBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_time_budget_stops_reading_instead_of_a_scroll_count(self):
        extract, scroll = endless_channel()
        with patch("crawler.slack.extract_visible", extract), patch("crawler.slack.scroll_older", scroll):
            messages, details = await slack.collect_from_page(
                Page(), "T01ABCDEF", "C01ABCDEF", max_messages=1000, max_scrolls=2000,
                max_threads=0, deadline=60, clock=Clock(1))
        self.assertEqual(details["stop_reason"], "time_budget")
        # 60 one-second ticks with 3 new messages per pass, far past the old 35-scroll cap.
        self.assertGreater(len(messages), 35 * 3 // 2)
        self.assertLessEqual(len(messages), 1000)

    async def test_message_limit_still_wins_before_the_budget(self):
        extract, scroll = endless_channel()
        with patch("crawler.slack.extract_visible", extract), patch("crawler.slack.scroll_older", scroll):
            messages, details = await slack.collect_from_page(
                Page(), "T01ABCDEF", "C01ABCDEF", max_messages=20, max_scrolls=2000,
                max_threads=0, deadline=10_000, clock=Clock(1))
        self.assertEqual(details["stop_reason"], "message_limit")
        self.assertEqual(len(messages), 20)

    async def test_visible_screen_is_kept_and_threads_skipped_once_time_is_up(self):
        async def extract(page, scope="channel"):
            return [{"ts": "1720000000.000100", "body": "parent", "author": "a",
                     "permalink": "", "has_replies": True}]

        async def scroll(page, scope="channel"):
            return {"found": True, "at_top": True, "moved": False}

        async def never(*args):
            raise AssertionError("thread opened after the deadline")

        with patch("crawler.slack.extract_visible", extract), patch("crawler.slack.scroll_older", scroll), \
             patch("crawler.slack.open_thread", never):
            messages, details = await slack.collect_from_page(
                Page(), "T01ABCDEF", "C01ABCDEF", max_messages=100, max_threads=5,
                deadline=0, clock=lambda: 1.0)
        self.assertEqual(details["stop_reason"], "time_budget")
        self.assertEqual(len(messages), 1)
        self.assertEqual(details["threads_skipped"], 1)


if __name__ == "__main__":
    unittest.main()
