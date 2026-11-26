import unittest

from crawler.slack import open_target, parse_target, target_url


class FakeLink:
    def __init__(self, href):
        self.href = href

    @property
    def first(self):
        return self

    async def count(self):
        return 1 if self.href else 0

    async def get_attribute(self, name):
        return self.href


class FakePage:
    """Simulates Slack redirects: each goto maps a URL to where the browser ends up."""

    def __init__(self, routes, link="", deeplinks=None):
        self.routes, self.link, self.url, self.visited = routes, link, "about:blank", []
        self.deeplinks, self.listeners = deeplinks or {}, []

    def on(self, event, handler):
        self.listeners.append(handler)

    async def goto(self, url, **kwargs):
        self.visited.append(url)
        self.url = self.routes.get(url, url)
        if url in self.deeplinks:
            for handler in self.listeners:
                handler(type("Request", (), {"url": self.deeplinks[url]})())
        if url in getattr(self, "aborts", ()):
            from playwright.async_api import Error
            raise Error(f"Page.goto: net::ERR_ABORTED at {url}")

    def locator(self, selector):
        return FakeLink(self.link)


class SlackTargetTests(unittest.IsolatedAsyncioTestCase):
    def test_parses_workspace_and_channel_forms(self):
        self.assertEqual(parse_target("Community.slack.com/", "#General"),
                         {"team": "", "domain": "community", "channel": "", "name": "general"})
        self.assertEqual(target_url(parse_target("https://community.slack.com", "C01ABCDEF")),
                         "https://community.slack.com/archives/C01ABCDEF")
        self.assertEqual(target_url(parse_target("T01ABCDEF", "C01ABCDEF")),
                         "https://app.slack.com/client/T01ABCDEF/C01ABCDEF")
        link = parse_target("community", "https://community.slack.com/archives/C01ABCDEF/p1720000000000100")
        self.assertEqual(link["channel"], "C01ABCDEF")
        self.assertEqual(parse_target("T01ABCDEF", "https://app.slack.com/client/T01ABCDEF/C01ABCDEF")["team"], "T01ABCDEF")

    def test_rejects_private_foreign_and_ambiguous_targets(self):
        for workspace, channel in (("community", "G01ABCDEF"), ("community", "D01ABCDEF"),
                                   ("app.slack.com", "#general"), ("evil.test/../x", "#general"),
                                   ("T01ABCDEF", "#general"), ("community", "rm -rf"),
                                   ("community", "https://other.slack.com/archives/C01ABCDEF"),
                                   ("T01ABCDEF", "https://app.slack.com/client/T09ZZZZZZ/C01ABCDEF"),
                                   ("community", "https://evil.test/archives/C01ABCDEF")):
            with self.subTest(workspace=workspace, channel=channel), self.assertRaises(ValueError):
                parse_target(workspace, channel)

    async def test_follows_archive_redirect_and_checks_the_opened_channel(self):
        target = parse_target("community", "#general")
        page = FakePage({"https://community.slack.com/archives/general": "https://app.slack.com/client/T01ABCDEF/C01ABCDEF"})
        self.assertEqual(await open_target(page, target, 10), ("T01ABCDEF", "C01ABCDEF"))

    async def test_aborted_first_navigation_is_accepted_when_slack_redirects(self):
        url = "https://community.slack.com/archives/general"
        page = FakePage({url: "https://app.slack.com/client/T01ABCDEF/C01ABCDEF"})
        page.aborts = {url}
        self.assertEqual(await open_target(page, parse_target("community", "#general"), 10), ("T01ABCDEF", "C01ABCDEF"))

    async def test_channel_name_resolves_through_the_desktop_handoff(self):
        url = "https://community.slack.com/archives/general"
        page = FakePage({}, deeplinks={url: "slack://channel?team=T01ABCDEF&id=C01ABCDEF&host=slack.com"})
        page.aborts = {url}
        self.assertEqual(await open_target(page, parse_target("community", "#general"), 10), ("T01ABCDEF", "C01ABCDEF"))
        self.assertEqual(page.visited[-1], "https://app.slack.com/client/T01ABCDEF/C01ABCDEF")
        private = FakePage({}, deeplinks={url: "slack://channel?team=T01ABCDEF&id=G01ABCDEF"})
        with self.assertRaises(RuntimeError):
            await open_target(private, parse_target("community", "#general"), 10)

    async def test_uses_only_the_web_client_link_on_an_interstitial(self):
        target = parse_target("community", "C01ABCDEF")
        page = FakePage({}, link="https://app.slack.com/client/T01ABCDEF/C01ABCDEF")
        self.assertEqual(await open_target(page, target, 10), ("T01ABCDEF", "C01ABCDEF"))
        bad = FakePage({}, link="https://evil.test/client/T01ABCDEF/C01ABCDEF")
        with self.assertRaises(RuntimeError):
            await open_target(bad, target, 10)
        self.assertEqual(len(bad.visited), 1)

    async def test_signin_and_wrong_channel_stop_collection(self):
        signin = FakePage({"https://community.slack.com/archives/general": "https://community.slack.com/signin"})
        with self.assertRaises(PermissionError):
            await open_target(signin, parse_target("community", "#general"), 10)
        wrong = FakePage({"https://community.slack.com/archives/C01ABCDEF": "https://app.slack.com/client/T01ABCDEF/C09ZZZZZZ"})
        with self.assertRaises(RuntimeError):
            await open_target(wrong, parse_target("community", "C01ABCDEF"), 10)
        private = FakePage({"https://community.slack.com/archives/secret": "https://app.slack.com/client/T01ABCDEF/G01ABCDEF"})
        with self.assertRaises(RuntimeError):
            await open_target(private, parse_target("community", "#secret"), 10)


if __name__ == "__main__":
    unittest.main()
