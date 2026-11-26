"""Run the user's installed Google Chrome with a dedicated Chaejip profile and attach over CDP.

Chrome is started as a normal browser (no automation flags), so sites such as Google sign-in
treat it like any other Chrome window. The DevTools port listens on 127.0.0.1 only, and the
profile is separate from the user's everyday Chrome profile.
"""

import json
import os
import platform
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

DEFAULT_PORT = 9333
MAC_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def chrome_path() -> str:
    configured = os.environ.get("CHAEJIP_CHROME")
    if configured:
        candidates = [configured]
    elif platform.system() == "Darwin":
        candidates = [MAC_CHROME, str(Path.home() / MAC_CHROME.lstrip("/"))]
    else:
        candidates = [shutil.which(name) for name in ("google-chrome", "google-chrome-stable")]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise RuntimeError("Google Chrome을 찾지 못했습니다. 설치하거나 CHAEJIP_CHROME에 실행 파일 경로를 지정하세요.")


def chrome_port() -> int:
    raw = os.environ.get("CHAEJIP_CHROME_PORT", str(DEFAULT_PORT))
    if not raw.isdigit() or not 1024 <= int(raw) <= 65535:
        raise ValueError("CHAEJIP_CHROME_PORT must be 1024..65535")
    return int(raw)


def endpoint(port: int) -> str:
    return f"http://127.0.0.1:{port}"


def is_running(port: int) -> bool:
    try:
        with urlopen(endpoint(port) + "/json/version", timeout=1) as response:
            return "Browser" in json.loads(response.read().decode())
    except (OSError, ValueError):
        return False


def wait_ready(port: int, timeout: float = 20, clock=time.monotonic, sleep=time.sleep) -> bool:
    deadline = clock() + timeout
    while clock() < deadline:
        if is_running(port):
            return True
        sleep(0.25)
    return is_running(port)


def launch(profile: Path, port: int, url: str | None = None, extra_args: tuple[str, ...] = ()) -> subprocess.Popen:
    profile = profile.resolve()
    profile.mkdir(parents=True, exist_ok=True, mode=0o700)
    profile.chmod(0o700)
    args = [chrome_path(), f"--user-data-dir={profile}", f"--remote-debugging-port={port}",
            "--no-first-run", "--no-default-browser-check", *extra_args]
    if url:
        args.append(url)
    return subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, start_new_session=True)


def open_tab(port: int, url: str) -> None:
    request = Request(endpoint(port) + "/json/new?" + quote(url, safe=":/?=&%"), method="PUT")
    with urlopen(request, timeout=5):
        pass


def ensure_running(profile: Path, port: int) -> subprocess.Popen | None:
    """Start Chaejip's Chrome if it is not already listening. Returns the process only if we started it."""
    if is_running(port):
        return None
    process = launch(profile, port)
    if not wait_ready(port):
        process.terminate()
        raise RuntimeError("Chaejip용 Chrome에 연결하지 못했습니다. 같은 프로필로 열린 Chrome 창이 있다면 모두 닫고 다시 시도하세요.")
    return process


def stop(process: subprocess.Popen | None) -> None:
    if process is None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()


def login(profile: Path, port: int, url: str) -> str:
    """Show the sign-in page in Chaejip's Chrome; the user signs in by hand."""
    if is_running(port):
        open_tab(port, url)
        return "이미 열린 Chaejip용 Chrome에 새 탭을 열었습니다. 그 탭에서 직접 로그인하세요."
    launch(profile, port, url)
    if not wait_ready(port):
        raise RuntimeError("Chrome을 띄웠지만 연결 포트가 열리지 않았습니다. 같은 프로필로 열린 Chrome 창을 모두 닫고 다시 실행하세요.")
    return "Chaejip용 Chrome 창을 열었습니다. 그 창에서 직접 로그인하세요. 로그인 후 창을 닫아도 세션은 유지됩니다."
