"""Collect channel messages and visible thread replies in an authenticated Slack tab."""

import asyncio
import re
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout


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


async def collect_thread(page, team: str, channel: str, parent_ts: str,
                         max_scrolls: int, wait_ms: int) -> tuple[list[dict], str]:
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
                            max_thread_scrolls: int = 5, wait_ms: int = 650) -> tuple[list[dict], dict]:
    """Walk the channel timeline and inspect threads while parents are visible."""
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
            if len(visited_threads) >= max_threads or len(found) >= max_messages:
                skipped_threads.add(parent_ts)
                continue
            visited_threads.add(parent_ts)
            if not await open_thread(page, parent_ts):
                thread_failures += 1
                continue
            replies, thread_stop = await collect_thread(page, team, channel, parent_ts,
                                                        max_thread_scrolls, wait_ms)
            if thread_stop != "top_or_no_more_messages":
                thread_scroll_limits += 1
            for reply in replies:
                found[reply["ts"]] = reply
        if len(found) >= max_messages:
            stop_reason = "message_limit"
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
                                           max_threads, max_thread_scrolls, wait_ms)
        finally:
            await context.close()


async def login(profile: Path) -> None:
    profile.mkdir(parents=True, exist_ok=True, mode=0o700)
    profile.chmod(0o700)
    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(str(profile), headless=False)
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto("https://app.slack.com/client/", wait_until="domcontentloaded", timeout=30000)
            await asyncio.to_thread(input, "브라우저에서 Slack에 직접 로그인한 뒤 Enter를 누르세요: ")
        finally:
            await context.close()
