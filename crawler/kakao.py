"""Read an explicitly named Kakao window with macOS Accessibility."""

import hashlib
import json
import platform
import subprocess
from pathlib import Path


def app_path() -> str | None:
    return next((str(p) for p in (Path("/Applications/KakaoTalk.app"), Path.home() / "Applications/KakaoTalk.app") if p.exists()), None)


def run_script(script: str, *args: str, javascript: bool = False) -> str:
    command = ["osascript"] + (["-l", "JavaScript"] if javascript else []) + ["-e", script, *args]
    result = subprocess.run(command, capture_output=True, text=True, timeout=15)
    if result.returncode:
        raise RuntimeError(result.stderr.strip()[:500])
    return result.stdout.strip()


def kakao_status(capture_method: str = "accessibility") -> dict:
    if platform.system() != "Darwin":
        return {"status": "unsupported_os", "message": "Live Kakao capture currently supports macOS only"}
    if not app_path():
        return {"status": "not_installed", "message": "KakaoTalk.app was not found in Applications"}
    try:
        flags = run_script('tell application "System Events"\nreturn {UI elements enabled, exists process "KakaoTalk"}\nend tell')
        accessible, running = [v.strip() == "true" for v in flags.split(",")]
    except (RuntimeError, subprocess.TimeoutExpired, ValueError) as exc:
        return {"status": "permission_required", "message": f"Allow Accessibility/Automation for the launching app: {str(exc)[:250]}"}
    if not accessible:
        return {"status": "permission_required", "message": "Allow the launching AI app or terminal in System Settings > Privacy & Security > Accessibility; allow System Events under Automation"}
    if not running:
        return {"status": "not_running", "message": "Open KakaoTalk, sign in and open the configured chat in its own window"}
    return {"status": "available", "message": "App and Accessibility available; chat-window text support must still be checked"}


# argv carries the window name as data; it is never interpolated into script code.
WINDOW_SCRIPT = r'''
function run(argv) {
    const process = Application('System Events').processes.byName('KakaoTalk');
    const windows = process.windows.whose({name: argv[0]})();
    if (windows.length !== 1) throw new Error('Expected exactly one open window with the configured title');
    const lines = [];
    let visited = 0, characters = 0;
    function walk(element, depth, inTable) {
        if (++visited > 5000 || depth > 40) throw new Error('Accessibility tree exceeds capture limit');
        let role = '';
        try { role = element.role(); } catch (_) { return; }
        inTable = inTable || role === 'AXTable';
        // Message bodies and the composer are both AXTextArea; only read the table.
        if (inTable && (role === 'AXStaticText' || role === 'AXTextArea')) {
            let value = '';
            try { value = element.value(); } catch (_) {}
            if (typeof value === 'string' && value.trim()) {
                const text = value.trim();
                characters += text.length;
                if (characters > 500000) throw new Error('Window text exceeds capture limit');
                lines.push(text);
            }
        }
        let children = [];
        try { children = element.uiElements(); } catch (_) {}
        for (const child of children) walk(child, depth + 1, inTable);
    }
    walk(windows[0], 0, false);
    return JSON.stringify({body: lines.join('\n')});
}
'''


def collect_window(source: dict) -> list[dict]:
    title = source.get("window_title", "").strip()
    if not title:
        raise ValueError("Set window_title to the exact title of an already open chat window")
    method = source.get("capture_method", "accessibility")
    if method != "accessibility":
        raise ValueError("Only legacy accessibility snapshots are supported here; use kakao_db for original history")
    status = kakao_status(method)
    if status["status"] == "permission_required":
        raise PermissionError(status["message"])
    if status["status"] != "available":
        raise RuntimeError(status["message"])
    body = json.loads(run_script(WINDOW_SCRIPT, title, javascript=True))["body"].strip()
    if not body or body == title:
        raise RuntimeError("Chat text is not exposed through Accessibility; use a text_file source with an exported conversation")
    digest = hashlib.sha256(body.encode()).hexdigest()
    return [dict(key=digest, title=f"{title} ({method} window snapshot)", body=body, url="", author="")]
