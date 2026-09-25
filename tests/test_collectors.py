import asyncio
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from crawler.collectors import collect_web, collect_text_file, load_sources
from crawler.slack import channel_address, extract_visible, scroll_older


class FixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        pages = {
            "/": '<article><h2><a href="/post/1">도입 문의</a></h2><b>Alice</b></article><a class="next" href="/page2">Next</a>',
            "/page2": '<article><h2><a href="/post/2">보안 제품</a></h2><b>Bob</b></article>',
            "/post/1": '<div class="body">제품을 찾습니다\n두 번째 줄</div>',
            "/post/2": '<div class="body">SSO 지원 질문</div>',
            "/broken": '<h1>Sign in</h1>',
        }
        content = pages.get(self.path, "")
        self.send_response(200 if content else 404)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(content.encode())

    def log_message(self, *_):
        pass


class CollectorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
        thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.http.server_close)
        self.addCleanup(self.http.shutdown)
        self.url = f"http://127.0.0.1:{self.http.server_port}"
        self.source = dict(id="fixture", kind="web", url=self.url + "/", item_selector="article",
                           title_selector="h2", link_selector="h2 a", author_selector="b",
                           detail_selector=".body", next_selector="a.next", max_pages=2, max_items=10,
                           timeout_ms=1000)

    async def test_browser_extracts_details_and_paginates_with_stable_keys(self):
        items = await collect_web(self.source, self.root)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["title"], "도입 문의")
        self.assertEqual(items[0]["author"], "Alice")
        self.assertIn("두 번째 줄", items[0]["body"])
        self.assertEqual(items[1]["body"], "SSO 지원 질문")
        self.assertEqual(items[0]["key"], self.url + "/post/1")
        self.source["max_items"] = 1
        self.assertEqual(len(await collect_web(self.source, self.root)), 1)

    async def test_missing_selector_is_failure_not_empty_success(self):
        self.source["url"] = self.url + "/broken"
        with self.assertRaisesRegex(RuntimeError, "selector|Selector"):
            await collect_web(self.source, self.root)

    async def test_export_is_a_snapshot_not_invented_message_records(self):
        path = self.root / "대화.txt"
        path.write_text("\ufeff보안방\n홍길동: SSO 제품 추천 부탁해요\n", encoding="utf-8")
        items = collect_text_file({"id": "chat", "path": str(path)})
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["author"], "")
        self.assertIn("홍길동: SSO", items[0]["body"])
        self.assertEqual(items[0]["key"], collect_text_file({"id": "chat", "path": str(path)})[0]["key"])
        path.write_text("", encoding="utf-8")
        with self.assertRaises(ValueError):
            collect_text_file({"id": "chat", "path": str(path)})

    async def test_config_rejects_profile_traversal_and_excessive_work(self):
        path = self.root / "sources.json"
        for overrides in ({"id": "../bad"}, {"max_pages": 999}, {"url": "file:///etc/passwd"}):
            path.write_text(json.dumps([self.source | overrides]))
            with self.assertRaises(ValueError):
                load_sources(path)
        path.write_text(json.dumps([self.source]))
        self.assertEqual(load_sources(path)[0]["id"], "fixture")

    async def test_slack_config_requires_a_public_channel_and_bounded_scroll(self):
        path = self.root / "sources.json"
        source = {"id": "slack", "kind": "slack_web", "enabled": False,
                  "url": "https://app.slack.com/client/T123/C456"}
        path.write_text(json.dumps([source]))
        self.assertEqual(load_sources(path)[0]["kind"], "slack_web")
        self.assertEqual(channel_address(source["url"])[1:], ("T123", "C456"))
        for change in ({"url": "https://evil.test/client/T123/C456"},
                       {"url": "https://app.slack.com/client/T123/G456"},
                       {"max_scrolls": 101}, {"max_messages": 0}):
            path.write_text(json.dumps([source | change]))
            with self.assertRaises(ValueError):
                load_sources(path)

    async def test_slack_reads_rendered_messages_and_scrolls_the_message_pane(self):
        from playwright.async_api import async_playwright

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page()
                await page.set_content("""<div style="height:120px;overflow-y:auto">
                    <div style="height:300px"></div>
                    <div data-qa="message_container"><a data-ts="1720000000.000001"
                      href="https://x.slack.com/archives/C456/p1720000000000001"></a>
                      <span data-qa="message_sender_name">Alice</span>
                      <div data-qa="message-text">첫 줄</div><div data-qa="message-text">둘째 줄</div></div>
                    </div>""")
                messages = await extract_visible(page)
                self.assertEqual(messages[0]["body"], "첫 줄\n둘째 줄")
                self.assertEqual(messages[0]["ts"], "1720000000.000001")
                await page.locator('[data-qa="message_container"]').evaluate(
                    "node => node.parentElement.scrollTop = node.parentElement.scrollHeight")
                self.assertTrue((await scroll_older(page))["moved"])
            finally:
                await browser.close()
