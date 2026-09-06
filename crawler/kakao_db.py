"""Read only the selected open-chat room from the current Mac's local database.

Key derivation adapted from NomaDamas/katok (MIT), see THIRD_PARTY_NOTICES.md.
No remote requests, UI, OCR, key persistence, or account-id brute force.
"""

import base64
import hashlib
import json
import platform
import plistlib
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def sqlcipher_path():
    path = shutil.which('sqlcipher')
    if not path:
        path = next((p for p in ('/opt/homebrew/bin/sqlcipher', '/usr/local/bin/sqlcipher') if Path(p).is_file()), None)
    if not path:
        raise RuntimeError('Install SQLCipher with brew install sqlcipher')
    return path


def database_status(source):
    if platform.system() != 'Darwin':
        return {'status': 'unsupported_os', 'message': 'Local Kakao database collection requires macOS'}
    try:
        sqlcipher_path()
        if source.get('account_file') and not Path(source['account_file']).is_file():
            return {'status': 'configuration_required', 'message': 'Configured local account metadata file is missing'}
    except RuntimeError as exc:
        return {'status': 'dependency_missing', 'message': str(exc)}
    return {'status': 'configured', 'message': 'Original local history configured; collect verifies account, room and database access'}


def derive_key(user_id, uuid):
    hashed = base64.b64encode(hashlib.sha1(uuid.encode()).digest() + hashlib.sha256(uuid.encode()).digest()).decode()
    password = 'F'.join(['A', hashed, '|', 'F', uuid[:5], 'H', str(user_id), '|', uuid[7:]])[::-1]
    return hashlib.pbkdf2_hmac('sha256', password.encode(), uuid[int(len(uuid) * .3):].encode(), 100000, 128).hex()


def query_database(path, key, sql, timeout=20):
    # Feed key through stdin, never argv, logs or an on-disk SQL file.
    script = f".bail on\n.output /dev/null\nPRAGMA key='{key}';\nPRAGMA cipher_compatibility=3;\nPRAGMA query_only=ON;\n.output stdout\n.mode json\nBEGIN;\n{sql};\nROLLBACK;\n"
    result = subprocess.run([sqlcipher_path(), '-readonly', str(path)], input=script, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        # SQLCipher diagnostics can echo SQL; do not expose them to MCP callers.
        raise RuntimeError('Local Kakao database query failed (key, permission, schema or lock)')
    return json.loads(result.stdout) if result.stdout.strip() else []


def local_metadata(account_file=None):
    if platform.system() != 'Darwin':
        raise RuntimeError('Local Kakao database collection requires macOS')
    root = Path.home() / 'Library/Containers/com.kakao.KakaoTalkMac/Data/Library'
    try:
        databases = sorted(p for p in (root / 'Application Support/com.kakao.KakaoTalkMac').iterdir() if re.fullmatch(r'[0-9a-f]{78}(?:\.db)?', p.name))
        candidates = set()
        active_hashes = set()
        if account_file:
            account = json.loads(Path(account_file).read_text())
            if type(account.get("user_id")) is not int or account["user_id"] <= 0:
                raise ValueError("Invalid local account metadata file")
            candidates.add(account["user_id"])
        for path in (root / 'Preferences').glob('com.kakao.KakaoTalkMac*.plist'):
            data = plistlib.loads(path.read_bytes())
            active_hashes.update(k.split(":", 1)[1] for k, v in data.items() if k.startswith("DESIGNATEDFRIENDSREVISION:") and v)
            values = [data.get(k) for k in ('userId', 'user_id', 'KAKAO_USER_ID', 'userID')]
            alerts = data.get('AlertKakaoIDsList', [])
            if isinstance(alerts, list):
                values.extend(alerts)
            for value in values:
                if isinstance(value, (str, int)) and str(value).isdigit() and int(value) > 0:
                    candidates.add(int(value))
    except PermissionError:
        raise PermissionError('Allow the launching AI app/terminal access to Kakao app data or Full Disk Access') from None
    if active_hashes:
        candidates = {uid for uid in candidates if hashlib.sha512(str(uid).encode()).hexdigest() in active_hashes}
    if not databases or len(databases) > 10 or len(candidates) > 20:
        raise RuntimeError('Could not resolve a bounded set of local Kakao database/account candidates')
    return databases, candidates, active_hashes


def device_uuid():
    result = subprocess.run(['/usr/sbin/ioreg', '-rd1', '-c', 'IOPlatformExpertDevice'], capture_output=True, text=True, timeout=5)
    match = re.search(r'"IOPlatformUUID" = "([A-Fa-f0-9-]{36})"', result.stdout)
    if not match:
        raise RuntimeError('Mac device identifier unavailable')
    return match.group(1)


def resolve_database(account_file=None):
    databases, candidates, _ = local_metadata(account_file)
    if not candidates:
        raise RuntimeError("Account ID unavailable; run --setup-kakao SOURCE_ID first")
    uuid = device_uuid()
    attempts = 0
    for user_id in sorted(candidates):
        key = derive_key(user_id, uuid)
        for database in databases:
            attempts += 1
            if attempts > 8:
                raise RuntimeError("Local account/database probe limit exceeded")
            try:
                query_database(database, key, 'SELECT count(*) AS n FROM sqlite_master', timeout=3)
                return database, key
            except RuntimeError:
                continue
    raise RuntimeError('Local Kakao database could not be opened with available account metadata')


def selected_room(path, key, title):
    literal = "CAST(X'" + title.encode().hex() + "' AS TEXT)"
    rows = query_database(path, key, f'''SELECT DISTINCT r.chatId FROM NTChatRoom r
        JOIN NTOpenLink l ON l.linkId=r.linkId WHERE l.linkName={literal}''')
    if len(rows) != 1:
        raise ValueError('Expected exactly one local open-chat room matching room_title')
    return int(rows[0]['chatId'])


def collect_database(source):
    title = source.get('room_title', '').strip()
    if not title:
        raise ValueError('Set room_title to the exact selected open-chat name')
    path, key = resolve_database(source.get('account_file'))
    room = selected_room(path, key, title)
    condition = f"chatId={room} AND message IS NOT NULL AND message<>''"
    size = query_database(path, key, f'SELECT count(*) AS n, coalesce(sum(length(CAST(message AS BLOB))),0) AS bytes FROM NTChatMessage WHERE {condition}')[0]
    if size['n'] > 50000 or size['bytes'] > 25000000:
        raise RuntimeError('Selected local history exceeds 50,000 messages / 25 MB; no partial history stored')
    rows = query_database(path, key, f'''SELECT logId, authorId, sentAt, message,
        (SELECT coalesce(nullif(u.friendNickName,''),nullif(u.displayName,''),nullif(u.nickName,''))
         FROM NTUser u WHERE u.userId=m.authorId LIMIT 1) AS author
        FROM NTChatMessage m WHERE {condition} ORDER BY sentAt,logId LIMIT 50001''')
    if len(rows) > 50000 or sum(len(r['message'].encode()) for r in rows) > 25000000:
        raise RuntimeError('Local history grew past capture limit; nothing stored')
    items = []
    for row in rows:
        sent = datetime.fromtimestamp(row['sentAt'], timezone.utc).isoformat()
        items.append(dict(key=f"{room}:{int(row['logId'])}", title=f'{title} · {sent}',
                          body=row['message'], author=row['author'] or f"user-{row['authorId']}", url=''))
    if not items:
        raise RuntimeError('No local text messages found for the selected room')
    return items
