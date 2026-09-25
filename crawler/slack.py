"""Read only messages rendered in a user-authenticated Slack browser tab."""

import asyncio
import re
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout


CHANNEL_PATH = re.compile(r"^/client/(T[A-Z0-9]+|E[A-Z0-9]+)/(C[A-Z0-9]+)(?:/)?$")
OPEN_CHANNEL_PATH = re.compile(r"^/client/(T[A-Z0-9]+|E[A-Z0-9]+)/(C[A-Z0-9]+)(?:/|$)")
MESSAGE_SELECTOR = '[data-qa="message_container"]'


def channel_address(value: str) -> tuple[str, str, str]:
    parts = urlsplit(value)
    match = CHANNEL_PATH.fullmatch(parts.path)
    if parts.scheme != "https" or parts.hostname != "app.slack.com" or not match or parts.username or parts.password:
        raise ValueError("Use a public-channel URL like https://app.slack.com/client/T.../C...")
    team, channel = match.groups()
    return f"https://app.slack.com/client/{team}/{channel}", team, channel


async def extract_visible(page) -> list[dict]:
    """Read text and metadata from DOM; never inspect Slack network traffic or credentials."""
    return await page.locator(MESSAGE_SELECTOR).evaluate_all("""nodes => nodes.map(node => {
        const stamp = node.querySelector('a[data-ts]');
        const textNodes = [...node.querySelectorAll('[data-qa="message-text"]')];
        const body = textNodes.map(n => n.innerText.trim()).filter(Boolean).join('\\n').trim();
        const author = node.querySelector('[data-qa="message_sender_name"]')?.innerText.trim() || '';
        return {ts: stamp?.getAttribute('data-ts') || '', body, author,
                permalink: stamp?.getAttribute('href') || ''};
    })""")


async def scroll_older(page) -> dict:
    """Scroll the message pane by less than a screen to overlap virtualized rows."""
    return await page.locator(MESSAGE_SELECTOR).first.evaluate("""node => {
        let pane = node.parentElement;
        while (pane) {
            const style = getComputedStyle(pane);
            if (/(auto|scroll)/.test(style.overflowY) && pane.scrollHeight > pane.clientHeight + 20) break;
            pane = pane.parentElement;
        }
        pane ||= document.querySelector('.p-message_pane .c-scrollbar__hider');
        if (!pane) return {found: false, at_top: false, moved: false};
        const before = pane.scrollTop;
        pane.scrollTop = Math.max(0, before - Math.max(100, pane.clientHeight * 0.65));
        return {found: true, at_top: pane.scrollTop <= 1, moved: pane.scrollTop < before};
    }""")


async def collect_channel(url: str, profile: Path, max_messages: int = 500,
                          max_scrolls: int = 60, wait_ms: int = 650,
                          headed: bool = False) -> tuple[list[dict], dict]:
    url, team, channel = channel_address(url)
    profile.mkdir(parents=True, exist_ok=True, mode=0o700)
    profile.chmod(0o700)
    found: dict[str, dict] = {}
    stop_reason = "scroll_limit"
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
            idle = 0
            for _ in range(max_scrolls + 1):
                added = 0
                for message in await extract_visible(page):
                    ts = message["ts"]
                    body = message["body"]
                    if not re.fullmatch(r"\d{9,12}\.\d{1,9}", ts) or not body:
                        continue
                    if len(body) > 50000:
                        body = body[:50000]
                    key = f"{team}:{channel}:{ts}"
                    if key not in found:
                        added += 1
                    found[key] = {"team": team, "channel": channel, "ts": ts,
                                  "body": body, "author": message["author"][:300],
                                  "permalink": message["permalink"]}
                if len(found) >= max_messages:
                    stop_reason = "message_limit"
                    break
                movement = await scroll_older(page)
                if not movement["found"]:
                    stop_reason = "scroll_pane_missing"
                    break
                idle = idle + 1 if not added and movement["at_top"] else 0
                if idle >= 3:
                    stop_reason = "top_or_no_more_messages"
                    break
                await page.wait_for_timeout(wait_ms)
        finally:
            await context.close()
    if not found:
        raise RuntimeError("The channel opened, but no text messages with timestamps were found. Slack's layout may have changed.")
    # A virtual list can render messages in either direction; keep the newest first.
    ordered = sorted(found.values(), key=lambda message: tuple(map(int, message["ts"].split("."))), reverse=True)
    return ordered[:max_messages], {"stop_reason": stop_reason, "visible_messages": len(found)}


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
