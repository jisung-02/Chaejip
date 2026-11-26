import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class MCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_stdio_collect_search_and_paginate_without_bulk_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            export = root / "chat.txt"
            export.write_text("보안 도입 문의\n" + "내용" * 10000, encoding="utf-8")
            config = root / "sources.json"
            config.write_text(json.dumps([
                {"id": "chat", "kind": "text_file", "path": str(export)},
                {"id": "disabled", "kind": "text_file", "path": str(export), "enabled": False},
                {"id": "slack", "kind": "slack_web", "url": "https://app.slack.com/client/T123/C456", "enabled": False},
                {"id": "kakao-test", "kind": "kakao_window", "window_title": ""},
            ]))
            params = StdioServerParameters(command=sys.executable, args=["-m", "crawler", "--config", str(config), "--data-dir", str(root / "data")], env=dict(os.environ))
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as client:
                    await client.initialize()
                    catalog = await client.list_tools()
                    self.assertEqual({t.name for t in catalog.tools}, {"list_sources", "collect", "collect_slack", "search_items", "get_item"})
                    async def call(name, args):
                        result = await client.call_tool(name, args)
                        self.assertFalse(result.isError, result.content)
                        return json.loads(result.content[0].text)
                    first = await call("collect", {"source_id": "chat"})
                    self.assertEqual(first["new"], 1)
                    self.assertNotIn("body", first)
                    self.assertEqual((await call("collect", {"source_id": "chat"}))["unchanged"], 1)
                    self.assertEqual((await call("collect", {"source_id": "disabled"}))["status"], "disabled")
                    slack = await call("collect", {"source_id": "slack"})
                    self.assertEqual(slack["status"], "disabled")
                    self.assertEqual(slack["guidance"]["required_fields"], ["url"])
                    self.assertEqual((await call("collect", {"source_id": "missing"}))["status"], "not_found")
                    bad = await call("collect_slack", {"workspace": "community", "channel": "G01ABCDEF"})
                    self.assertEqual(bad["status"], "invalid_arguments")
                    login = await call("collect_slack", {"workspace": "community", "channel": "#general"})
                    self.assertEqual(login["status"], "login_required")
                    self.assertIn("--slack-login", login["message"])
                    setup = await call("collect", {"source_id": "kakao-test"})
                    self.assertEqual(setup["status"], "configuration_required")
                    self.assertTrue(Path(setup["guidance"]["config_path"]).samefile(config))
                    self.assertEqual(setup["guidance"]["source_id"], "kakao-test")
                    self.assertEqual(setup["guidance"]["required_fields"], ["window_title"])
                    self.assertEqual(setup["guidance"]["retry"]["arguments"], {"source_id": "kakao-test"})
                    self.assertEqual(setup["guidance"]["fallback"]["kind"], "text_file")
                    self.assertTrue(setup["guidance"]["next_steps"])
                    self.assertNotIn("body", setup)
                    catalog = await call("list_sources", {})
                    self.assertTrue(all("guidance" not in s for s in catalog["sources"]))
                    help_result = await call("list_sources", {"source_id": "kakao-test", "include_guidance": True})
                    self.assertEqual(len(help_result["sources"]), 1)
                    self.assertTrue(Path(help_result["sources"][0]["guidance"]["config_path"]).samefile(config))
                    hits = await call("search_items", {"query": "도입"})
                    self.assertEqual(len(hits["items"]), 1)
                    self.assertLessEqual(len(hits["items"][0]["excerpt"]), 300)
                    record = await call("get_item", {"id": hits["items"][0]["id"], "max_chars": 20})
                    self.assertEqual(len(record["body"]), 20)
                    self.assertEqual(record["next_offset"], 20)
                    invalid = await client.call_tool("search_items", {"query": "", "limit": 0})
                    self.assertTrue(invalid.isError)
