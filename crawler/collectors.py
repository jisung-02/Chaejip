"""Fixed-selector collectors. No LLM calls and no model-supplied code."""

import json
import re
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlsplit

from playwright.async_api import async_playwright, TimeoutError as BrowserTimeout


def web_url(value: str) -> str:
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password or len(value) > 4096:
        raise ValueError("Expected an HTTP(S) URL without embedded credentials (max 4096 characters)")
    return urldefrag(value)[0]


def load_sources(path: Path) -> list[dict]:
    sources = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(sources, list):
        raise ValueError("Sources file must contain a JSON array")
    seen = set()
    for s in sources:
        if not isinstance(s, dict) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", s.get("id", "")):
            raise ValueError("Source id must be 1..64 letters, digits, underscores or hyphens")
        if s["id"] in seen:
            raise ValueError(f"Duplicate source id: {s['id']}")
        seen.add(s["id"])
        if not isinstance(s.get("enabled", True), bool):
            raise ValueError("enabled must be a boolean")
        if s.get("kind") not in ("web", "kakao_window", "kakao_db", "text_file"):
            raise ValueError(f"Unknown source kind: {s.get('kind')}")
        if s["kind"] == "web":
            web_url(s.get("url", ""))
            for key in ("item_selector", "title_selector", "link_selector"):
                if not isinstance(s.get(key), str) or not s[key].strip():
                    raise ValueError(f"Missing selector: {key}")
            for key, default, maximum in (("max_pages", 1, 5), ("max_items", 20, 100), ("timeout_ms", 15000, 30000)):
                value = s.get(key, default)
                if type(value) is not int or not 1 <= value <= maximum:
                    raise ValueError(f"{key} must be 1..{maximum}")
            for key in ("body_selector", "author_selector", "detail_selector", "next_selector"):
                if key in s and (not isinstance(s[key], str) or not s[key].strip()):
                    raise ValueError(f"Invalid selector: {key}")
        elif s["kind"] == "kakao_db":
            if not isinstance(s.get("room_title"), str) or not s["room_title"].strip():
                raise ValueError("kakao_db requires the exact room_title")
            if "account_file" in s and (not isinstance(s["account_file"], str) or not Path(s["account_file"]).is_absolute()):
                raise ValueError("account_file requires an absolute path")
        elif s["kind"] == "text_file":
            if not isinstance(s.get("path"), str) or not Path(s["path"]).expanduser().is_absolute():
                raise ValueError("text_file requires an absolute path")
        elif not isinstance(s.get("window_title", ""), str):
            raise ValueError("window_title must be a string")
        elif s.get("capture_method", "accessibility") != "accessibility":
            raise ValueError("OCR is not supported; use kakao_db for original history")
    return sources


def collect_text_file(source: dict) -> list[dict]:
    path = Path(source["path"]).expanduser().resolve()
    if path.stat().st_size > 5_000_000:
        raise ValueError("Text export exceeds 5 MB; split it before collecting")
    body = path.read_text(encoding="utf-8-sig").strip()
    if not body:
        raise ValueError("Text export is empty")
    return [dict(key=str(path), title=source.get("name", path.stem), body=body,
                 url=path.as_uri(), author="")]


async def collect_web(source: dict, data_dir: Path) -> list[dict]:
    profile = data_dir / "profiles" / source["id"]
    profile.mkdir(parents=True, exist_ok=True, mode=0o700)
    profile.chmod(0o700)
    max_items = source.get("max_items", 20)
    timeout = source.get("timeout_ms", 15000)
    items, keys, visited = [], set(), set()
    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(str(profile), headless=True)
        try:
            context.set_default_timeout(timeout)
            page = context.pages[0] if context.pages else await context.new_page()
            detail = await context.new_page() if source.get("detail_selector") else None
            url = web_url(source["url"])
            origin = urlsplit(url)[:2]
            for _ in range(source.get("max_pages", 1)):
                if url in visited:
                    break
                visited.add(url)
                response = await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
                if response and response.status >= 400:
                    raise RuntimeError(f"HTTP {response.status}; collection stopped")
                if urlsplit(page.url)[:2] != origin:
                    raise RuntimeError("Unexpected cross-origin redirect; check login or configured URL")
                try:
                    await page.locator(source["item_selector"]).first.wait_for(state="attached", timeout=timeout)
                except BrowserTimeout as exc:
                    raise RuntimeError("Item selector not found; check login, challenge or changed page layout") from exc
                rows = page.locator(source["item_selector"])
                for n in range(min(await rows.count(), max_items)):
                    row = rows.nth(n)
                    title = (await row.locator(source["title_selector"]).first.inner_text()).strip()
                    href = await row.locator(source["link_selector"]).first.get_attribute("href")
                    if not title or not href:
                        raise RuntimeError("Title/link selector yielded empty content")
                    item_url = web_url(urljoin(page.url, href))
                    if item_url in keys:
                        continue
                    author = ""
                    if source.get("author_selector"):
                        author = (await row.locator(source["author_selector"]).first.inner_text()).strip()
                    body = (await row.locator(source["body_selector"]).first.inner_text()).strip() if source.get("body_selector") else (await row.inner_text()).strip()
                    if detail:
                        if urlsplit(item_url)[:2] != origin:
                            raise RuntimeError("Detail URL must stay on the configured origin")
                        result = await detail.goto(item_url, wait_until="domcontentloaded", timeout=timeout)
                        if result and result.status >= 400:
                            raise RuntimeError(f"Detail HTTP {result.status}; collection stopped")
                        if urlsplit(detail.url)[:2] != origin:
                            raise RuntimeError("Unexpected cross-origin detail redirect")
                        body = (await detail.locator(source["detail_selector"]).first.inner_text()).strip()
                    if not body:
                        raise RuntimeError("Body selector yielded empty content")
                    if len(body) > 500_000:
                        raise RuntimeError("Item body exceeds 500,000 characters; narrow the selector")
                    items.append(dict(key=item_url, title=title[:1000], url=item_url, author=author[:300], body=body))
                    keys.add(item_url)
                    if len(items) >= max_items:
                        break
                if len(items) >= max_items or not source.get("next_selector"):
                    break
                next_link = page.locator(source["next_selector"]).first
                if not await next_link.count():
                    break
                href = await next_link.get_attribute("href")
                if not href:
                    raise RuntimeError("Pagination requires a link with href; no arbitrary button clicks")
                url = web_url(urljoin(page.url, href))
                if urlsplit(url)[:2] != origin:
                    raise RuntimeError("Pagination must stay on the configured origin")
        finally:
            await context.close()
    return items
