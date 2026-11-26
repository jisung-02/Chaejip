"""Collect channel messages and visible thread replies in an authenticated Slack tab."""

import asyncio
import re
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.async_api import Error as PlaywrightError, async_playwright, TimeoutError as PlaywrightTimeout


CHANNEL_PATH = re.compile(r"^/client/(T[A-Z0-9]+|E[A-Z0-9]+)/(C[A-Z0-9]+)(?:/)?$")
OPEN_CHANNEL_PATH = re.compile(r"^/client/(T[A-Z0-9]+|E[A-Z0-9]+)/(C[A-Z0-9]+)(?:/|$)")
MESSAGE_SELECTOR = '[data-qa="message_container"]'
THREAD_SELECTOR = '.p-threads_flexpane, [data-qa="threads_flexpane"]'
REPLY_SELECTOR = '.c-message_kit__thread_replies, .c-message__reply_bar, [data-qa="reply_count"]'


def channel_address(value: str) -> tuple[str, str, str]:
    parts = urlsplit(value)
    match = CHANNEL_PATH.fullmatch(parts.path)
    if parts.scheme != "https" or parts.hostname != "app.slack.com" or not match or parts.username or parts.password:
        raise ValueError("Use a public-channel URL like https://app.slack.com/client/T.../C...")
    team, channel = match.groups()
    return f"https://app.slack.com/client/{team}/{channel}", team, channel


async def extract_visible(page, scope: str = "channel") -> list[dict]:
    """Read individual messages from the channel or thread pane, never the composer."""
    if scope not in ("channel", "thread"):
        raise ValueError("Unknown Slack pane")
    return await page.locator(MESSAGE_SELECTOR).evaluate_all("""(nodes, scope) => {
        const inThread = node => !!node.closest('.p-threads_flexpane, [data-qa="threads_flexpane"]');
        let lastAuthor = '';
        return nodes.filter(node => inThread(node) === (scope === 'thread')).map(node => {
        const stamp = node.querySelector('a[data-ts]');
        const textNodes = [...node.querySelectorAll('[data-qa="message-text"]')];
        const body = textNodes.map(n => n.innerText.trim()).filter(Boolean).join('\\n').trim();
        const author = node.querySelector('[data-qa="message_sender_name"]')?.innerText.trim() || lastAuthor;
        if (author) lastAuthor = author;
        return {ts: stamp?.getAttribute('data-ts') || '', body, author,
                permalink: stamp?.getAttribute('href') || '',
                has_replies: !!node.querySelector('.c-message_kit__thread_replies, .c-message__reply_bar, [data-qa="reply_count"]')};
        });
    }""", scope)


async def scroll_older(page, scope: str = "channel") -> dict:
    """Scroll the message pane by less than a screen to overlap virtualized rows."""
    if scope not in ("channel", "thread"):
        raise ValueError("Unknown Slack pane")
    return await page.evaluate("""scope => {
        const inThread = node => !!node.closest('.p-threads_flexpane, [data-qa="threads_flexpane"]');
        const node = [...document.querySelectorAll('[data-qa="message_container"]')]
            .find(row => inThread(row) === (scope === 'thread'));
        if (!node) return {found: false, at_top: false, moved: false};
        let pane = node.parentElement;
        while (pane) {
            const style = getComputedStyle(pane);
            if (/(auto|scroll)/.test(style.overflowY) && pane.scrollHeight > pane.clientHeight + 20) break;
            pane = pane.parentElement;
        }
        if (!pane) return {found: true, at_top: true, moved: false};
        const before = pane.scrollTop;
        pane.scrollTop = Math.max(0, before - Math.max(100, pane.clientHeight * 0.55));
        return {found: true, at_top: pane.scrollTop <= 1, moved: pane.scrollTop < before};
    }""", scope)


async def open_thread(page, parent_ts: str) -> bool:
    """Open an existing reply thread from the channel's reply indicator."""
    opened = await page.evaluate("""ts => {
        const row = [...document.querySelectorAll('[data-qa="message_container"]')]
            .find(node => !node.closest('.p-threads_flexpane, [data-qa="threads_flexpane"]') &&
                node.querySelector('a[data-ts]')?.getAttribute('data-ts') === ts);
        const button = row?.querySelector('.c-message_kit__thread_replies, .c-message__reply_bar, [data-qa="reply_count"]');
        if (!button) return false;
        button.click();
        return true;
    }""", parent_ts)
    if not opened:
        return False
    try:
        await page.locator(THREAD_SELECTOR).first.wait_for(state="visible", timeout=3000)
        await page.locator(THREAD_SELECTOR).first.locator(f'a[data-ts="{parent_ts}"]').first.wait_for(
            state="attached", timeout=3000)
    except PlaywrightTimeout:
        return False
    await page.wait_for_timeout(350)
    return True


def message_record(message: dict, team: str, channel: str, thread_ts: str = "") -> dict | None:
    ts = message["ts"]
    body = message["body"]
    if not re.fullmatch(r"\d{9,12}\.\d{1,9}", ts) or not body:
        return None
    return {"team": team, "channel": channel, "ts": ts, "thread_ts": thread_ts,
            "body": body[:50000], "author": message["author"][:300],
            "permalink": message["permalink"]}


def out_of_time(deadline: float | None, clock=time.monotonic) -> bool:
    return deadline is not None and clock() >= deadline


async def collect_thread(page, team: str, channel: str, parent_ts: str,
                         max_scrolls: int, wait_ms: int, deadline: float | None = None,
                         clock=time.monotonic) -> tuple[list[dict], str]:
    replies: dict[str, dict] = {}
    idle = 0
    stop_reason = "scroll_limit"
    for _ in range(max_scrolls + 1):
        added = 0
        for message in await extract_visible(page, "thread"):
            record = message_record(message, team, channel, parent_ts)
            if record and record["ts"] != parent_ts:
                if record["ts"] not in replies:
                    added += 1
                replies[record["ts"]] = record
        if out_of_time(deadline, clock):
            stop_reason = "time_budget"
            break
        movement = await scroll_older(page, "thread")
        if not movement["found"]:
            stop_reason = "scroll_pane_missing"
            break
        idle = idle + 1 if not added and movement["at_top"] else 0
        if idle >= 3:
            stop_reason = "top_or_no_more_messages"
            break
        await page.wait_for_timeout(wait_ms)
    return list(replies.values()), stop_reason


async def collect_from_page(page, team: str, channel: str, max_messages: int = 500,
                            max_scrolls: int = 60, max_threads: int = 5,
                            max_thread_scrolls: int = 5, wait_ms: int = 650,
                            deadline: float | None = None, clock=time.monotonic) -> tuple[list[dict], dict]:
    """Walk the channel timeline and inspect threads while parents are visible.

    Stops at the top of the channel, at max_messages, after max_scrolls, or when the
    monotonic deadline passes (returning what was read so far)."""
    found: dict[str, dict] = {}
    visited_threads: set[str] = set()
    thread_failures = 0
    thread_scroll_limits = 0
    skipped_threads: set[str] = set()
    stop_reason = "scroll_limit"
    idle = 0
    for _ in range(max_scrolls + 1):
        added = 0
        visible = await extract_visible(page, "channel")
        for message in visible:
            record = message_record(message, team, channel)
            if record:
                if record["ts"] not in found:
                    added += 1
                found[record["ts"]] = record
        for message in visible:
            parent_ts = message["ts"]
            if not message["has_replies"] or parent_ts in visited_threads:
                continue
            if len(visited_threads) >= max_threads or len(found) >= max_messages or out_of_time(deadline, clock):
                skipped_threads.add(parent_ts)
                continue
            visited_threads.add(parent_ts)
            if not await open_thread(page, parent_ts):
                thread_failures += 1
                continue
            replies, thread_stop = await collect_thread(page, team, channel, parent_ts,
                                                        max_thread_scrolls, wait_ms, deadline, clock)
            if thread_stop != "top_or_no_more_messages":
                thread_scroll_limits += 1
            for reply in replies:
                found[reply["ts"]] = reply
        if len(found) >= max_messages:
            stop_reason = "message_limit"
            break
        if out_of_time(deadline, clock):
            stop_reason = "time_budget"
            break
        movement = await scroll_older(page, "channel")
        if not movement["found"]:
            stop_reason = "scroll_pane_missing"
            break
        idle = idle + 1 if not added and movement["at_top"] else 0
        if idle >= 3:
            stop_reason = "top_or_no_more_messages"
            break
        await page.wait_for_timeout(wait_ms)
    if not found:
        raise RuntimeError("채널에서 시각과 본문이 있는 메시지를 찾지 못했습니다. 로그인과 Slack 화면 구조를 확인하세요.")
    ordered = sorted(found.values(), key=lambda msg: tuple(map(int, msg["ts"].split("."))), reverse=True)
    returned = ordered[:max_messages]
    return returned, {
        "stop_reason": stop_reason, "channel_messages": sum(not item["thread_ts"] for item in returned),
        "thread_replies": sum(bool(item["thread_ts"]) for item in returned),
        "threads_opened": len(visited_threads) - thread_failures,
        "thread_failures": thread_failures, "thread_scroll_limits": thread_scroll_limits,
        "threads_skipped": len(skipped_threads),
    }


async def collect_channel(url: str, profile: Path, max_messages: int = 500,
                          max_scrolls: int = 60, max_threads: int = 5,
                          max_thread_scrolls: int = 5, wait_ms: int = 650,
                          headed: bool = False) -> tuple[list[dict], dict]:
    url, team, channel = channel_address(url)
    profile.mkdir(parents=True, exist_ok=True, mode=0o700)
    profile.chmod(0o700)
    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(str(profile), headless=not headed)
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            try:
                await page.locator(MESSAGE_SELECTOR).first.wait_for(state="attached", timeout=20000)
            except PlaywrightTimeout as exc:
                raise RuntimeError("Slack 메시지가 표시되지 않았습니다. --config CONFIG_PATH --login SOURCE_ID로 로그인하고 채널 URL을 확인하세요.") from exc
            # A login redirect, another channel, or an access-denied screen must not be accepted.
            current = urlsplit(page.url)
            match = OPEN_CHANNEL_PATH.match(current.path)
            if current.hostname != "app.slack.com" or not match or match.groups() != (team, channel):
                raise RuntimeError("Slack opened a different channel; collection stopped")
            return await collect_from_page(page, team, channel, max_messages, max_scrolls,
                                           max_threads, max_thread_scrolls, wait_ms,
                                           deadline=time.monotonic() + READ_BUDGET_SECONDS)
        finally:
            await context.close()


async def login(profile: Path, workspace: str | None = None) -> None:
    url = "https://app.slack.com/client/"
    if workspace:
        url = f"https://{parse_target(workspace, '#general')['domain']}.slack.com/"
    profile.mkdir(parents=True, exist_ok=True, mode=0o700)
    profile.chmod(0o700)
    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(str(profile), headless=False)
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.to_thread(input, "브라우저에서 Slack에 직접 로그인한 뒤 Enter를 누르세요: ")
        finally:
            await context.close()


TEAM_ID = re.compile(r"[TE][A-Z0-9]{6,20}")
PUBLIC_CHANNEL_ID = re.compile(r"C[A-Z0-9]{6,20}")
CHANNEL_NAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,79}")
DOMAIN = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")
ARCHIVE_PATH = re.compile(r"^/archives/(C[A-Z0-9]{6,20})(?:/.*)?$")


def parse_target(workspace: str, channel: str) -> dict:
    """Turn user-supplied workspace/channel text into a bounded Slack web target.

    workspace: "community", "community.slack.com", a workspace URL, or a team ID (T...).
    channel: a public channel ID (C...), a channel link, or a channel name ("#general").
    """
    if not isinstance(workspace, str) or not isinstance(channel, str):
        raise ValueError("workspace and channel must be text")
    workspace, channel = workspace.strip(), channel.strip()
    target = {"team": "", "domain": "", "channel": "", "name": ""}
    if TEAM_ID.fullmatch(workspace):
        target["team"] = workspace
    else:
        host = urlsplit(workspace if "://" in workspace else "https://" + workspace).hostname or ""
        host = host.lower().removesuffix(".slack.com")
        if not DOMAIN.fullmatch(host) or host in ("app", "api", "www"):
            raise ValueError("workspace must be a Slack workspace domain like 'community' or 'community.slack.com', or a team ID T...")
        target["domain"] = host
    if channel.startswith("https://"):
        parts = urlsplit(channel)
        host = (parts.hostname or "").lower()
        client = OPEN_CHANNEL_PATH.match(parts.path) if host == "app.slack.com" else None
        archive = ARCHIVE_PATH.match(parts.path) if host.endswith(".slack.com") else None
        if client:
            team, target["channel"] = client.groups()
            if target["team"] and target["team"] != team:
                raise ValueError("Channel link belongs to a different workspace")
            target["team"] = team
        elif archive:
            if target["domain"] and host != f"{target['domain']}.slack.com":
                raise ValueError("Channel link belongs to a different workspace")
            target["domain"] = target["domain"] or host.removesuffix(".slack.com")
            target["channel"] = archive.group(1)
        else:
            raise ValueError("Channel link must look like https://<workspace>.slack.com/archives/C... or https://app.slack.com/client/T.../C...")
    elif PUBLIC_CHANNEL_ID.fullmatch(channel):
        target["channel"] = channel
    elif re.fullmatch(r"[GD][A-Z0-9]{6,20}", channel):
        raise ValueError("Only public channels (C...) can be read; private channels and DMs are not supported")
    elif CHANNEL_NAME.fullmatch(channel.lstrip("#").lower()):
        if not target["domain"]:
            raise ValueError("To use a channel name, give the workspace domain (e.g. 'community'); with a team ID give the channel ID C...")
        target["name"] = channel.lstrip("#").lower()
    else:
        raise ValueError("channel must be a public channel ID C..., a channel link, or a channel name like #general")
    if target["channel"] and not PUBLIC_CHANNEL_ID.fullmatch(target["channel"]):
        raise ValueError("Only public channels (C...) can be read")
    return target


def target_url(target: dict) -> str:
    if target["team"] and target["channel"]:
        return f"https://app.slack.com/client/{target['team']}/{target['channel']}"
    if target["domain"]:
        return f"https://{target['domain']}.slack.com/archives/{target['channel'] or target['name']}"
    raise ValueError("Give the workspace domain or a channel link")


def is_signin(url: str) -> bool:
    parts = urlsplit(url)
    return (parts.hostname or "").endswith("slack.com") and ("signin" in parts.path or parts.path.startswith("/ssb/"))


def desktop_deeplink(url: str) -> tuple[str, str] | None:
    """Slack's /archives page hands off to slack://channel?team=T...&id=C...; read the IDs from it."""
    parts = urlsplit(url)
    if parts.scheme != "slack" or parts.netloc != "channel":
        return None
    query = parse_qs(parts.query)
    team, channel = query.get("team", [""])[0], query.get("id", [""])[0]
    if TEAM_ID.fullmatch(team) and re.fullmatch(r"[CGD][A-Z0-9]{6,20}", channel):
        return team, channel
    return None


async def open_target(page, target: dict, timeout_ms: int = 20000) -> tuple[str, str]:
    """Navigate to the requested channel and confirm the web client opened exactly that channel."""
    handoff: list[tuple[str, str]] = []

    def on_request(request) -> None:
        found = desktop_deeplink(request.url)
        if found:
            handoff.append(found)

    def settled(url: str) -> bool:
        parts = urlsplit(url)
        return (parts.hostname == "app.slack.com" and bool(OPEN_CHANNEL_PATH.match(parts.path))) or is_signin(url)

    async def wait(ms: int, until_handoff: bool = False) -> bool:
        for _ in range(max(1, ms // 250)):
            if settled(page.url) or (until_handoff and handoff):
                return True
            await asyncio.sleep(0.25)
        return settled(page.url)

    async def go(url: str) -> None:
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except PlaywrightError as exc:
            # The slack:// desktop hand-off is aborted by Chrome; the web flow continues afterwards.
            if "ERR_ABORTED" not in str(exc):
                raise

    page.on("request", on_request)
    await go(target_url(target))
    if not await wait(min(10000, timeout_ms), until_handoff=True) or (handoff and not settled(page.url)):
        if handoff:
            team, channel = handoff[-1]
            await go(f"https://app.slack.com/client/{team}/{channel}")
        else:
            # Some pages show an "open in app / use the browser" choice; follow only its web-client link.
            link = page.locator('a[href*="app.slack.com/client/"]').first
            if await link.count():
                href = await link.get_attribute("href") or ""
                if urlsplit(href).scheme == "https" and urlsplit(href).hostname == "app.slack.com":
                    await go(href)
        await wait(timeout_ms)
    current = urlsplit(page.url)
    if is_signin(page.url):
        hint = target["domain"] or "WORKSPACE"
        raise PermissionError(f"Slack 로그인이 필요합니다. 터미널에서 --slack-login {hint}로 직접 로그인하세요.")
    match = OPEN_CHANNEL_PATH.match(current.path) if current.hostname == "app.slack.com" else None
    if not match:
        raise RuntimeError("Slack 채널 화면을 열지 못했습니다. 워크스페이스·채널을 확인하거나 채널 ID(C...)나 채널 링크를 넣어 보세요.")
    team, channel = match.groups()
    if (target["team"] and team != target["team"]) or (target["channel"] and channel != target["channel"]):
        raise RuntimeError("Slack opened a different workspace or channel; collection stopped")
    if not PUBLIC_CHANNEL_ID.fullmatch(channel):
        raise RuntimeError("Only public channels (C...) can be read")
    return team, channel


def signin_url(workspace: str | None) -> str:
    if not workspace:
        return "https://app.slack.com/client/"
    return f"https://{parse_target(workspace, '#general')['domain']}.slack.com/"


READ_BUDGET_SECONDS = 95
MAX_TARGET_SCROLLS = 2000


async def read_target(workspace: str, channel: str, profile: Path, max_messages: int = 200,
                      max_threads: int = 5) -> tuple[list[dict], dict]:
    """Open a user-chosen public channel in a new tab of Chaejip's Chrome, read it, close the tab."""
    from . import chrome

    target = parse_target(workspace, channel)
    if not profile.is_dir():
        raise PermissionError("Chaejip용 Chrome 프로필이 없습니다. 터미널에서 --slack-login WORKSPACE로 먼저 로그인하세요.")
    port = chrome.chrome_port()
    # collect() stops at 120 s; leave room to close the tab, stop Chrome and save.
    deadline = time.monotonic() + READ_BUDGET_SECONDS
    started = await asyncio.to_thread(chrome.ensure_running, profile, port)
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.connect_over_cdp(chrome.endpoint(port))
            if not browser.contexts:
                raise RuntimeError("Chrome 연결에 브라우저 프로필이 없습니다")
            page = await browser.contexts[0].new_page()
            try:
                team, channel_id = await open_target(page, target)
                try:
                    await page.locator(MESSAGE_SELECTOR).first.wait_for(state="attached", timeout=20000)
                except PlaywrightTimeout as exc:
                    raise RuntimeError("채널은 열렸지만 메시지가 표시되지 않았습니다. Slack 화면 구조를 확인하세요.") from exc
                label = (await page.title()).split(" - ")[0].strip()[:200]
                messages, details = await collect_from_page(
                    page, team, channel_id, max_messages=max_messages,
                    max_scrolls=MAX_TARGET_SCROLLS, max_threads=max_threads, deadline=deadline)
                return messages, details | {"team": team, "channel": channel_id, "channel_label": label}
            finally:
                await page.close()
    finally:
        await asyncio.to_thread(chrome.stop, started)
